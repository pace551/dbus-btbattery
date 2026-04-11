"""Pytest configuration and fixtures for dbus-btbattery tests."""

import sys
from unittest.mock import MagicMock

# Stub the bleak module which requires BLE hardware unavailable in CI.
if 'bleak' not in sys.modules:
    _bleak_stub = MagicMock()
    _bleak_stub.BleakClient = MagicMock
    _bleak_stub.BleakError = Exception
    sys.modules['bleak'] = _bleak_stub
