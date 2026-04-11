import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import time
import utils


class _MockDev:
    def __init__(self, last_read_time, address="AA:BB:CC:DD:EE:FF"):
        self.last_read_time = last_read_time
        self.address = address


class _MockBatt:
    def __init__(self, last_read_time):
        self._ble_dev = _MockDev(last_read_time)


class _MockLoop:
    def __init__(self):
        self.quit_called = False
    def quit(self):
        self.quit_called = True


def _make_watchdog(batteries, mainloop):
    """Mirror the exact closure used in dbus-btbattery.py main()."""
    def watchdog():
        now = time.monotonic()
        for batt in batteries:
            if batt._ble_dev.last_read_time == 0.0:
                continue
            elapsed = now - batt._ble_dev.last_read_time
            if elapsed > utils.BT_WATCHDOG_TIMEOUT:
                import logging
                logging.getLogger("BluetoothBattery").error(
                    "Watchdog: %s has not completed a read in %.0fs, restarting",
                    batt._ble_dev.address,
                    elapsed,
                )
                mainloop.quit()
                return False
        return True
    return watchdog


def test_watchdog_skips_battery_never_read():
    loop = _MockLoop()
    batt = _MockBatt(0.0)
    wd = _make_watchdog([batt], loop)
    assert wd() is True
    assert not loop.quit_called


def test_watchdog_no_trigger_when_recent():
    loop = _MockLoop()
    batt = _MockBatt(time.monotonic())
    wd = _make_watchdog([batt], loop)
    assert wd() is True
    assert not loop.quit_called


def test_watchdog_triggers_and_quits_when_stale():
    loop = _MockLoop()
    stale_time = time.monotonic() - utils.BT_WATCHDOG_TIMEOUT - 1
    batt = _MockBatt(stale_time)
    wd = _make_watchdog([batt], loop)
    result = wd()
    assert result is False   # False stops the GLib timer
    assert loop.quit_called


def test_watchdog_first_stale_battery_stops_scan():
    """Once one battery is stale, quit is called and the rest are not checked."""
    loop = _MockLoop()
    stale = _MockBatt(time.monotonic() - utils.BT_WATCHDOG_TIMEOUT - 1)
    fresh = _MockBatt(time.monotonic())
    wd = _make_watchdog([stale, fresh], loop)
    assert wd() is False
    assert loop.quit_called
