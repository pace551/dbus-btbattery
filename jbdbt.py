import asyncio
import threading
from bleak import BleakClient, BleakError
from threading import Lock
from battery import Protection, Battery, Cell
from utils import *
from struct import *
import time
import binascii

# JBD BMS standard GATT UUIDs
BLE_TX_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"  # Write commands
BLE_RX_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"  # Receive notifications

# JBD BMS command bytes
CMD_GENERAL_INFO = b'\xdd\xa5\x03\x00\xff\xfd\x77'
CMD_CELL_VOLTAGES = b'\xdd\xa5\x04\x00\xff\xfc\x77'

# Seconds to wait for each notification response before giving up
READ_TIMEOUT = 10.0


class JbdProtection(Protection):
	def __init__(self):
		Protection.__init__(self)
		self.voltage_high_cell = False
		self.voltage_low_cell = False
		self.short = False
		self.IC_inspection = False
		self.software_lock = False

	def set_voltage_high_cell(self, value):
		self.voltage_high_cell = value
		self.cell_imbalance = (
			2 if self.voltage_low_cell or self.voltage_high_cell else 0
		)

	def set_voltage_low_cell(self, value):
		self.voltage_low_cell = value
		self.cell_imbalance = (
			2 if self.voltage_low_cell or self.voltage_high_cell else 0
		)

	def set_short(self, value):
		self.short = value
		self.internal_failure = (
			2 if self.short or self.IC_inspection or self.software_lock else 0
		)

	def set_ic_inspection(self, value):
		self.IC_inspection = value
		self.internal_failure = (
			2 if self.short or self.IC_inspection or self.software_lock else 0
		)

	def set_software_lock(self, value):
		self.software_lock = value
		self.internal_failure = (
			2 if self.short or self.IC_inspection or self.software_lock else 0
		)


# Shared event loop for all BleakJbdDev instances.
# bleak's dbus-fast backend binds internal state to the first loop it sees,
# so all BLE coroutines must run on the same loop.
_ble_loop: asyncio.AbstractEventLoop | None = None
_ble_loop_lock = threading.Lock()

# Serializes BLE connect operations: BlueZ only allows one active
# discovery at a time, so concurrent connect() calls fail with InProgress.
# Released once connected; reads run concurrently without the lock.
_ble_connect_lock: asyncio.Lock | None = None


def _get_ble_loop() -> asyncio.AbstractEventLoop:
	global _ble_loop, _ble_connect_lock
	with _ble_loop_lock:
		if _ble_loop is None or not _ble_loop.is_running():
			_ble_loop = asyncio.new_event_loop()
			_ble_connect_lock = asyncio.Lock()
			t = threading.Thread(target=_ble_loop.run_forever, daemon=True)
			t.start()
		return _ble_loop


class BleakJbdDev:
	def __init__(self, address):
		self.cellDataCallback = None
		self.cellData = None
		self.cellDataTotalLen = 0
		self.cellDataRemainingLen = 0
		self.last_state = "0000"
		self._last_state_change_time = time.monotonic()

		self.generalDataCallback = None
		self.generalData = None
		self.generalDataTotalLen = 0
		self.generalDataRemainingLen = 0

		self.address = address
		self.interval = BT_POLL_INTERVAL
		self.initial_delay = 0
		self.running = False
		self.last_read_time: float = 0.0  # monotonic; 0 = never successfully read

		# Set at the start of each read cycle; fired by the notification handler
		# when a complete general/cell packet has been assembled and delivered.
		self._general_event: asyncio.Event | None = None
		self._cell_event: asyncio.Event | None = None

	def reset(self):
		"""Reset the notification state machine buffers for a fresh read cycle."""
		self.last_state = "0000"
		self.cellData = None
		self.generalData = None
		self.cellDataTotalLen = 0
		self.cellDataRemainingLen = 0
		self.generalDataTotalLen = 0
		self.generalDataRemainingLen = 0
		self._last_state_change_time = time.monotonic()

	def connect(self):
		self.running = True
		future = asyncio.run_coroutine_threadsafe(self._ble_main_loop(), _get_ble_loop())

		def _on_ble_done(f):
			if not f.cancelled() and f.exception():
				logger.error("BLE loop for %s crashed: %s", self.address, f.exception())

		future.add_done_callback(_on_ble_done)

	async def _ble_main_loop(self):
		if self.initial_delay > 0:
			await asyncio.sleep(self.initial_delay)
		while self.running:
			success = False
			client = BleakClient(self.address)
			logger.info('Connecting ' + self.address)
			try:
				# Hold the lock for the entire connect-read-disconnect cycle so
				# only one battery uses the BLE radio at a time. Concurrent GATT
				# sessions cause connection handshakes to drown out notifications,
				# producing read timeouts on the other batteries.
				async with _ble_connect_lock:
					try:
						await client.connect(timeout=BT_CONNECT_TIMEOUT)
						logger.info('Connected ' + self.address)

						# Fresh events and clean state machine for this read cycle.
						# JbdBt's generalData/cellData (set by callbacks) are not cleared
						# here — the dbus poller continues to see the previous read's data
						# until the new callbacks fire.
						self._general_event = asyncio.Event()
						self._cell_event = asyncio.Event()
						self.reset()

						await client.start_notify(BLE_RX_UUID, self._notification_handler)

						await client.write_gatt_char(BLE_TX_UUID, CMD_GENERAL_INFO, response=True)
						await asyncio.wait_for(self._general_event.wait(), timeout=READ_TIMEOUT)

						await asyncio.sleep(0.5)

						await client.write_gatt_char(BLE_TX_UUID, CMD_CELL_VOLTAGES, response=True)
						await asyncio.wait_for(self._cell_event.wait(), timeout=READ_TIMEOUT)

						success = True
						self.last_read_time = time.monotonic()
					finally:
						try:
							await client.disconnect()
						except Exception:
							pass

			except asyncio.TimeoutError:
				logger.warning(f'Read timeout ({self.address})')
			except BleakError as ex:
				logger.info('Connection failed: ' + str(ex))
			except Exception as ex:
				logger.info('BLE error: ' + str(ex))

			if self.running:
				if success:
					sleep_for = self.interval
					logger.info(f'Disconnected {self.address} (read complete, next in {sleep_for}s)')
				else:
					# Before the first successful read, use a short retry so
					# setup_vedbus() can see all batteries within its 30s window.
					# After the first read, fall back to the normal poll interval.
					sleep_for = BT_INIT_RETRY_INTERVAL if self.last_read_time == 0.0 else self.interval
					logger.info(f'Disconnected {self.address} (failed, retry in {sleep_for}s)')
				await asyncio.sleep(sleep_for)

	def stop(self):
		self.running = False

	def addCellDataCallback(self, func):
		self.cellDataCallback = func

	def addGeneralDataCallback(self, func):
		self.generalDataCallback = func

	def _notification_handler(self, sender, data):
		try:
			self._notification_handler_inner(data)
		except Exception as ex:
			logger.warning(f'Notification handler error ({self.address}): {ex}')
			self.last_state = "0000"
			self.cellData = None
			self.generalData = None
			self._last_state_change_time = time.monotonic()

	def _notification_handler_inner(self, data):
		if data is None:
			logger.info("data is None")
			return

		hex_data = binascii.hexlify(data)
		hex_string = hex_data.decode('utf-8')

		HEADER_LEN = 4  # [Start Code][Command][Status][Length]
		FOOTER_LEN = 3  # [16bit Checksum][Stop Code]

		# Check for state machine timeout — if mid-reassembly for too long,
		# reset so we can process fresh packets
		if self.last_state != "0000":
			elapsed = time.monotonic() - self._last_state_change_time
			if elapsed > 10:
				logger.warning(f'State machine timeout ({self.address}): '
				               f'stuck in {self.last_state} for {elapsed:.0f}s, resetting')
				self.last_state = "0000"
				self.cellData = None
				self.generalData = None
				self._last_state_change_time = time.monotonic()

		# Route incoming BMS data.
		# When already mid-reassembly (state != "0000"), always append to the
		# active buffer regardless of fragment content — header bytes are only
		# meaningful at position 0 of the very first fragment.

		if self.last_state == "dd04":
			# Continuation fragment for cell data
			self.cellData = self.cellData + data
		elif self.last_state == "dd03":
			# Continuation fragment for general data
			self.generalData = self.generalData + data
		elif hex_string[:4] == 'dd04':
			# First fragment of a cell data packet
			self.last_state = "dd04"
			self._last_state_change_time = time.monotonic()
			self.cellDataTotalLen = data[3] + HEADER_LEN + FOOTER_LEN
			self.cellDataRemainingLen = self.cellDataTotalLen - len(data)
			logger.debug("cellDataTotalLen: " + str(int(self.cellDataTotalLen)))
			self.cellData = data
		elif hex_string[:4] == 'dd03':
			# First fragment of a general data packet
			self.last_state = "dd03"
			self._last_state_change_time = time.monotonic()
			self.generalDataTotalLen = data[3] + HEADER_LEN + FOOTER_LEN
			self.generalDataRemainingLen = self.generalDataTotalLen - len(data)
			logger.debug("generalDataTotalLen: " + str(int(self.generalDataTotalLen)))
			self.generalData = data

		# Completion checks — use >= to handle oversized accumulation
		if self.last_state == "dd04" and self.cellData and len(self.cellData) >= self.cellDataTotalLen:
			self.cellDataCallback(self.cellData[:self.cellDataTotalLen])
			logger.debug("cellData(" + str(self.cellDataTotalLen) + "): " + str(binascii.hexlify(self.cellData[:self.cellDataTotalLen]).decode('utf-8')))
			self.last_state = "0000"
			self._last_state_change_time = time.monotonic()
			self.cellData = None
			if self._cell_event:
				self._cell_event.set()

		if self.last_state == "dd03" and self.generalData and len(self.generalData) >= self.generalDataTotalLen:
			self.generalDataCallback(self.generalData[:self.generalDataTotalLen])
			logger.debug("generalData(" + str(self.generalDataTotalLen) + "): " + str(binascii.hexlify(self.generalData[:self.generalDataTotalLen]).decode('utf-8')))
			self.last_state = "0000"
			self._last_state_change_time = time.monotonic()
			self.generalData = None
			if self._general_event:
				self._general_event.set()

class JbdBt(Battery):
	def __init__(self, address, initial_delay=0):
		Battery.__init__(self, 0, 0, address)

		self.protection = JbdProtection()
		self.type = "JBD BT"

		self.mutex = Lock()
		self.generalData = None
		self.cellData = None

		self.address = address
		self.port = "/bt" + address.replace(":", "")
		self.interval = BT_POLL_INTERVAL

		dev = BleakJbdDev(self.address)
		dev.initial_delay = initial_delay
		dev.addCellDataCallback(self.cellDataCB)
		dev.addGeneralDataCallback(self.generalDataCB)
		dev.connect()
		self._ble_dev = dev


	def test_connection(self):
		return False

	def get_settings(self):
		deadline = time.monotonic() + BT_INIT_RETRY_INTERVAL
		result = self.read_gen_data()
		while not result:
			if time.monotonic() >= deadline:
				logger.warning(
					"get_settings() timed out for %s — battery not available at startup",
					self.address,
				)
				return False
			time.sleep(1)
			result = self.read_gen_data()
		self.max_battery_charge_current = MAX_BATTERY_CHARGE_CURRENT
		self.max_battery_discharge_current = MAX_BATTERY_DISCHARGE_CURRENT
		return result

	def refresh_data(self):
		result = self.read_gen_data()
		result = result and self.read_cell_data()
		return result

	def log_settings(self):
		# Override log_settings() to call get_settings() first
		self.get_settings()
		Battery.log_settings(self)

	def to_protection_bits(self, byte_data):
		tmp = bin(byte_data)[2:].rjust(13, zero_char)

		self.protection.voltage_high = 2 if is_bit_set(tmp[10]) else 0
		self.protection.voltage_low = 2 if is_bit_set(tmp[9]) else 0
		self.protection.temp_high_charge = 1 if is_bit_set(tmp[8]) else 0
		self.protection.temp_low_charge = 1 if is_bit_set(tmp[7]) else 0
		self.protection.temp_high_discharge = 1 if is_bit_set(tmp[6]) else 0
		self.protection.temp_low_discharge = 1 if is_bit_set(tmp[5]) else 0
		self.protection.current_over = 1 if is_bit_set(tmp[4]) else 0
		self.protection.current_under = 1 if is_bit_set(tmp[3]) else 0

		# Software implementations for low soc
		self.protection.soc_low = (
			2 if self.soc < SOC_LOW_ALARM else 1 if self.soc < SOC_LOW_WARNING else 0
		)

		# extra protection flags for LltJbd
		self.protection.set_voltage_low_cell(is_bit_set(tmp[11]))
		self.protection.set_voltage_high_cell(is_bit_set(tmp[12]))
		self.protection.set_software_lock(is_bit_set(tmp[0]))
		self.protection.set_ic_inspection(is_bit_set(tmp[1]))
		self.protection.set_short(is_bit_set(tmp[2]))

	def to_cell_bits(self, byte_data, byte_data_high):
		self.cells: List[Cell] = []

		# get up to the first 16 cells
		tmp = bin(byte_data)[2:].rjust(min(self.cell_count, 16), zero_char)
		for bit in reversed(tmp):
			self.cells.append(Cell(is_bit_set(bit)))

		# get any cells above 16
		if self.cell_count > 16:
			tmp = bin(byte_data_high)[2:].rjust(self.cell_count - 16, zero_char)
			for bit in reversed(tmp):
				self.cells.append(Cell(is_bit_set(bit)))

	def to_fet_bits(self, byte_data):
		tmp = bin(byte_data)[2:].rjust(2, zero_char)
		self.charge_fet = is_bit_set(tmp[1])
		self.discharge_fet = is_bit_set(tmp[0])

	def read_gen_data(self):
		self.mutex.acquire()

		if self.generalData is None:
			self.mutex.release()
			return False

		gen_data = self.generalData[4:]
		self.mutex.release()

		if len(gen_data) < 27:
			return False

		(
			voltage,
			current,
			capacity_remain,
			capacity,
			self.cycles,
			self.production,
			balance,
			balance2,
			protection,
			version,
			self.soc,
			fet,
			self.cell_count,
			self.temp_sensors,
		) = unpack_from(">HhHHHHhHHBBBBB", gen_data, 0)
		self.voltage = voltage / 100
		self.current = current / 100
		self.capacity_remain = capacity_remain / 100
		self.capacity = capacity / 100
		self.to_cell_bits(balance, balance2)
		self.version = float(str(version >> 4 & 0x0F) + "." + str(version & 0x0F))
		self.to_fet_bits(fet)
		self.to_protection_bits(protection)
		self.max_battery_voltage = MAX_CELL_VOLTAGE * self.cell_count
		self.min_battery_voltage = MIN_CELL_VOLTAGE * self.cell_count

		for t in range(self.temp_sensors):
			temp1 = unpack_from(">H", gen_data, 23 + (2 * t))[0]
			self.to_temp(t + 1, kelvin_to_celsius(temp1 / 10))

		return True

	def read_cell_data(self):
		self.mutex.acquire()

		if self.cellData is None:
			self.mutex.release()
			return False

		cell_data = self.cellData[4:]
		self.mutex.release()

		if len(cell_data) < self.cell_count * 2:
			return False

		for c in range(self.cell_count):
			try:
				cell_volts = unpack_from(">H", cell_data, c * 2)
				if len(cell_volts) != 0:
					self.cells[c].voltage = cell_volts[0] / 1000
			except error:
				logger.warning(
					"Cell %d voltage unpack failed for %s, setting to 0",
					c,
					self.address,
				)
				self.cells[c].voltage = 0

		return True

	def cellDataCB(self, data):
		self.mutex.acquire()
		self.cellData = data
		self.mutex.release()

	def generalDataCB(self, data):
		self.mutex.acquire()
		self.generalData = data
		self.mutex.release()


# Unit test
if __name__ == "__main__":

	batt = JbdBt( "70:3e:97:07:e0:dd" )
	batt.get_settings()

	while True:
		batt.refresh_data()
		print("Cells " + str(batt.cell_count) )
		for c in range(batt.cell_count):
			print( str(batt.cells[c].voltage) + "v", end=" " )
		print("")
		time.sleep(5)
