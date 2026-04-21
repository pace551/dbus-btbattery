"""Tests for the retry_pending() closure used in dbus-btbattery.py main().

After all retries exhaust, if NO batteries have registered, we call
mainloop.quit() so the watchdog-driven restart cycle can try again
with a fresh process (and fresh HCI reset). Without this, a wedge that
prevents the first connection traps the process in a broken state even
though the BLE loop itself may recover later.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class _MockHelper:
    def __init__(self, succeed=False, port="/test"):
        self._succeed = succeed
        self.battery = type("B", (), {"port": port})()

    def setup_vedbus(self):
        return self._succeed


class _MockLoop:
    def __init__(self):
        self.quit_called = False
    def quit(self):
        self.quit_called = True


def _make_retry_pending(pending, active_helpers, mainloop, max_retries=5,
                        helper_factory=None):
    """Mirror the retry_pending closure in dbus-btbattery.py main().

    helper_factory(battery) is called for each retry; it returns a
    helper object with setup_vedbus(). This lets tests control the
    pass/fail sequence per retry cycle.
    """
    import logging
    logger = logging.getLogger("BluetoothBattery")

    def retry_pending():
        still_pending = []
        for batt, retry_count in pending:
            new_helper = helper_factory(batt)
            if new_helper.setup_vedbus():
                active_helpers.append(new_helper)
                logger.info("Battery %s registered after %d retries",
                            batt.port, retry_count + 1)
            else:
                retry_count += 1
                if max_retries > 0 and retry_count >= max_retries:
                    logger.error("Battery %s: giving up after %d retries",
                                 batt.port, retry_count)
                else:
                    still_pending.append([batt, retry_count])
        pending.clear()
        pending.extend(still_pending)

        if len(pending) == 0 and len(active_helpers) == 0:
            # All gave up with nothing registered — exit so the watchdog-driven
            # restart cycle can retry with a fresh process and HCI reset.
            logger.error("All batteries failed initial setup, exiting service")
            mainloop.quit()
            return False

        return len(pending) > 0

    return retry_pending


def _make_battery(port):
    return type("B", (), {"port": port})()


def test_retry_pending_keeps_retrying_when_under_cap():
    loop = _MockLoop()
    batt = _make_battery("/btA")
    pending = [[batt, 0]]
    active = []

    rp = _make_retry_pending(
        pending, active, loop,
        max_retries=5,
        helper_factory=lambda b: _MockHelper(succeed=False, port=b.port),
    )

    assert rp() is True   # still has pending, keep timer running
    assert not loop.quit_called
    assert pending == [[batt, 1]]


def test_retry_pending_registers_on_success():
    loop = _MockLoop()
    batt = _make_battery("/btA")
    pending = [[batt, 2]]
    active = []

    rp = _make_retry_pending(
        pending, active, loop,
        max_retries=5,
        helper_factory=lambda b: _MockHelper(succeed=True, port=b.port),
    )

    assert rp() is False   # pending empty AND active non-empty
    assert len(active) == 1
    assert not loop.quit_called


def test_retry_pending_quits_when_all_give_up_with_none_active():
    """THE KEY FIX: when all retries exhaust and nothing registered,
    call mainloop.quit() to trigger a restart."""
    loop = _MockLoop()
    batt = _make_battery("/btA")
    pending = [[batt, 4]]   # one more retry takes it past max=5
    active = []

    rp = _make_retry_pending(
        pending, active, loop,
        max_retries=5,
        helper_factory=lambda b: _MockHelper(succeed=False, port=b.port),
    )

    assert rp() is False
    assert loop.quit_called
    assert len(pending) == 0
    assert len(active) == 0


def test_retry_pending_does_not_quit_when_partial_success():
    """When some batteries registered and others gave up, keep running
    with the partial set — don't force a restart."""
    loop = _MockLoop()
    batt_a = _make_battery("/btA")
    batt_b = _make_battery("/btB")

    # A is already registered (simulate prior success)
    active = [_MockHelper(succeed=True, port="/btA")]
    # B is on its last retry and about to give up
    pending = [[batt_b, 4]]

    rp = _make_retry_pending(
        pending, active, loop,
        max_retries=5,
        helper_factory=lambda b: _MockHelper(succeed=False, port=b.port),
    )

    result = rp()
    assert result is False   # pending empty, so timer stops
    assert not loop.quit_called   # but we don't force a restart
    assert len(pending) == 0
    assert len(active) == 1


def test_retry_pending_all_batteries_gave_up_multiple_quits():
    """With multiple batteries all failing, we only quit once all have
    exhausted their retries AND nothing registered."""
    loop = _MockLoop()
    batt_a = _make_battery("/btA")
    batt_b = _make_battery("/btB")
    pending = [[batt_a, 4], [batt_b, 2]]   # A at cap-1, B mid-way
    active = []

    rp = _make_retry_pending(
        pending, active, loop,
        max_retries=5,
        helper_factory=lambda b: _MockHelper(succeed=False, port=b.port),
    )

    # First call: A gives up, B still has retries left
    assert rp() is True
    assert not loop.quit_called
    assert len(pending) == 1   # B still pending

    # Keep calling until B gives up too
    for _ in range(3):
        if rp() is False:
            break

    # Once B exhausts and active is still empty, quit fires
    assert loop.quit_called


def test_retry_pending_infinite_retries_never_give_up():
    """With max_retries=0 (infinite), nothing ever gives up so quit is
    never called from retry_pending regardless of how many failures."""
    loop = _MockLoop()
    batt = _make_battery("/btA")
    pending = [[batt, 99]]
    active = []

    rp = _make_retry_pending(
        pending, active, loop,
        max_retries=0,   # infinite
        helper_factory=lambda b: _MockHelper(succeed=False, port=b.port),
    )

    assert rp() is True   # still pending
    assert not loop.quit_called
    assert pending == [[batt, 100]]
