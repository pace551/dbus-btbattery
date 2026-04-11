import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from jbdbt import BleakJbdDev


def test_last_read_time_initialises_to_zero():
    dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")
    assert dev.last_read_time == 0.0
