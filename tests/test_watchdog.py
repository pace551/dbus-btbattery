import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import time
import utils


class _MockDev:
    def __init__(self, last_read_time, address="AA:BB:CC:DD:EE:FF"):
        self.last_read_time = last_read_time
        self.address = address


class _MockBatt:
    def __init__(self, last_read_time, address="AA:BB:CC:DD:EE:FF"):
        self._ble_dev = _MockDev(last_read_time, address)


class _MockLoop:
    def __init__(self):
        self.quit_called = False
    def quit(self):
        self.quit_called = True


def _make_watchdog(batteries, mainloop, process_start_time):
    """Mirror the exact closure used in dbus-btbattery.py main().

    Batteries that have never successfully read (last_read_time == 0.0)
    are checked against process_start_time instead of being skipped.
    This ensures the service can escape a wedge where the first read
    never completes after a restart.
    """
    def watchdog():
        now = time.monotonic()
        for batt in batteries:
            if batt._ble_dev.last_read_time == 0.0:
                elapsed = now - process_start_time
                reason = "no reads since startup"
            else:
                elapsed = now - batt._ble_dev.last_read_time
                reason = "no read"
            if elapsed > utils.BT_WATCHDOG_TIMEOUT:
                import logging
                logging.getLogger("BluetoothBattery").error(
                    "Watchdog: %s — %s in %.0fs, restarting",
                    batt._ble_dev.address, reason, elapsed,
                )
                mainloop.quit()
                return False
        return True
    return watchdog


def test_watchdog_no_trigger_when_recent():
    loop = _MockLoop()
    batt = _MockBatt(time.monotonic())
    wd = _make_watchdog([batt], loop, time.monotonic())
    assert wd() is True
    assert not loop.quit_called


def test_watchdog_triggers_and_quits_when_stale():
    loop = _MockLoop()
    stale_time = time.monotonic() - utils.BT_WATCHDOG_TIMEOUT - 1
    batt = _MockBatt(stale_time)
    wd = _make_watchdog([batt], loop, time.monotonic())
    result = wd()
    assert result is False   # False stops the GLib timer
    assert loop.quit_called


def test_watchdog_first_stale_battery_stops_scan():
    """Once one battery is stale, quit is called and the rest are not checked."""
    loop = _MockLoop()
    stale = _MockBatt(time.monotonic() - utils.BT_WATCHDOG_TIMEOUT - 1)
    fresh = _MockBatt(time.monotonic())
    wd = _make_watchdog([stale, fresh], loop, time.monotonic())
    assert wd() is False
    assert loop.quit_called


def test_watchdog_never_read_recent_startup_does_not_trigger():
    """A battery that has never read should NOT trigger the watchdog while
    the process is still within its grace period (process started recently)."""
    loop = _MockLoop()
    batt = _MockBatt(0.0)
    wd = _make_watchdog([batt], loop, time.monotonic())   # just started
    assert wd() is True
    assert not loop.quit_called


def test_watchdog_never_read_old_startup_triggers():
    """A battery that has never read MUST trigger the watchdog if the
    process has been running longer than BT_WATCHDOG_TIMEOUT — otherwise
    a wedge that prevents the first read traps the process forever."""
    loop = _MockLoop()
    batt = _MockBatt(0.0)
    old_start = time.monotonic() - utils.BT_WATCHDOG_TIMEOUT - 1
    wd = _make_watchdog([batt], loop, old_start)
    assert wd() is False
    assert loop.quit_called


def test_watchdog_never_read_triggered_among_mixed_batteries():
    """One never-read battery past the timeout should trigger even when
    other batteries are reading normally."""
    loop = _MockLoop()
    fresh = _MockBatt(time.monotonic(), address="AA:AA:AA:AA:AA:AA")
    never = _MockBatt(0.0, address="BB:BB:BB:BB:BB:BB")
    old_start = time.monotonic() - utils.BT_WATCHDOG_TIMEOUT - 1
    wd = _make_watchdog([fresh, never], loop, old_start)
    assert wd() is False
    assert loop.quit_called
