"""
Tests for ParallelBattery (parallel.py).

Run standalone:
    python tests/test_parallel.py
"""

import sys
import os

# Allow imports from the project root when running standalone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from battery import Cell, Protection
from tests.test_serial import make_mock_battery
from parallel import ParallelBattery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_battery_with_protection(**kwargs):
	"""Factory that also sets a Protection object on the mock."""
	b = make_mock_battery(**kwargs)
	b.protection = Protection()
	return b


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_voltage_averages():
	b1 = make_mock_battery_with_protection(voltage=48.0)
	b2 = make_mock_battery_with_protection(voltage=50.0)
	b3 = make_mock_battery_with_protection(voltage=46.0)
	b4 = make_mock_battery_with_protection(voltage=52.0)
	pb = ParallelBattery([b1, b2, b3, b4])
	pb.get_settings()
	expected = (48.0 + 50.0 + 46.0 + 52.0) / 4
	assert pb.voltage == expected, f"Expected {expected}, got {pb.voltage}"


def test_current_sums():
	b1 = make_mock_battery_with_protection(current=10.0)
	b2 = make_mock_battery_with_protection(current=12.0)
	b3 = make_mock_battery_with_protection(current=8.0)
	b4 = make_mock_battery_with_protection(current=15.0)
	pb = ParallelBattery([b1, b2, b3, b4])
	pb.get_settings()
	expected = 10.0 + 12.0 + 8.0 + 15.0
	assert pb.current == expected, f"Expected {expected}, got {pb.current}"


def test_capacity_sums():
	b1 = make_mock_battery_with_protection(capacity=100.0)
	b2 = make_mock_battery_with_protection(capacity=120.0)
	pb = ParallelBattery([b1, b2])
	pb.get_settings()
	expected = 100.0 + 120.0
	assert pb.capacity == expected, f"Expected {expected}, got {pb.capacity}"


def test_capacity_remain_sums():
	b1 = make_mock_battery_with_protection(capacity_remain=80.0)
	b2 = make_mock_battery_with_protection(capacity_remain=90.0)
	pb = ParallelBattery([b1, b2])
	pb.get_settings()
	expected = 80.0 + 90.0
	assert pb.capacity_remain == expected, f"Expected {expected}, got {pb.capacity_remain}"


def test_soc_averages():
	b1 = make_mock_battery_with_protection(soc=80.0)
	b2 = make_mock_battery_with_protection(soc=60.0)
	b3 = make_mock_battery_with_protection(soc=70.0)
	b4 = make_mock_battery_with_protection(soc=90.0)
	pb = ParallelBattery([b1, b2, b3, b4])
	pb.get_settings()
	expected = (80.0 + 60.0 + 70.0 + 90.0) / 4
	assert pb.soc == expected, f"Expected {expected}, got {pb.soc}"


def test_cell_count_same_as_single():
	b1 = make_mock_battery_with_protection(cell_count=16, voltage=48.0)
	b2 = make_mock_battery_with_protection(cell_count=16, voltage=48.0)
	b3 = make_mock_battery_with_protection(cell_count=16, voltage=48.0)
	b4 = make_mock_battery_with_protection(cell_count=16, voltage=48.0)
	pb = ParallelBattery([b1, b2, b3, b4])
	pb.get_settings()
	# Parallel doesn't add cells — same count as one battery
	assert pb.cell_count == 16, f"Expected 16, got {pb.cell_count}"


def test_cycles_uses_max():
	b1 = make_mock_battery_with_protection(cycles=10)
	b2 = make_mock_battery_with_protection(cycles=50)
	pb = ParallelBattery([b1, b2])
	pb.get_settings()
	assert pb.cycles == 50, f"Expected 50, got {pb.cycles}"


def test_temp_uses_max():
	b1 = make_mock_battery_with_protection(temp1=25.0, temp2=26.0)
	b2 = make_mock_battery_with_protection(temp1=30.0, temp2=22.0)
	pb = ParallelBattery([b1, b2])
	pb.get_settings()
	assert pb.temp1 == 30.0, f"Expected temp1=30.0, got {pb.temp1}"
	assert pb.temp2 == 26.0, f"Expected temp2=26.0, got {pb.temp2}"


def test_charge_fet_and_false():
	b1 = make_mock_battery_with_protection(charge_fet=True, discharge_fet=True)
	b2 = make_mock_battery_with_protection(charge_fet=False, discharge_fet=True)
	pb = ParallelBattery([b1, b2])
	pb.get_settings()
	assert pb.charge_fet is False, "charge_fet should be AND — one False makes result False"
	assert pb.discharge_fet is True, "discharge_fet should remain True when all are True"


def test_discharge_fet_and_false():
	b1 = make_mock_battery_with_protection(charge_fet=True, discharge_fet=True)
	b2 = make_mock_battery_with_protection(charge_fet=True, discharge_fet=False)
	pb = ParallelBattery([b1, b2])
	pb.get_settings()
	assert pb.discharge_fet is False, "discharge_fet should be AND — one False makes result False"
	assert pb.charge_fet is True, "charge_fet should remain True when all are True"


def test_charge_current_sums():
	"""max_battery_charge_current = per-battery limit * count"""
	from utils import MAX_BATTERY_CHARGE_CURRENT, MAX_BATTERY_DISCHARGE_CURRENT
	b1 = make_mock_battery_with_protection()
	b2 = make_mock_battery_with_protection()
	b3 = make_mock_battery_with_protection()
	pb = ParallelBattery([b1, b2, b3])
	pb.get_settings()
	expected_charge = MAX_BATTERY_CHARGE_CURRENT * 3
	expected_discharge = MAX_BATTERY_DISCHARGE_CURRENT * 3
	assert pb.max_battery_charge_current == expected_charge, \
		f"Expected {expected_charge}, got {pb.max_battery_charge_current}"
	assert pb.max_battery_discharge_current == expected_discharge, \
		f"Expected {expected_discharge}, got {pb.max_battery_discharge_current}"


def test_cells_use_min_voltage_per_position():
	"""Min voltage per cell position across all batteries."""
	# Battery 1: cells at 3.3, 3.4, 3.5, 3.6
	cells_a = []
	for v in [3.3, 3.4, 3.5, 3.6]:
		c = Cell(False)
		c.voltage = v
		cells_a.append(c)

	# Battery 2: cells at 3.5, 3.2, 3.6, 3.4
	cells_b = []
	for v in [3.5, 3.2, 3.6, 3.4]:
		c = Cell(False)
		c.voltage = v
		cells_b.append(c)

	b1 = make_mock_battery_with_protection(cell_count=4, cells=cells_a, voltage=13.8)
	b2 = make_mock_battery_with_protection(cell_count=4, cells=cells_b, voltage=13.7)

	pb = ParallelBattery([b1, b2])
	pb.refresh_data()

	# Min per position: [min(3.3,3.5), min(3.4,3.2), min(3.5,3.6), min(3.6,3.4)]
	expected = [3.3, 3.2, 3.5, 3.4]
	assert len(pb.cells) == 4, f"Expected 4 cells, got {len(pb.cells)}"
	for i, exp_v in enumerate(expected):
		assert pb.cells[i].voltage == exp_v, \
			f"Cell {i}: expected {exp_v}, got {pb.cells[i].voltage}"


def test_protection_worst_case():
	"""Highest alarm level per field wins."""
	b1 = make_mock_battery_with_protection()
	b2 = make_mock_battery_with_protection()

	# Set different alarm levels
	b1.protection.voltage_high = 1   # Warning
	b2.protection.voltage_high = 2   # Alarm — should win

	b1.protection.voltage_low = 2    # Alarm — should win
	b2.protection.voltage_low = 0

	b1.protection.soc_low = None
	b2.protection.soc_low = 1       # non-None should win over None

	b1.protection.current_over = 0
	b2.protection.current_over = 0  # both 0, should stay 0

	pb = ParallelBattery([b1, b2])
	pb.refresh_data()

	assert pb.protection.voltage_high == 2, \
		f"Expected voltage_high=2, got {pb.protection.voltage_high}"
	assert pb.protection.voltage_low == 2, \
		f"Expected voltage_low=2, got {pb.protection.voltage_low}"
	assert pb.protection.soc_low == 1, \
		f"Expected soc_low=1, got {pb.protection.soc_low}"
	assert pb.protection.current_over == 0, \
		f"Expected current_over=0, got {pb.protection.current_over}"


def test_type_string():
	pb = ParallelBattery([])
	assert pb.type == "Parallel", f"Expected 'Parallel', got {pb.type}"


def test_constructor_accepts_list():
	batteries = [make_mock_battery_with_protection() for _ in range(3)]
	pb = ParallelBattery(batteries)
	assert len(pb.batts) == 3


def test_empty_battery_list():
	pb = ParallelBattery([])
	result = pb.get_settings()
	assert result is False, f"Expected False for empty list, got {result}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
	tests = [
		test_voltage_averages,
		test_current_sums,
		test_capacity_sums,
		test_capacity_remain_sums,
		test_soc_averages,
		test_cell_count_same_as_single,
		test_cycles_uses_max,
		test_temp_uses_max,
		test_charge_fet_and_false,
		test_discharge_fet_and_false,
		test_charge_current_sums,
		test_cells_use_min_voltage_per_position,
		test_protection_worst_case,
		test_type_string,
		test_constructor_accepts_list,
		test_empty_battery_list,
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
		print("All ParallelBattery tests passed")
		sys.exit(0)
	else:
		print(f"{failed} test(s) failed")
		sys.exit(1)
