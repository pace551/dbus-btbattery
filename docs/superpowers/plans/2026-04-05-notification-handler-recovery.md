# Notification Handler Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the notification handler state machine bugs that cause stale battery data, add tiered self-healing recovery, and stop publishing stale values to D-Bus.

**Architecture:** Three layers — (1) fix the packet reassembly bugs that cause the state machine to get stuck, (2) add staleness detection with tiered recovery (soft reset → BLE reconnect → watchdog), (3) stop `dbushelper.py` from republishing stale data. Each battery gets `/Info/SoftResetCount` and `/Info/ReconnectCount` on D-Bus.

**Tech Stack:** Python 3, bleak (BLE), GLib main loop, VeDbusService (D-Bus), pytest

**Spec:** `docs/superpowers/specs/2026-04-05-notification-handler-recovery-design.md`

---

## File Structure

| File | Role |
|------|------|
| `jbdbt.py` | BLE device class (`BleakJbdDev`) and battery class (`JbdBt`) — notification handler, state machine, recovery |
| `dbushelper.py` | D-Bus publishing — stale data gating, recovery counter paths |
| `utils.py` | Module-level config constants |
| `default_config.ini` | Default config values |
| `dbus_btbattery_cli.py` | CLI argument parsing |
| `dbus-btbattery.py` | Main entry — wires config to modules |
| `tests/test_notification_handler.py` | Tests for notification handler fixes and recovery |

---

### Task 1: Add config constants and CLI args for recovery timeouts

**Files:**
- Modify: `default_config.ini:148-155`
- Modify: `utils.py:227-229`
- Modify: `dbus_btbattery_cli.py:19-50`
- Modify: `dbus-btbattery.py:37-45`

- [ ] **Step 1: Add config defaults to `default_config.ini`**

Add after the existing `BT_WATCHDOG_TIMER` line (line 152):

```ini
; Soft-reset timeout in seconds — resets notification handler state machine
; when no successful data callback has arrived within this window.
; 0 to disable. Must be > BT_POLL_INTERVAL.
BT_SOFT_RESET_TIMEOUT = 60

; Reconnect timeout in seconds — forces BLE disconnect/reconnect when
; soft reset hasn't restored data flow. 0 to disable.
; Must be > BT_SOFT_RESET_TIMEOUT.
BT_RECONNECT_TIMEOUT = 120
```

- [ ] **Step 2: Add constants to `utils.py`**

Add after line 228 (`BT_WATCHDOG_TIMER = ...`):

```python
BT_SOFT_RESET_TIMEOUT = int(config["DEFAULT"]["BT_SOFT_RESET_TIMEOUT"])
BT_RECONNECT_TIMEOUT = int(config["DEFAULT"]["BT_RECONNECT_TIMEOUT"])
```

- [ ] **Step 3: Add CLI args to `dbus_btbattery_cli.py`**

Add after the `--dbus-poll-interval` argument (line 24):

```python
parser.add_argument('--bt-soft-reset-timeout', type=int, default=None,
                    help='Soft-reset timeout in seconds, 0 to disable')
parser.add_argument('--bt-reconnect-timeout', type=int, default=None,
                    help='BLE reconnect timeout in seconds, 0 to disable')
```

Add after the `args.dbus_poll_interval` resolution block (line 50):

```python
if args.bt_soft_reset_timeout is None:
    args.bt_soft_reset_timeout = utils.BT_SOFT_RESET_TIMEOUT
if args.bt_reconnect_timeout is None:
    args.bt_reconnect_timeout = utils.BT_RECONNECT_TIMEOUT
```

- [ ] **Step 4: Wire new config values in `dbus-btbattery.py`**

Add after line 45 (`jbdbt.BT_WATCHDOG_TIMER = args.bt_watchdog_timer`):

```python
jbdbt.BT_SOFT_RESET_TIMEOUT = args.bt_soft_reset_timeout
jbdbt.BT_RECONNECT_TIMEOUT = args.bt_reconnect_timeout
```

Add startup validation after the new assignments:

```python
if args.bt_soft_reset_timeout and args.bt_soft_reset_timeout <= args.bt_poll_interval:
    logger.warning(f"BT_SOFT_RESET_TIMEOUT ({args.bt_soft_reset_timeout}s) should be > BT_POLL_INTERVAL ({args.bt_poll_interval}s)")
if args.bt_reconnect_timeout and args.bt_reconnect_timeout <= args.bt_soft_reset_timeout:
    logger.warning(f"BT_RECONNECT_TIMEOUT ({args.bt_reconnect_timeout}s) should be > BT_SOFT_RESET_TIMEOUT ({args.bt_soft_reset_timeout}s)")
```

- [ ] **Step 5: Run existing tests to verify no breakage**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/test_cli.py -v`
Expected: All existing CLI tests pass.

- [ ] **Step 6: Commit**

```bash
git add default_config.ini utils.py dbus_btbattery_cli.py dbus-btbattery.py
git commit -m "feat: add BT_SOFT_RESET_TIMEOUT and BT_RECONNECT_TIMEOUT config"
```

---

### Task 2: Fix notification handler bugs (with tests)

**Files:**
- Create: `tests/test_notification_handler.py`
- Modify: `jbdbt.py:85-199`

- [ ] **Step 1: Write failing tests for all three bugs**

Create `tests/test_notification_handler.py`:

```python
"""
Tests for BleakJbdDev notification handler fixes.

Run: python -m pytest tests/test_notification_handler.py -v
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jbdbt import BleakJbdDev


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_dev():
    """Create a BleakJbdDev with mock callbacks that record calls."""
    dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")
    dev.general_calls = []
    dev.cell_calls = []
    dev.addGeneralDataCallback(lambda data: dev.general_calls.append(data))
    dev.addCellDataCallback(lambda data: dev.cell_calls.append(data))
    return dev


def build_packet(cmd_byte, payload):
    """Build a complete JBD BMS response packet.

    Format: DD <cmd> 00 <len> <payload> <checksum_hi> <checksum_lo> 77
    cmd_byte: 0x03 for general, 0x04 for cell
    payload: bytes of the data section
    """
    length = len(payload)
    # Checksum = 0x10000 - sum(payload bytes + length byte)
    cksum = 0x10000 - (sum(payload) + length) & 0xFFFF
    return bytes([0xDD, cmd_byte, 0x00, length]) + payload + bytes([cksum >> 8, cksum & 0xFF, 0x77])


# ---------------------------------------------------------------------------
# Bug 1: Header detection must only match at position 0
# ---------------------------------------------------------------------------

def test_dd04_in_payload_does_not_corrupt_reassembly():
    """If cell data payload contains 0xDD 0x04 bytes, it must NOT be
    misinterpreted as a new cell-data header."""
    dev = make_dev()

    # Build a general data packet whose payload contains dd04 in the middle.
    # 27 bytes minimum for general data parsing.
    payload = bytearray(27)
    payload[10] = 0xDD  # byte that forms 'dd04' when adjacent to next
    payload[11] = 0x04
    packet = build_packet(0x03, bytes(payload))

    # Send as two fragments — first 10 bytes, then the rest.
    # The second fragment contains dd04 in position 6-7, NOT at position 0.
    dev._notification_handler(None, packet[:10])
    dev._notification_handler(None, packet[10:])

    assert len(dev.general_calls) == 1, "General data callback should fire exactly once"
    assert len(dev.cell_calls) == 0, "Cell data callback should NOT fire"


def test_dd03_in_payload_does_not_corrupt_reassembly():
    """If cell data payload contains 0xDD 0x03 bytes, it must NOT be
    misinterpreted as a new general-data header."""
    dev = make_dev()

    # Build a cell data packet whose payload contains dd03.
    payload = bytearray(8)  # 4 cells * 2 bytes
    payload[2] = 0xDD
    payload[3] = 0x03
    packet = build_packet(0x04, bytes(payload))

    # Send as two fragments
    dev._notification_handler(None, packet[:6])
    dev._notification_handler(None, packet[6:])

    assert len(dev.cell_calls) == 1, "Cell data callback should fire exactly once"
    assert len(dev.general_calls) == 0, "General data callback should NOT fire"


# ---------------------------------------------------------------------------
# Bug 2: Length completion must use >= not ==
# ---------------------------------------------------------------------------

def test_oversized_packet_still_completes():
    """If accumulated data exceeds expected length, the callback must still
    fire (with data truncated to expected length)."""
    dev = make_dev()

    payload = bytes(8)  # 4 cells
    packet = build_packet(0x04, payload)

    # Send the full packet PLUS 3 extra bytes in the last fragment
    dev._notification_handler(None, packet[:6])
    dev._notification_handler(None, packet[6:] + b'\x00\x00\x00')

    assert len(dev.cell_calls) == 1, "Cell data callback should fire even with extra bytes"
    # Callback data should be truncated to expected length
    expected_len = len(payload) + 4 + 3  # HEADER_LEN + FOOTER_LEN
    assert len(dev.cell_calls[0]) == expected_len, \
        f"Callback data should be {expected_len} bytes, got {len(dev.cell_calls[0])}"


# ---------------------------------------------------------------------------
# Bug 3: State machine timeout
# ---------------------------------------------------------------------------

def test_stuck_state_machine_resets_after_timeout():
    """If the state machine is mid-reassembly for too long, it should
    reset so the next valid packet can be processed."""
    dev = make_dev()

    # Send only the first fragment of a cell data packet (incomplete)
    payload = bytes(8)
    packet = build_packet(0x04, payload)
    dev._notification_handler(None, packet[:6])

    assert dev.last_state == "dd04", "State should be mid-reassembly"

    # Simulate time passing beyond the 10s timeout
    dev._last_state_change_time = time.monotonic() - 11

    # Send another cell data first-fragment — the timeout check should
    # reset the state machine first, allowing this new packet to start fresh
    packet2 = build_packet(0x04, bytes([0x01] * 8))
    dev._notification_handler(None, packet2[:6])
    dev._notification_handler(None, packet2[6:])

    assert len(dev.cell_calls) == 1, "New packet should complete after timeout reset"


# ---------------------------------------------------------------------------
# Error handling: exceptions must not kill the handler
# ---------------------------------------------------------------------------

def test_notification_handler_survives_exception():
    """An exception in the callback must not prevent the handler from
    processing subsequent packets."""
    dev = make_dev()

    def exploding_callback(data):
        raise ValueError("boom")

    dev.addCellDataCallback(exploding_callback)

    # First packet — callback raises, but handler should catch it
    payload = bytes(8)
    packet = build_packet(0x04, payload)
    dev._notification_handler(None, packet)

    # State should be reset after the exception
    assert dev.last_state == "0000", "State should reset after exception"

    # Second packet with a working callback should succeed
    dev.cell_calls = []
    dev.addCellDataCallback(lambda data: dev.cell_calls.append(data))

    packet2 = build_packet(0x04, bytes([0x01] * 8))
    dev._notification_handler(None, packet2)

    assert len(dev.cell_calls) == 1, "Handler should work after recovering from exception"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/test_notification_handler.py -v`
Expected: Multiple failures — `dd04_in_payload` and `dd03_in_payload` fail because `find()` matches mid-payload; `oversized_packet` fails because `==` doesn't match; `stuck_state_machine` fails because there's no `_last_state_change_time` attribute; `survives_exception` fails because there's no try/except.

- [ ] **Step 3: Fix Bug 1 — header detection at position 0 only**

In `jbdbt.py`, replace the `_notification_handler` method (lines 156-199) of class `BleakJbdDev`. Also add `_last_state_change_time` to `__init__` (after line 91) and to `reset()` (after line 107):

Add to `__init__` after `self.last_state = "0000"` (line 91):

```python
self._last_state_change_time = time.monotonic()
```

Add to `reset()` after `self.generalDataRemainingLen = 0` (line 107):

```python
self._last_state_change_time = time.monotonic()
```

Replace the entire `_notification_handler` method (lines 156-199) with:

```python
def _notification_handler(self, sender, data):
    try:
        self._notification_handler_inner(data)
    except Exception as ex:
        logger.warning(f'Notification handler error ({self.address}): {ex}')
        self.last_state = "0000"
        self.cellData = None
        self.generalData = None
        self._last_state_change_time = time.monotonic()

def _notification_handler_inner(self, data):
    if data is None:
        logger.info("data is None")
        return

    hex_data = binascii.hexlify(data)
    hex_string = hex_data.decode('utf-8')

    HEADER_LEN = 4  # [Start Code][Command][Status][Length]
    FOOTER_LEN = 3  # [16bit Checksum][Stop Code]

    # Check for state machine timeout — if mid-reassembly for too long,
    # reset so we can process fresh packets
    if self.last_state != "0000":
        elapsed = time.monotonic() - self._last_state_change_time
        if elapsed > 10:
            logger.warning(f'State machine timeout ({self.address}): '
                           f'stuck in {self.last_state} for {elapsed:.0f}s, resetting')
            self.last_state = "0000"
            self.cellData = None
            self.generalData = None
            self._last_state_change_time = time.monotonic()

    # Route incoming BMS data — only match headers at position 0

    # Cell Data
    if hex_string[:4] == 'dd04':
        self.last_state = "dd04"
        self._last_state_change_time = time.monotonic()
        self.cellDataTotalLen = data[3] + HEADER_LEN + FOOTER_LEN
        self.cellDataRemainingLen = self.cellDataTotalLen - len(data)
        logger.debug("cellDataTotalLen: " + str(int(self.cellDataTotalLen)))
        self.cellData = data
    elif self.last_state == "dd04" and hex_string[:4] != 'dd04' and hex_string[:4] != 'dd03':
        self.cellData = self.cellData + data

    # General Data
    elif hex_string[:4] == 'dd03':
        self.last_state = "dd03"
        self._last_state_change_time = time.monotonic()
        self.generalDataTotalLen = data[3] + HEADER_LEN + FOOTER_LEN
        self.generalDataRemainingLen = self.generalDataTotalLen - len(data)
        logger.debug("generalDataTotalLen: " + str(int(self.generalDataTotalLen)))
        self.generalData = data
    elif self.last_state == "dd03" and hex_string[:4] != 'dd04' and hex_string[:4] != 'dd03':
        self.generalData = self.generalData + data

    # Completion checks — use >= to handle oversized accumulation
    if self.last_state == "dd04" and self.cellData and len(self.cellData) >= self.cellDataTotalLen:
        self.cellDataCallback(self.cellData[:self.cellDataTotalLen])
        logger.debug("cellData(" + str(self.cellDataTotalLen) + "): " + str(binascii.hexlify(self.cellData[:self.cellDataTotalLen]).decode('utf-8')))
        self.last_state = "0000"
        self._last_state_change_time = time.monotonic()
        self.cellData = None

    if self.last_state == "dd03" and self.generalData and len(self.generalData) >= self.generalDataTotalLen:
        self.generalDataCallback(self.generalData[:self.generalDataTotalLen])
        logger.debug("generalData(" + str(self.generalDataTotalLen) + "): " + str(binascii.hexlify(self.generalData[:self.generalDataTotalLen]).decode('utf-8')))
        self.last_state = "0000"
        self._last_state_change_time = time.monotonic()
        self.generalData = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/test_notification_handler.py -v`
Expected: All 5 tests pass.

- [ ] **Step 5: Run all tests to verify no regressions**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add jbdbt.py tests/test_notification_handler.py
git commit -m "fix: notification handler header detection, length check, timeout, and error handling"
```

---

### Task 3: Add staleness tracking and tiered recovery

**Files:**
- Modify: `jbdbt.py:85-148` (BleakJbdDev)
- Modify: `jbdbt.py:201-221` (JbdBt)
- Modify: `tests/test_notification_handler.py`

- [ ] **Step 1: Write failing tests for staleness tracking and recovery**

Append to `tests/test_notification_handler.py`:

```python
# ---------------------------------------------------------------------------
# Staleness tracking and tiered recovery
# ---------------------------------------------------------------------------

def test_successful_callback_updates_staleness_timestamp():
    """last_successful_callback_time should update when a complete
    packet is processed."""
    dev = make_dev()

    before = time.monotonic()
    payload = bytes(8)
    packet = build_packet(0x04, payload)
    dev._notification_handler(None, packet)
    after = time.monotonic()

    assert dev.last_successful_callback_time >= before
    assert dev.last_successful_callback_time <= after


def test_data_age_returns_seconds_since_last_callback():
    """data_age() should return elapsed time since last successful callback."""
    dev = make_dev()

    # Simulate a callback 30 seconds ago
    dev.last_successful_callback_time = time.monotonic() - 30
    age = dev.data_age()
    assert 29 <= age <= 31, f"Expected ~30s, got {age}"


def test_soft_reset_clears_state_and_increments_counter():
    """soft_reset() should clear the state machine and increment the counter."""
    dev = make_dev()

    # Put state machine in mid-reassembly
    payload = bytes(8)
    packet = build_packet(0x04, payload)
    dev._notification_handler(None, packet[:6])
    assert dev.last_state == "dd04"

    dev.soft_reset()

    assert dev.last_state == "0000"
    assert dev.cellData is None
    assert dev.generalData is None
    assert dev.soft_reset_count == 1


def test_soft_reset_count_and_reconnect_count_start_at_zero():
    """Counters should be zero on fresh device."""
    dev = make_dev()
    assert dev.soft_reset_count == 0
    assert dev.reconnect_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/test_notification_handler.py -v -k "staleness or soft_reset or reconnect_count"`
Expected: Failures — `last_successful_callback_time`, `data_age()`, `soft_reset()`, and counter attributes don't exist yet.

- [ ] **Step 3: Add staleness tracking and recovery methods to `BleakJbdDev`**

In `jbdbt.py`, add to `BleakJbdDev.__init__` after `self._last_state_change_time = time.monotonic()`:

```python
self.last_successful_callback_time = time.monotonic()
self.soft_reset_count = 0
self.reconnect_count = 0
```

Add to `BleakJbdDev.reset()` at the end:

```python
self._last_state_change_time = time.monotonic()
```

Note: `reset()` should NOT reset `last_successful_callback_time` — that tracks actual data flow, not connection events. And counters persist across reconnections.

Add new methods to `BleakJbdDev` after `reset()`:

```python
def data_age(self):
    """Seconds since last successful data callback."""
    return time.monotonic() - self.last_successful_callback_time

def soft_reset(self):
    """Reset the notification handler state machine without disconnecting BLE."""
    logger.info(f'Soft reset ({self.address}): clearing state machine')
    self.last_state = "0000"
    self.cellData = None
    self.generalData = None
    self.cellDataTotalLen = 0
    self.cellDataRemainingLen = 0
    self.generalDataTotalLen = 0
    self.generalDataRemainingLen = 0
    self._last_state_change_time = time.monotonic()
    self.soft_reset_count += 1
```

Update the completion checks in `_notification_handler_inner` — add a line to update `last_successful_callback_time` after each successful callback. In the cell data completion block, after `self.cellDataCallback(...)`:

```python
self.last_successful_callback_time = time.monotonic()
```

And in the general data completion block, after `self.generalDataCallback(...)`:

```python
self.last_successful_callback_time = time.monotonic()
```

- [ ] **Step 4: Add tiered recovery to `_ble_main_loop`**

Replace the inner while loop in `_ble_main_loop` (lines 128-132) with:

```python
while self.running and client.is_connected:
    try:
        await client.write_gatt_char(BLE_TX_UUID, CMD_GENERAL_INFO, response=True)
        await asyncio.sleep(0.5)
        await client.write_gatt_char(BLE_TX_UUID, CMD_CELL_VOLTAGES, response=True)
    except Exception as ex:
        logger.info(f'GATT write error ({self.address}): {ex}')
        break

    await asyncio.sleep(self.interval)

    # Tiered recovery based on data staleness
    try:
        age = self.data_age()
        if BT_RECONNECT_TIMEOUT and age > BT_RECONNECT_TIMEOUT:
            logger.warning(f'Data stale for {age:.0f}s ({self.address}), forcing BLE reconnect')
            self.reconnect_count += 1
            break  # exits inner loop → disconnect/reconnect in outer loop
        elif BT_SOFT_RESET_TIMEOUT and age > BT_SOFT_RESET_TIMEOUT:
            self.soft_reset()
    except Exception as ex:
        logger.warning(f'Recovery check error ({self.address}): {ex}')
```

- [ ] **Step 5: Surface counters in `JbdBt`**

In `JbdBt.__init__` (around line 218), after `dev.connect()`, add:

```python
self._ble_dev = dev
```

In `JbdBt.refresh_data()` (around line 236), add before the `return` statement:

```python
self.soft_reset_count = self._ble_dev.soft_reset_count
self.reconnect_count = self._ble_dev.reconnect_count
```

Add these attributes to `JbdBt.__init__` after `self.interval = BT_POLL_INTERVAL` (line 216):

```python
self.soft_reset_count = 0
self.reconnect_count = 0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/test_notification_handler.py -v`
Expected: All tests pass.

- [ ] **Step 7: Run all tests**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add jbdbt.py tests/test_notification_handler.py
git commit -m "feat: add staleness tracking and tiered recovery (soft reset + BLE reconnect)"
```

---

### Task 4: Stop publishing stale data and add recovery counter D-Bus paths

**Files:**
- Modify: `dbushelper.py:140-161` (setup_vedbus — add paths)
- Modify: `dbushelper.py:316-344` (publish_battery — stale data gating)
- Modify: `dbushelper.py:346+` (publish_dbus — publish counters)

- [ ] **Step 1: Add D-Bus paths for recovery counters in `setup_vedbus`**

In `dbushelper.py`, add after the `/Info/MaxDischargeCurrent` path block (around line 160):

```python
self._dbusservice.add_path("/Info/SoftResetCount", 0, writeable=True)
self._dbusservice.add_path("/Info/ReconnectCount", 0, writeable=True)
```

- [ ] **Step 2: Gate stale data publishing in `publish_battery`**

Replace the `publish_battery` method (lines 316-344) with:

```python
def publish_battery(self, loop):
    # This is called every battery.poll_interval milli second as set up per battery type to read and update the data
    try:
        # Call the battery's refresh_data function
        success = self.battery.refresh_data()
        if success:
            self.error_count = 0
            self.battery.online = True

            # This is to mannage CCL\DCL
            self.battery.manage_charge_current()

            # This is to mannage CVCL
            self.battery.manage_charge_voltage()

            # publish all the data from the battery object to dbus
            self.publish_dbus()
        else:
            self.error_count += 1
            if self.error_count >= 10:
                if self.battery.online:
                    self.battery.online = False
                    # Publish once to push offline status to D-Bus
                    self.publish_dbus()
            # Has it completely failed
            if self.error_count >= 60:
                loop.quit()

        # Always publish recovery counters regardless of data freshness
        self.publish_counters()

    except Exception:
        traceback.print_exc()
        loop.quit()
```

- [ ] **Step 3: Add `publish_counters` method**

Add after the `publish_battery` method:

```python
def publish_counters(self):
    """Publish recovery counters — always, even when data is stale."""
    try:
        if hasattr(self.battery, 'soft_reset_count'):
            self._dbusservice["/Info/SoftResetCount"] = self.battery.soft_reset_count
        if hasattr(self.battery, 'reconnect_count'):
            self._dbusservice["/Info/ReconnectCount"] = self.battery.reconnect_count
    except Exception:
        pass  # counters are diagnostic, never fail the publish loop
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add dbushelper.py
git commit -m "feat: gate stale data publishing and add recovery counter D-Bus paths"
```

---

### Task 5: Integration verification and final commit

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 2: Verify config end-to-end**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python -c "import utils; print(f'SOFT_RESET={utils.BT_SOFT_RESET_TIMEOUT}, RECONNECT={utils.BT_RECONNECT_TIMEOUT}')"`
Expected: `SOFT_RESET=60, RECONNECT=120`

- [ ] **Step 3: Verify CLI args parse**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && python dbus_btbattery_cli.py --help | grep -E "soft-reset|reconnect-timeout"`
Expected: Both new args appear in help text.

- [ ] **Step 4: Review git log for clean commit history**

Run: `cd /Users/jafinch/Dev/claude-code/van/dbus-btbattery && git log --oneline -5`
Expected: 4 clean commits from Tasks 1-4.
