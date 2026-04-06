# Notification Handler Recovery Design

**Date:** 2026-04-05
**Status:** Approved
**Repo:** pace551/dbus-btbattery

## Problem

After running for 31+ hours, all 4 BT battery D-Bus values went stale (voltage stuck at 13.27V, current at 0.0A) while actual battery state had drifted (confirmed 13.32V after reboot). The daemon process remained running and BLE connections appeared healthy on the Devices screen. Different batteries went stale at different times, evidenced by divergent temperature readings (+/-10F) that should have been within 1-2F for co-located batteries.

## Root Cause

Three bugs in `BleakJbdDev._notification_handler` (`jbdbt.py:156-199`) cause the packet reassembly state machine to silently get stuck, preventing data callbacks from firing. Meanwhile, `dbushelper.py` unconditionally publishes the last cached values to D-Bus, making stale data appear fresh.

### Bug 1: Header detection searches entire packet

```python
if hex_string.find('dd04') != -1:  # matches ANYWHERE in payload
```

If BMS data payload contains bytes `0xDD 0x04` (across field boundaries), the state machine misinterprets it as a new message header mid-reassembly, corrupting the packet.

### Bug 2: Length completion uses exact equality

```python
if ... and len(self.cellData) == self.cellDataTotalLen:  # stuck if overshoots
```

If accumulated data exceeds expected length (extra bytes, off-by-one in BMS length byte), the `==` check fails forever. The state machine waits for an exact match that can never happen.

### Bug 3: No timeout or reset for stuck state machine

Once stuck in mid-reassembly state (`dd03`/`dd04`), the state machine stays stuck permanently. BLE commands continue, BMS responds, but the handler fails to reassemble. Callbacks never fire, but the daemon keeps publishing last cached values.

## Design

### Section 1: Notification Handler Bug Fixes

**File:** `jbdbt.py` — `BleakJbdDev._notification_handler`

**1a. Header detection at position 0 only:**
```python
if hex_string[:4] == 'dd04':   # only match at packet start
```
Same for `dd03`. Continuation packets with `dd04`/`dd03` bytes in their payload no longer corrupt reassembly.

**1b. Length completion with `>=`:**
```python
if ... and len(self.cellData) >= self.cellDataTotalLen:
```
If accumulated data exceeds expected length, truncate to `cellDataTotalLen` before calling the callback.

**1c. State machine timeout:**
Track `last_state_change_time`. If the state machine has been in `dd03`/`dd04` (mid-reassembly) for more than 10 seconds without completing, log a warning and reset to `"0000"`.

**1d. Error handling:**
Wrap the entire `_notification_handler` body in try/except. On any exception, log the error and reset the state machine to `"0000"` so it can recover on the next packet. An unhandled exception in this handler runs in the asyncio event loop and could silently kill data flow for that battery.

### Section 2: Tiered Recovery

**2a. Staleness tracking in `BleakJbdDev`:**

Add `last_successful_callback_time` — updated whenever `cellDataCallback` or `generalDataCallback` is called with a complete packet. This is the single source of truth for whether the battery's data pipeline is working.

**2b. Recovery tiers in `_ble_main_loop`:**

Add a staleness check in the inner while loop after each sleep cycle:

| Tier | Trigger | Action | Log Level |
|------|---------|--------|-----------|
| 1 — Soft reset | `BT_SOFT_RESET_TIMEOUT` (default 60s) since last successful callback | Reset `last_state` to `"0000"`, clear partial buffers. Increment `soft_reset_count`. | INFO |
| 2 — BLE reconnect | `BT_RECONNECT_TIMEOUT` (default 120s) since last successful callback | Break out of inner while loop, triggering the existing disconnect/reconnect flow in `_ble_main_loop`'s outer loop. Increment `reconnect_count`. | WARNING |
| 3 — Watchdog reboot | `BT_WATCHDOG_TIMER` (default 300s, can be 0 to disable) | Existing `checkTS()` behavior. Unchanged. | ERROR |

**2c. Surfacing counts to `JbdBt`:**

`BleakJbdDev` exposes `soft_reset_count` and `reconnect_count` as properties. `JbdBt.refresh_data()` reads them and stores on the battery object so `dbushelper.py` can publish them.

**2d. Error handling:**

- `_ble_main_loop`: staleness check wrapped in try/except — a failure in recovery logic must not kill the BLE loop
- `_notification_handler`: entire body wrapped in try/except with state machine reset on exception
- Soft reset method: defensive, only clears state, cannot raise

### Section 3: Stale Data Publishing

**File:** `dbushelper.py` — `publish_battery`

Current behavior: `publish_dbus()` runs unconditionally, republishing stale cached values as if fresh.

New behavior:

- **`refresh_data()` succeeds:** publish normally, reset error count
- **Fails for < 10 consecutive polls:** skip `publish_dbus()` — D-Bus retains last-written values but we're not actively refreshing them. Brief grace period for recovery.
- **Fails for >= 10 consecutive polls (~50s):** set `battery.online = False` and publish once — pushes offline status to D-Bus so the GUI shows "Offline"
- **Fails for >= 60 consecutive polls (~5 min):** `loop.quit()` as before (daemon exit, supervisor restarts)

Recovery counters (`/Info/SoftResetCount`, `/Info/ReconnectCount`) always publish regardless of stale-data skip, so diagnostic values remain visible.

### Section 4: Configuration

**File:** `default_config.ini`

New keys:

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

Wired through `dbus_btbattery_cli.py` (argparse) and applied to both `utils` and `jbdbt` modules at startup, same pattern as existing `BT_POLL_INTERVAL` and `BT_WATCHDOG_TIMER`.

Startup validation: warn if `BT_SOFT_RESET_TIMEOUT` <= `BT_POLL_INTERVAL` or `BT_RECONNECT_TIMEOUT` <= `BT_SOFT_RESET_TIMEOUT`.

## Files Changed

| File | Changes |
|------|---------|
| `jbdbt.py` | Fix 3 notification handler bugs, add state machine timeout, try/except wrapper, staleness tracking, soft reset method, reconnect signaling, expose counters |
| `dbushelper.py` | Skip `publish_dbus()` on stale data, publish offline after 10 failures, publish recovery counters |
| `dbus-btbattery.py` | Wire new config values to `jbdbt` module |
| `dbus_btbattery_cli.py` | Add `--bt-soft-reset-timeout` and `--bt-reconnect-timeout` args |
| `default_config.ini` | Add `BT_SOFT_RESET_TIMEOUT` and `BT_RECONNECT_TIMEOUT` |
| `utils.py` | Add new timeout constants |

No new files. No changes to `parallel.py` — the parallel aggregate inherits the fix naturally because its `refresh_data()` calls each sub-battery's `refresh_data()`, and if a sub-battery goes offline, the aggregate reflects that.

## D-Bus Paths Added

Per battery service (`com.victronenergy.battery.btXXX`):

- `/Info/SoftResetCount` — number of state machine resets since daemon start
- `/Info/ReconnectCount` — number of BLE reconnects since daemon start
