import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils import mapRange


def test_mapRange_normal():
    assert mapRange(5, 0, 10, 0, 100) == 50.0


def test_mapRange_equal_bounds_returns_outMin_zero():
    # Without the guard this raises ZeroDivisionError
    assert mapRange(5, 3, 3, 0, 100) == 0


def test_mapRange_equal_bounds_returns_outMin_nonzero():
    assert mapRange(5, 3, 3, 40, 100) == 40
