"""Tests for BleakJbdDev.shutdown() and _current_client tracking.

shutdown() gracefully disconnects any active BleakClient before the process
exits, preventing BlueZ from accumulating stale GATT operations across
unclean btbattery restart cycles.
"""

import asyncio
import os
import sys
import threading
import time
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import jbdbt
from jbdbt import BleakJbdDev


def _run_loop_in_thread():
    """Start a real asyncio loop in a background thread and install it as
    jbdbt._ble_loop. Returns (loop, thread, cleanup_fn)."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    async def _make_lock():
        return asyncio.Lock()

    connect_lock = asyncio.run_coroutine_threadsafe(_make_lock(), loop).result(timeout=1)

    orig_loop = jbdbt._ble_loop
    orig_lock = jbdbt._ble_connect_lock
    jbdbt._ble_loop = loop
    jbdbt._ble_connect_lock = connect_lock

    def cleanup():
        # Cancel any tasks that are still pending (e.g. a hung disconnect mock)
        # so we don't get "Task was destroyed but it is pending" warnings.
        def _cancel_all():
            for task in asyncio.all_tasks(loop):
                task.cancel()
        loop.call_soon_threadsafe(_cancel_all)
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        jbdbt._ble_loop = orig_loop
        jbdbt._ble_connect_lock = orig_lock

    return loop, cleanup


def test_current_client_initialised_to_none():
    dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")
    assert dev._current_client is None


def test_shutdown_sets_running_false():
    dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")
    dev.running = True
    dev.shutdown(timeout=0.1)
    assert dev.running is False


def test_shutdown_noop_when_no_current_client():
    """shutdown() must not raise when no BLE cycle is active."""
    dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")
    dev.running = True
    assert dev._current_client is None
    dev.shutdown(timeout=0.1)
    assert dev.running is False


def test_shutdown_noop_when_loop_never_started():
    """shutdown() must return promptly if the BLE event loop was never started
    (e.g. connect() was never called on this device)."""
    dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")
    dev._current_client = MagicMock(is_connected=True)
    dev.running = True

    # Ensure no BLE loop is running for this test.
    orig_loop = jbdbt._ble_loop
    jbdbt._ble_loop = None
    try:
        start = time.monotonic()
        dev.shutdown(timeout=0.5)
        elapsed = time.monotonic() - start
        assert elapsed < 0.2, f"shutdown() took {elapsed:.3f}s when loop is None"
        assert dev.running is False
    finally:
        jbdbt._ble_loop = orig_loop


def test_shutdown_disconnects_active_client():
    """When a client is active and the BLE loop is running, shutdown() must
    schedule client.disconnect() and wait for it to complete."""
    loop, cleanup = _run_loop_in_thread()
    try:
        dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")

        disconnect_called = threading.Event()

        async def fake_disconnect():
            disconnect_called.set()

        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.disconnect = fake_disconnect
        dev._current_client = mock_client
        dev.running = True

        dev.shutdown(timeout=3.0)

        assert dev.running is False
        assert disconnect_called.is_set(), "disconnect() was not called"
    finally:
        cleanup()


def test_shutdown_skips_disconnect_when_client_not_connected():
    """If client.is_connected is False, we should not call disconnect()."""
    loop, cleanup = _run_loop_in_thread()
    try:
        dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")

        disconnect_called = threading.Event()

        async def fake_disconnect():
            disconnect_called.set()

        mock_client = MagicMock()
        mock_client.is_connected = False
        mock_client.disconnect = fake_disconnect
        dev._current_client = mock_client

        dev.shutdown(timeout=1.0)

        assert not disconnect_called.is_set(), "disconnect() should be skipped when not connected"
    finally:
        cleanup()


def test_shutdown_honours_timeout_when_disconnect_hangs():
    """A hanging disconnect() must not block shutdown() longer than the timeout."""
    loop, cleanup = _run_loop_in_thread()
    try:
        dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")

        async def hang_forever():
            await asyncio.sleep(10)

        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.disconnect = hang_forever
        dev._current_client = mock_client

        start = time.monotonic()
        dev.shutdown(timeout=0.5)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"shutdown() took {elapsed:.3f}s, timeout was 0.5s"
    finally:
        cleanup()


def test_shutdown_swallows_disconnect_exception():
    """shutdown() must not propagate exceptions from disconnect()."""
    loop, cleanup = _run_loop_in_thread()
    try:
        dev = BleakJbdDev("AA:BB:CC:DD:EE:FF")

        async def raise_error():
            raise RuntimeError("BlueZ is angry")

        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.disconnect = raise_error
        dev._current_client = mock_client

        # Must not raise.
        dev.shutdown(timeout=1.0)
    finally:
        cleanup()
