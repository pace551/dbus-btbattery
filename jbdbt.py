import asyncio
import threading
from bleak import BleakClient, BleakError
from threading import Lock
from battery import Protection, Battery, Cell
from utils import *
from struct import *
import time
import binascii
import os

# JBD BMS standard GATT UUIDs
BLE_TX_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"  # Write commands
BLE_RX_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"  # Receive notifications

# JBD BMS command bytes
CMD_GENERAL_INFO = b'\xdd\xa5\x03\x00\xff\xfd\x77'
CMD_CELL_VOLTAGES = b'\xdd\xa5\x04\x00\xff\xfc\x77'


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

# Serializes BLE connect/scan operations: BlueZ only allows one active
# discovery at a time, so concurrent connect() calls fail with InProgress.
# Released once connected; polling runs concurrently without the lock.
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
		self.running = False
		self.last_successful_callback_time = time.monotonic()
		self.soft_reset_count = 0
		self.reconnect_count = 0

	def reset(self):
		self.last_state = "0000"
		self.cellData = None
		self.generalData = None
		self.cellDataTotalLen = 0
		self.cellDataRemainingLen = 0
		self.generalDataTotalLen = 0
		self.generalDataRemainingLen = 0
		self._last_state_change_time = time.monotonic()

	def data_age(self):
		"""Seconds since last successful data callback."""
		return time.monotonic() - self.last_successful_callback_time

	def soft_reset(self):
		"""Reset the notification handler state machine without disconnecting BLE."""
		logger.info(f'Soft reset ({self.address}): clearing state machine')
		self.last_state = "0000"
		self.cellData = None
		self.generalData = None
		self.cellDataTotalLen = 0
		self.cellDataRemainingLen = 0
		self.generalDataTotalLen = 0
		self.generalDataRemainingLen = 0
		self._last_state_change_time = time.monotonic()
		self.soft_reset_count += 1

	def connect(self):
		self.running = True
		asyncio.run_coroutine_threadsafe(self._ble_main_loop(), _get_ble_loop())

	async def _ble_main_loop(self):
		while self.running:
			client = BleakClient(self.address)
			try:
				logger.info('Connecting ' + self.address)
				# Serialize the connect/scan phase — BlueZ allows only one
				# active discovery at a time. Lock is released once connected
				# so other batteries can connect while this one polls.
				async with _ble_connect_lock:
					await client.connect()

				logger.info('Connected ' + self.address)
				self.reset()
				await client.start_notify(BLE_RX_UUID, self._notification_handler)

				while self.running and client.is_connected:
					try:
						await client.write_gatt_char(BLE_TX_UUID, CMD_GENERAL_INFO, response=True)
						await asyncio.sleep(0.5)
						await client.write_gatt_char(BLE_TX_UUID, CMD_CELL_VOLTAGES, response=True)
					except Exception as ex:
						logger.info(f'GATT write error ({self.address}): {ex}')
						break

					await asyncio.sleep(self.interval)

					# Tiered recovery based on data staleness
					try:
						age = self.data_age()
						if BT_RECONNECT_TIMEOUT and age > BT_RECONNECT_TIMEOUT:
							logger.warning(f'Data stale for {age:.0f}s ({self.address}), forcing BLE reconnect')
							self.reconnect_count += 1
							break  # exits inner loop → disconnect/reconnect in outer loop
						elif BT_SOFT_RESET_TIMEOUT and age > BT_SOFT_RESET_TIMEOUT:
							self.soft_reset()
					except Exception as ex:
						logger.warning(f'Recovery check error ({self.address}): {ex}')

			except BleakError as ex:
				logger.info('Connection failed: ' + str(ex))
			except Exception as ex:
				logger.info('BLE error: ' + str(ex))
			finally:
				await client.disconnect()

			if self.running:
				logger.info('Disconnected')
				await asyncio.sleep(3)

	def stop(self):
		self.running = False
		if self._loop and self._loop.is_running():
			self._loop.call_soon_threadsafe(self._loop.stop)

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
			self.last_successful_callback_time = time.monotonic()
			logger.debug("cellData(" + str(self.cellDataTotalLen) + "): " + str(binascii.hexlify(self.cellData[:self.cellDataTotalLen]).decode('utf-8')))
			self.last_state = "0000"
			self._last_state_change_time = time.monotonic()
			self.cellData = None

		if self.last_state == "dd03" and self.generalData and len(self.generalData) >= self.generalDataTotalLen:
			self.generalDataCallback(self.generalData[:self.generalDataTotalLen])
			self.last_successful_callback_time = time.monotonic()
			logger.debug("generalData(" + str(self.generalDataTotalLen) + "): " + str(binascii.hexlify(self.generalData[:self.generalDataTotalLen]).decode('utf-8')))
			self.last_state = "0000"
			self._last_state_change_time = time.monotonic()
			self.generalData = None

class JbdBt(Battery):
	def __init__(self, address):
		Battery.__init__(self, 0, 0, address)

		self.protection = JbdProtection()
		self.type = "JBD BT"

		self.mutex = Lock()
		self.generalData = None
		self.generalDataTS = time.monotonic()
		self.cellData = None
		self.cellDataTS = time.monotonic()

		self.address = address
		self.port = "/bt" + address.replace(":", "")
		self.interval = BT_POLL_INTERVAL
		self.soft_reset_count = 0
		self.reconnect_count = 0

		dev = BleakJbdDev(self.address)
		dev.addCellDataCallback(self.cellDataCB)
		dev.addGeneralDataCallback(self.generalDataCB)
		dev.connect()
		self._ble_dev = dev


	def test_connection(self):
		return False

	def get_settings(self):
		result = self.read_gen_data()
		while not result:
			result = self.read_gen_data()
			time.sleep(1)
		self.max_battery_charge_current = MAX_BATTERY_CHARGE_CURRENT
		self.max_battery_discharge_current = MAX_BATTERY_DISCHARGE_CURRENT
		return result

	def refresh_data(self):
		result = self.read_gen_data()
		result = result and self.read_cell_data()
		self.soft_reset_count = self._ble_dev.soft_reset_count
		self.reconnect_count = self._ble_dev.reconnect_count
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
		# clear the list
		#for c in self.cells:
		#	self.cells.remove(c)
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
		self.checkTS(self.generalDataTS)

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
		self.checkTS(self.cellDataTS)

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
				self.cells[c].voltage = 0

		return True

	def cellDataCB(self, data):
		self.mutex.acquire()
		self.cellData = data
		self.cellDataTS = time.monotonic()
		self.mutex.release()

	def generalDataCB(self, data):
		self.mutex.acquire()
		self.generalData = data
		self.generalDataTS = time.monotonic()
		self.mutex.release()

	def checkTS(self, ts):
		elapsed = 0
		if ts:
			elapsed = time.monotonic() - ts

		#if (int(elapsed) % 60) == 0:
		#	logger.info(elapsed)

		if BT_WATCHDOG_TIMER == 0:
			return

		if elapsed > BT_WATCHDOG_TIMER:
			logger.info('Watchdog timer expired. BT chipset might be locked up. Rebooting')
			os.system('reboot')


# Unit test
if __name__ == "__main__":


	batt = JbdBt( "70:3e:97:07:e0:dd" )
	#batt = JbdBt( "70:3e:97:07:e0:d9" )
	#batt = JbdBt( "e0:9f:2a:fd:29:26" )
	#batt = JbdBt( "70:3e:97:08:00:62" )
	#batt = JbdBt( "a4:c1:37:40:89:5e" )
	#batt = JbdBt( "a4:c1:37:00:25:91" )
	batt.get_settings()

	while True:
		batt.refresh_data()
		print("Cells " + str(batt.cell_count) )
		for c in range(batt.cell_count):
			print( str(batt.cells[c].voltage) + "v", end=" " )
		print("")
		time.sleep(5)


