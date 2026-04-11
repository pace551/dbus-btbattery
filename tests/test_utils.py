import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils import mapRange
import utils


def test_mapRange_normal():
    assert mapRange(5, 0, 10, 0, 100) == 50.0


def test_mapRange_equal_bounds_returns_outMin_zero():
    # Without the guard this raises ZeroDivisionError
    assert mapRange(5, 3, 3, 0, 100) == 0


def test_mapRange_equal_bounds_returns_outMin_nonzero():
    assert mapRange(5, 3, 3, 40, 100) == 40


def test_BT_CONNECT_TIMEOUT_is_positive_int():
    assert isinstance(utils.BT_CONNECT_TIMEOUT, int)
    assert utils.BT_CONNECT_TIMEOUT > 0


def test_BT_WATCHDOG_TIMEOUT_is_non_negative_int():
    assert isinstance(utils.BT_WATCHDOG_TIMEOUT, int)
    assert utils.BT_WATCHDOG_TIMEOUT >= 0
