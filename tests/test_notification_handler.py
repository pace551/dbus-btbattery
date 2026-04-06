"""
Tests for BleakJbdDev notification handler fixes.

Run: python -m pytest tests/test_notification_handler.py -v
"""

import sys
import os
import logging
import time
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Stub only the bleak module which requires BLE hardware unavailable in CI.
# Do NOT stub battery/utils — keep real implementations so other test modules
# are not contaminated.
if 'bleak' not in sys.modules:
    _bleak_stub = MagicMock()
    _bleak_stub.BleakClient = MagicMock
    _bleak_stub.BleakError = Exception
    sys.modules['bleak'] = _bleak_stub

import jbdbt as _jbdbt_mod
# Provide module-level names that utils exports via `from utils import *`
# and that jbdbt references at module/class level.
_jbdbt_mod.BT_POLL_INTERVAL = 30
_jbdbt_mod.logger = logging.getLogger('jbdbt_test')

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
# Event signaling: notification handler sets asyncio events on completion
# ---------------------------------------------------------------------------

def test_cell_event_set_on_complete_packet():
    """_cell_event should be set when a complete cell data packet is received."""
    import asyncio
    dev = make_dev()
    dev._cell_event = asyncio.Event()

    payload = bytes(8)
    packet = build_packet(0x04, payload)
    dev._notification_handler(None, packet)

    assert dev._cell_event.is_set(), "_cell_event should be set after complete cell packet"


def test_general_event_set_on_complete_packet():
    """_general_event should be set when a complete general data packet is received."""
    import asyncio
    dev = make_dev()
    dev._general_event = asyncio.Event()

    payload = bytearray(27)
    packet = build_packet(0x03, bytes(payload))
    dev._notification_handler(None, packet)

    assert dev._general_event.is_set(), "_general_event should be set after complete general packet"


def test_events_none_by_default():
    """Events should be None on a fresh device (set only at start of each read cycle)."""
    dev = make_dev()
    assert dev._general_event is None
    assert dev._cell_event is None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
