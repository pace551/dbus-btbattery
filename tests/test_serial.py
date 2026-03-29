"""
Tests for SeriesBattery (serial.py).

MockBattery and make_mock_battery are importable by other test files (e.g. test_parallel.py).

Run standalone:
    python tests/test_serial.py
"""

import sys
import os

# Allow imports from the project root when running standalone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from battery import Cell
from serial import SeriesBattery


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class MockBattery:
	"""Simulates a JbdBt without needing BLE."""

	def __init__(
		self,
		voltage=48.0,
		current=10.0,
		soc=80.0,
		capacity=100.0,
		capacity_remain=80.0,
		cell_count=16,
		cycles=5,
		charge_fet=True,
		discharge_fet=True,
		temp_sensors=2,
		temp1=25.0,
		temp2=26.0,
		cells=None,
		production=None,
	):
		self.voltage = voltage
		self.current = current
		self.soc = soc
		self.capacity = capacity
		self.capacity_remain = capacity_remain
		self.cell_count = cell_count
		self.cycles = cycles
		self.charge_fet = charge_fet
		self.discharge_fet = discharge_fet
		self.temp_sensors = temp_sensors
		self.temp1 = temp1
		self.temp2 = temp2
		self.production = production

		if cells is not None:
			self.cells = cells
		else:
			self.cells = []
			for _ in range(cell_count):
				c = Cell(False)
				c.voltage = round(voltage / cell_count, 4)
				self.cells.append(c)

	def get_settings(self):
		return True

	def refresh_data(self):
		return True


def make_mock_battery(**kwargs):
	"""Factory with sensible defaults. Pass keyword args to override."""
	return MockBattery(**kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_voltage_sums():
	b1 = make_mock_battery(voltage=48.0)
	b2 = make_mock_battery(voltage=50.0)
	sb = SeriesBattery([b1, b2])
	sb.get_settings()
	assert sb.voltage == 98.0, f"Expected 98.0, got {sb.voltage}"


def test_cell_count_sums():
	b1 = make_mock_battery(cell_count=8, voltage=24.0)
	b2 = make_mock_battery(cell_count=16, voltage=48.0)
	sb = SeriesBattery([b1, b2])
	sb.get_settings()
	assert sb.cell_count == 24, f"Expected 24, got {sb.cell_count}"


def test_current_averages():
	b1 = make_mock_battery(current=10.0)
	b2 = make_mock_battery(current=20.0)
	sb = SeriesBattery([b1, b2])
	sb.get_settings()
	assert sb.current == 15.0, f"Expected 15.0, got {sb.current}"


def test_soc_uses_lowest():
	b1 = make_mock_battery(soc=90.0)
	b2 = make_mock_battery(soc=70.0)
	b3 = make_mock_battery(soc=85.0)
	sb = SeriesBattery([b1, b2, b3])
	sb.get_settings()
	assert sb.soc == 70.0, f"Expected 70.0, got {sb.soc}"


def test_capacity_uses_lowest():
	b1 = make_mock_battery(capacity=100.0)
	b2 = make_mock_battery(capacity=90.0)
	sb = SeriesBattery([b1, b2])
	sb.get_settings()
	assert sb.capacity == 90.0, f"Expected 90.0, got {sb.capacity}"


def test_refresh_data_concatenates_cells():
	cells_a = [Cell(False) for _ in range(4)]
	cells_b = [Cell(False) for _ in range(8)]
	for i, c in enumerate(cells_a):
		c.voltage = 3.3 + i * 0.01
	for i, c in enumerate(cells_b):
		c.voltage = 3.2 + i * 0.01

	b1 = make_mock_battery(cell_count=4, cells=cells_a, voltage=13.2)
	b2 = make_mock_battery(cell_count=8, cells=cells_b, voltage=25.6)
	sb = SeriesBattery([b1, b2])
	sb.refresh_data()
	assert len(sb.cells) == 12, f"Expected 12 cells, got {len(sb.cells)}"
	assert sb.cells[:4] == cells_a, "First 4 cells should come from b1"
	assert sb.cells[4:] == cells_b, "Last 8 cells should come from b2"


def test_single_battery():
	b = make_mock_battery(voltage=48.0, current=5.0, soc=60.0, capacity=200.0)
	sb = SeriesBattery([b])
	sb.get_settings()
	assert sb.voltage == 48.0
	assert sb.current == 5.0
	assert sb.soc == 60.0
	assert sb.capacity == 200.0


def test_empty_battery_list():
	sb = SeriesBattery([])
	result = sb.get_settings()
	# With no batteries get_settings returns False (result never set to True)
	assert result is False, f"Expected False for empty list, got {result}"
	assert sb.voltage == 0
	assert sb.cell_count == 0


def test_charge_fet_and():
	b1 = make_mock_battery(charge_fet=True, discharge_fet=True)
	b2 = make_mock_battery(charge_fet=False, discharge_fet=True)
	sb = SeriesBattery([b1, b2])
	sb.get_settings()
	assert sb.charge_fet is False, "charge_fet should be AND of all batteries"
	assert sb.discharge_fet is True, "discharge_fet should be AND of all batteries"


def test_cycles_uses_highest():
	b1 = make_mock_battery(cycles=10)
	b2 = make_mock_battery(cycles=50)
	b3 = make_mock_battery(cycles=30)
	sb = SeriesBattery([b1, b2, b3])
	sb.get_settings()
	assert sb.cycles == 50, f"Expected 50, got {sb.cycles}"


def test_type_string():
	sb = SeriesBattery([])
	assert sb.type == "Series", f"Expected 'Series', got {sb.type}"


def test_constructor_accepts_list():
	batteries = [make_mock_battery() for _ in range(3)]
	sb = SeriesBattery(batteries)
	assert len(sb.batts) == 3


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
	tests = [
		test_voltage_sums,
		test_cell_count_sums,
		test_current_averages,
		test_soc_uses_lowest,
		test_capacity_uses_lowest,
		test_refresh_data_concatenates_cells,
		test_single_battery,
		test_empty_battery_list,
		test_charge_fet_and,
		test_cycles_uses_highest,
		test_type_string,
		test_constructor_accepts_list,
	]

	passed = 0
	failed = 0
	for t in tests:
		try:
			t()
			print(f"  PASS  {t.__name__}")
			passed += 1
		except Exception as e:
			print(f"  FAIL  {t.__name__}: {e}")
			failed += 1

	print()
	if failed == 0:
		print("All SeriesBattery tests passed")
		sys.exit(0)
	else:
		print(f"{failed} test(s) failed")
		sys.exit(1)
