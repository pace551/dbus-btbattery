"""
Tests for dbus_btbattery_cli.parse_args().

Run standalone:
    python3 tests/test_cli.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dbus_btbattery_cli import parse_args


def run_parse(argv):
	"""Helper: set sys.argv and call parse_args()."""
	sys.argv = ['dbus-btbattery.py'] + argv
	return parse_args()


def test_single_address():
	args = run_parse(['AA:BB:CC:DD:EE:FF'])
	assert args.addresses == ['AA:BB:CC:DD:EE:FF'], f"Expected one address, got {args.addresses}"
	assert args.mode == 'single', f"Expected mode='single', got {args.mode!r}"
	print("PASS test_single_address")


def test_parallel_flag():
	args = run_parse(['--parallel', 'AA:BB:CC:DD:EE:FF', '11:22:33:44:55:66'])
	assert args.mode == 'parallel', f"Expected mode='parallel', got {args.mode!r}"
	assert len(args.addresses) == 2, f"Expected 2 addresses, got {args.addresses}"
	print("PASS test_parallel_flag")


def test_series_flag():
	args = run_parse(['--series', 'AA:BB:CC:DD:EE:FF', '11:22:33:44:55:66'])
	assert args.mode == 'series', f"Expected mode='series', got {args.mode!r}"
	assert len(args.addresses) == 2, f"Expected 2 addresses, got {args.addresses}"
	print("PASS test_series_flag")


def test_legacy_multi_address_defaults_to_series():
	"""Multiple addresses with no mode flag → series (backwards compat)."""
	args = run_parse(['AA:BB:CC:DD:EE:FF', '11:22:33:44:55:66'])
	assert args.mode == 'series', f"Expected mode='series' (legacy), got {args.mode!r}"
	assert len(args.addresses) == 2, f"Expected 2 addresses, got {args.addresses}"
	print("PASS test_legacy_multi_address_defaults_to_series")


def test_timing_cli_overrides():
	"""CLI timing args override config defaults."""
	args = run_parse([
		'AA:BB:CC:DD:EE:FF',
		'--bt-poll-interval', '60',
		'--dbus-poll-interval', '1000',
	])
	assert args.bt_poll_interval == 60, f"Expected bt_poll_interval=60, got {args.bt_poll_interval}"
	assert args.dbus_poll_interval == 1000, f"Expected dbus_poll_interval=1000, got {args.dbus_poll_interval}"
	print("PASS test_timing_cli_overrides")


if __name__ == '__main__':
	test_single_address()
	test_parallel_flag()
	test_series_flag()
	test_legacy_multi_address_defaults_to_series()
	test_timing_cli_overrides()
	print("\nAll CLI tests passed")
