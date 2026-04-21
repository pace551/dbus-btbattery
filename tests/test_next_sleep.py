"""Tests for BleakJbdDev._next_sleep() — the sleep-between-cycles calculator.

On failure, we add self.initial_delay to the base sleep so the index-based
stagger set at startup is re-established after simultaneous failures.
Without this, all batteries retry at the same absolute time after a wedge
and compound BlueZ contention. See issue #48.
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import jbdbt
from jbdbt import BleakJbdDev


def _make_dev(initial_delay=0.0, last_read_time=0.0):
    dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")
    dev.initial_delay = initial_delay
    dev.last_read_time = last_read_time
    return dev


def test_success_returns_interval():
    """On a successful read, sleep exactly BT_POLL_INTERVAL — no stagger
    addition (stagger is only needed on failure clusters)."""
    dev = _make_dev(initial_delay=15, last_read_time=1000.0)
    assert dev._next_sleep(success=True) == dev.interval


def test_failure_before_first_read_adds_stagger():
    """Before the first successful read, failure uses the shorter
    BT_INIT_RETRY_INTERVAL as base — plus the stagger."""
    dev = _make_dev(initial_delay=30, last_read_time=0.0)
    assert dev._next_sleep(success=False) == jbdbt.BT_INIT_RETRY_INTERVAL + 30


def test_failure_after_first_read_adds_stagger():
    """After at least one successful read, failure uses BT_POLL_INTERVAL
    as base (same as success path would) — plus the stagger."""
    dev = _make_dev(initial_delay=30, last_read_time=1000.0)
    assert dev._next_sleep(success=False) == dev.interval + 30


def test_failure_with_zero_initial_delay_no_extra_stagger():
    """Battery 0 has initial_delay=0 and should get no extra stagger —
    it's the reference point that other batteries stagger behind."""
    dev = _make_dev(initial_delay=0, last_read_time=0.0)
    assert dev._next_sleep(success=False) == jbdbt.BT_INIT_RETRY_INTERVAL


def test_failure_with_zero_delay_after_read_still_uses_interval():
    dev = _make_dev(initial_delay=0, last_read_time=1000.0)
    assert dev._next_sleep(success=False) == dev.interval


def test_success_ignores_initial_delay():
    """Success path is stagger-independent — the batteries' natural
    interval-based timing keeps them spread out once they're cycling."""
    dev_a = _make_dev(initial_delay=0, last_read_time=1000.0)
    dev_b = _make_dev(initial_delay=30, last_read_time=1000.0)
    assert dev_a._next_sleep(success=True) == dev_b._next_sleep(success=True)
