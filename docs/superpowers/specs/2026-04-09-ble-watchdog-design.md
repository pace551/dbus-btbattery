# BLE Watchdog Design

## Problem

After several hours of operation, `bleak.connect()` can hang indefinitely when
BlueZ enters a wedged state. Because `_ble_connect_lock` is held for the entire
connect-read-disconnect cycle, a single stuck `connect()` call blocks all other
batteries from connecting. The service continues running and logging "Connecting"
messages but no reads ever complete, leaving D-Bus data permanently stale until
the service is manually restarted.

## Solution

Two complementary defences:

1. **Explicit connect timeout** — prevent a single `connect()` call from hanging
   the asyncio loop indefinitely.
2. **GLib watchdog timer** — detect when reads stop completing across the fleet
   and call `mainloop.quit()` so runit restarts the service automatically.

---

## Components

### 1. `BT_CONNECT_TIMEOUT` config key (`default_config.ini`, `utils.py`)

New integer config key, default `15` seconds. Read in `utils.py` alongside the
other BT timing constants.

Distinct from `READ_TIMEOUT` (which covers GATT notification waits after a
successful connection). `BT_CONNECT_TIMEOUT` covers only the BLE connection
handshake.

### 2. Explicit connect timeout (`jbdbt.py`)

```python
await client.connect(timeout=BT_CONNECT_TIMEOUT)
```

`BT_CONNECT_TIMEOUT` is available via `from utils import *`. No new imports
needed. This is the only change to `_ble_main_loop`.

### 3. `last_read_time` on `BleakJbdDev` (`jbdbt.py`)

```python
self.last_read_time: float = 0.0  # monotonic; 0 = never read
```

Set to `time.monotonic()` immediately after `success = True` in `_ble_main_loop`.
Written from the asyncio thread; read from the GLib thread. Because it is a
plain float assignment (atomic on CPython) this is safe without a lock.

### 4. `BT_WATCHDOG_TIMEOUT` config key (`default_config.ini`, `utils.py`)

New integer config key, default `600` seconds (10 minutes ≈ 5 missed read
cycles at the default 120 s poll interval). Set to `0` to disable.

### 5. GLib watchdog timer (`dbus-btbattery.py`)

Registered at startup if `BT_WATCHDOG_TIMEOUT > 0`. Fires every 60 seconds.

`batteries` is the existing `list[JbdBt]` in `main()`. Each `JbdBt` has a
`dev: BleakJbdDev` attribute; `last_read_time` lives on `BleakJbdDev` and is
accessed as `batt.dev.last_read_time`.

```
for each batt in batteries:
    if batt.dev.last_read_time == 0:
        skip (battery has never read — startup grace covered by BT_INIT_RETRY_INTERVAL)
    if monotonic() - batt.dev.last_read_time > BT_WATCHDOG_TIMEOUT:
        log error with batt.address and elapsed time
        mainloop.quit()
        return False  # stop timer
return True  # keep timer running
```

The watchdog only triggers after at least one successful read (`last_read_time
!= 0`), so it does not interfere with the existing startup retry mechanism.

runit restarts the service within a few seconds of `mainloop.quit()`.

---

## Config Summary

| Key | Default | Description |
|---|---|---|
| `BT_CONNECT_TIMEOUT` | `15` | Seconds to wait for BLE connection handshake |
| `BT_WATCHDOG_TIMEOUT` | `600` | Seconds since last successful read before service restart; `0` = disabled |

---

## Files Changed

| File | Change |
|---|---|
| `default_config.ini` | Add `BT_CONNECT_TIMEOUT`, `BT_WATCHDOG_TIMEOUT` |
| `utils.py` | Read new config vars |
| `jbdbt.py` | Pass `timeout=BT_CONNECT_TIMEOUT` to `client.connect()`; add `last_read_time` field; set on success |
| `dbus-btbattery.py` | Register watchdog GLib timer |

---

## What This Does Not Cover

- Restarting only the stuck battery's BLE loop (rejected: asyncio loop may itself be wedged)
- BlueZ daemon restart (out of scope; runit restart of the service is sufficient)
- Batteries that have never completed a read (handled by existing startup retry)
