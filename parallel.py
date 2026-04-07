from battery import Protection, Battery, Cell
from utils import *


_PROTECTION_FIELDS = [
	"voltage_high",
	"voltage_low",
	"voltage_cell_low",
	"soc_low",
	"current_over",
	"current_under",
	"cell_imbalance",
	"internal_failure",
	"temp_high_charge",
	"temp_low_charge",
	"temp_high_discharge",
	"temp_low_discharge",
]


class ParallelBattery(Battery):
	def __init__(self, batteries=None):
		Battery.__init__(self, 0, 0, 0)

		self.type = "Parallel"
		self.port = "/parallel"

		self.batts = list(batteries) if batteries else []


	def test_connection(self):
		return False


	def get_settings(self):
		self.voltage = 0
		self.current = 0
		self.cycles = 0
		self.soc = 0
		self.cell_count = 0
		self.capacity = 0
		self.capacity_remain = 0
		self.charge_fet = True
		self.discharge_fet = True
		self.temp1 = None
		self.temp2 = None

		result = False
		success_count = 0

		for b in self.batts:
			result = b.get_settings()
			if result:
				success_count += 1

				# Average voltage
				self.voltage += b.voltage

				# Cell count: same as a single battery (parallel doesn't add cells)
				if success_count == 1:
					self.cell_count = b.cell_count

				# Sum current
				self.current += b.current

				# Sum capacity
				self.capacity += b.capacity

				# Sum capacity_remain
				self.capacity_remain += b.capacity_remain

				# Average SOC
				self.soc += b.soc

				# Use highest cycle count
				if b.cycles > self.cycles:
					self.cycles = b.cycles

				# Max temp1
				if b.temp1 is not None:
					if self.temp1 is None or b.temp1 > self.temp1:
						self.temp1 = b.temp1

				# Max temp2
				if b.temp2 is not None:
					if self.temp2 is None or b.temp2 > self.temp2:
						self.temp2 = b.temp2

				# AND FET states
				self.charge_fet &= b.charge_fet
				self.discharge_fet &= b.discharge_fet

		if success_count > 0:
			# Finalize averages
			self.voltage /= success_count
			self.soc /= success_count

			# Use temp_sensors from first battery
			self.temp_sensors = self.batts[0].temp_sensors

		self.max_battery_voltage = MAX_CELL_VOLTAGE * self.cell_count
		self.min_battery_voltage = MIN_CELL_VOLTAGE * self.cell_count

		# Current limits scale with number of batteries
		self.max_battery_charge_current = MAX_BATTERY_CHARGE_CURRENT * success_count
		self.max_battery_discharge_current = MAX_BATTERY_DISCHARGE_CURRENT * success_count

		return success_count > 0


	def _aggregate_protection(self):
		self.protection = Protection()
		for field in _PROTECTION_FIELDS:
			worst = None
			for b in self.batts:
				if not hasattr(b, "protection") or b.protection is None:
					continue
				val = getattr(b.protection, field, None)
				if val is None:
					continue
				if worst is None or val > worst:
					worst = val
			setattr(self.protection, field, worst)


	def refresh_data(self):
		# Refresh each sub-battery first
		refresh_results = []
		for b in self.batts:
			try:
				refresh_results.append(b.refresh_data())
			except Exception:
				logger.error("refresh_data() failed for %s", b.port, exc_info=True)
				refresh_results.append(False)
		any_refreshed = any(refresh_results)

		# Then aggregate the now-fresh data
		result = self.get_settings()

		# Build cells with min voltage per position
		self.cells = []
		if self.cell_count and self.cell_count > 0:
			for i in range(self.cell_count):
				min_voltage = None
				for b in self.batts:
					if i < len(b.cells) and b.cells[i].voltage is not None:
						if min_voltage is None or b.cells[i].voltage < min_voltage:
							min_voltage = b.cells[i].voltage
				c = Cell(False)
				c.voltage = min_voltage
				self.cells.append(c)

		self._aggregate_protection()

		return result and any_refreshed


	def manage_charge_current(self):
		# The base class CCCM methods use absolute current values from utils
		# (computed at import time from MAX_BATTERY_CHARGE_CURRENT), so they
		# always return single-battery limits regardless of self.max_battery_charge_current.
		# Instead, run each sub-battery's calculation and sum the results.
		charge_total = 0
		discharge_total = 0
		for b in self.batts:
			b.manage_charge_current()
			if b.control_charge_current is not None:
				charge_total += b.control_charge_current
			if b.control_discharge_current is not None:
				discharge_total += b.control_discharge_current

		self.control_charge_current = min(charge_total, self.max_battery_charge_current)
		self.control_discharge_current = min(discharge_total, self.max_battery_discharge_current)
		self.control_allow_charge = self.control_charge_current > 0
		self.control_allow_discharge = self.control_discharge_current > 0


	def log_settings(self):
		self.get_settings()
		Battery.log_settings(self)
