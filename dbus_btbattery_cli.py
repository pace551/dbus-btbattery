import argparse
import utils


def parse_args():
	parser = argparse.ArgumentParser(
		description='dbus-btbattery: Bluetooth BMS driver for VenusOS'
	)

	mode_group = parser.add_mutually_exclusive_group()
	mode_group.add_argument('--parallel', action='store_true',
							help='Parallel battery mode')
	mode_group.add_argument('--series', action='store_true',
							help='Series battery mode')

	parser.add_argument('addresses', nargs='*', default=[],
						help='Bluetooth MAC addresses')

	parser.add_argument('--bt-poll-interval', type=int, default=None,
						help='BLE poll interval in seconds')
	parser.add_argument('--bt-watchdog-timer', type=int, default=None,
						help='BT watchdog timer in seconds, 0 to disable')
	parser.add_argument('--dbus-poll-interval', type=int, default=None,
						help='D-Bus publish interval in milliseconds')

	args = parser.parse_args()

	# Resolve addresses: CLI args override config.ini
	if not args.addresses and utils.BT_ADDRESSES:
		args.addresses = utils.BT_ADDRESSES

	# Resolve mode
	if args.parallel:
		args.mode = 'parallel'
	elif args.series:
		args.mode = 'series'
	elif len(args.addresses) > 1:
		args.mode = 'series'  # legacy backwards compat
	else:
		args.mode = utils.CONNECTION_MODE if utils.CONNECTION_MODE != 'single' else 'single'

	# Resolve timing: CLI overrides config.ini
	if args.bt_poll_interval is None:
		args.bt_poll_interval = utils.BT_POLL_INTERVAL
	if args.bt_watchdog_timer is None:
		args.bt_watchdog_timer = utils.BT_WATCHDOG_TIMER
	if args.dbus_poll_interval is None:
		args.dbus_poll_interval = utils.DBUS_POLL_INTERVAL

	return args
