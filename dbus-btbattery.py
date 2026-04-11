#!/usr/bin/python
# -*- coding: utf-8 -*-

from dbus.mainloop.glib import DBusGMainLoop
import sys
import time

from gi.repository import GLib as gobject

from dbushelper import DbusHelper
from utils import logger
import utils
from jbdbt import JbdBt
import jbdbt
from serial import SeriesBattery
from parallel import ParallelBattery
from dbus_btbattery_cli import parse_args


logger.info("Starting dbus-btbattery")


def main():
	# Must be called before any D-Bus connections are made
	DBusGMainLoop(set_as_default=True)

	logger.info(
		"dbus-btbattery v" + str(utils.DRIVER_VERSION) + utils.DRIVER_SUBVERSION
	)

	args = parse_args()

	if not args.addresses:
		logger.error("ERROR >>> No Bluetooth addresses provided")
		sys.exit(1)

	# Apply timing overrides to utils module
	utils.BT_POLL_INTERVAL = args.bt_poll_interval
	utils.DBUS_POLL_INTERVAL = args.dbus_poll_interval

	# Apply timing override to jbdbt module — jbdbt uses 'from utils import *'
	# which copies at import time, so the module-level var must be set directly.
	jbdbt.BT_POLL_INTERVAL = args.bt_poll_interval

	# Create JbdBt instances with staggered initial delays so batteries
	# connect at evenly spaced intervals rather than all at once.
	batteries = [
		JbdBt(addr, initial_delay=i * utils.BT_CONNECT_STAGGER)
		for i, addr in enumerate(args.addresses)
	]

	helpers = []

	if args.mode == 'parallel':
		# Register aggregate first to get lowest instance number
		aggregate = ParallelBattery(batteries)
		aggregate.log_settings()
		helpers.append(DbusHelper(aggregate))
		# Then register individual batteries
		for batt in batteries:
			batt.log_settings()
			helpers.append(DbusHelper(batt))
		logger.info(f"Parallel mode: {len(batteries)} individual + 1 aggregate = {len(helpers)} D-Bus services")

	elif args.mode == 'series':
		aggregate = SeriesBattery(batteries)
		aggregate.log_settings()
		helpers.append(DbusHelper(aggregate))
		logger.info(f"Series mode: {len(batteries)} batteries combined into 1 D-Bus service")

	else:
		batt = batteries[0]
		batt.log_settings()
		helpers.append(DbusHelper(batt))
		logger.info("Single battery mode")

	mainloop = gobject.MainLoop()

	if utils.BT_WATCHDOG_TIMEOUT > 0:
		def watchdog():
			now = time.monotonic()
			for batt in batteries:
				if batt._ble_dev.last_read_time == 0.0:
					continue
				elapsed = now - batt._ble_dev.last_read_time
				if elapsed > utils.BT_WATCHDOG_TIMEOUT:
					logger.error(
						"Watchdog: %s has not completed a read in %.0fs, restarting",
						batt._ble_dev.address,
						elapsed,
					)
					mainloop.quit()
					return False
			return True

		gobject.timeout_add(60_000, watchdog)

	active_helpers = []
	pending = []  # list of [JbdBt_instance, retry_count]

	for helper in helpers:
		if helper.setup_vedbus():
			active_helpers.append(helper)
		else:
			logger.warning(
				"Battery %s failed initial setup, will retry every %ds (max %d retries, 0=indefinite)",
				helper.battery.port,
				utils.BT_INIT_RETRY_INTERVAL,
				utils.BT_INIT_MAX_RETRIES,
			)
			pending.append([helper.battery, 0])

	def poll_all_batteries(loop):
		for helper in active_helpers:
			try:
				helper.publish_battery(loop)
			except Exception:
				logger.error(
					"Unhandled exception in publish_battery for %s",
					helper.battery.port,
					exc_info=True,
				)
		return True

	if pending:
		def retry_pending():
			still_pending = []
			for batt, retry_count in pending:
				new_helper = DbusHelper(batt)
				if new_helper.setup_vedbus():
					active_helpers.append(new_helper)
					logger.info(
						"Battery %s registered after %d retries",
						batt.port,
						retry_count + 1,
					)
				else:
					retry_count += 1
					if utils.BT_INIT_MAX_RETRIES > 0 and retry_count >= utils.BT_INIT_MAX_RETRIES:
						logger.error(
							"Battery %s: giving up after %d retries",
							batt.port,
							retry_count,
						)
					else:
						still_pending.append([batt, retry_count])
			pending.clear()
			pending.extend(still_pending)
			return len(pending) > 0  # False stops the GLib timer

		gobject.timeout_add(utils.BT_INIT_RETRY_INTERVAL * 1000, retry_pending)

	gobject.timeout_add(args.dbus_poll_interval, lambda: poll_all_batteries(mainloop))
	try:
		mainloop.run()
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	main()
