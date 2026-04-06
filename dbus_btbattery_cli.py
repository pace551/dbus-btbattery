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
						help='BLE poll interval in seconds (connect-read-disconnect cycle)')
	parser.add_argument('--dbus-poll-interval', type=int, default=None,
						help='D-Bus publish interval in milliseconds')

	args = parser.parse_args()

	# Resolve addresses: CLI args override config.ini
	if not args.addresses and utils.BT_ADDRESSES:
		args.addresses = utils.BT_ADDRESSES

	# Resolve mode: CLI flags > config.ini > legacy fallback
	if args.parallel:
		args.mode = 'parallel'
	elif args.series:
		args.mode = 'series'
	elif utils.CONNECTION_MODE in ('parallel', 'series'):
		args.mode = utils.CONNECTION_MODE
	elif len(args.addresses) > 1:
		args.mode = 'series'  # legacy: multiple addresses without flag
	else:
		args.mode = 'single'

	# Resolve timing: CLI overrides config.ini
	if args.bt_poll_interval is None:
		args.bt_poll_interval = utils.BT_POLL_INTERVAL
	if args.dbus_poll_interval is None:
		args.dbus_poll_interval = utils.DBUS_POLL_INTERVAL

	return args
