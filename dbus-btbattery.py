#!/usr/bin/python
# -*- coding: utf-8 -*-

from dbus.mainloop.glib import DBusGMainLoop
from threading import Thread
import sys

if sys.version_info.major == 2:
	import gobject
else:
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
	logger.info(
		"dbus-btbattery v" + str(utils.DRIVER_VERSION) + utils.DRIVER_SUBVERSION
	)

	args = parse_args()

	if not args.addresses:
		logger.error("ERROR >>> No Bluetooth addresses provided")
		sys.exit(1)

	# Apply timing overrides to utils module
	utils.BT_POLL_INTERVAL = args.bt_poll_interval
	utils.BT_WATCHDOG_TIMER = args.bt_watchdog_timer
	utils.DBUS_POLL_INTERVAL = args.dbus_poll_interval

	# Apply timing overrides - must set on both utils and jbdbt modules
	# because jbdbt uses 'from utils import *' (copies at import time)
	jbdbt.BT_POLL_INTERVAL = args.bt_poll_interval
	jbdbt.BT_WATCHDOG_TIMER = args.bt_watchdog_timer

	# Create JbdBt instances
	batteries = [JbdBt(addr) for addr in args.addresses]

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

	DBusGMainLoop(set_as_default=True)
	if sys.version_info.major == 2:
		gobject.threads_init()
	mainloop = gobject.MainLoop()

	for helper in helpers:
		if not helper.setup_vedbus():
			logger.error("ERROR >>> Problem setting up vedbus for " + str(helper.battery.port))
			sys.exit(1)

	def poll_all_batteries(loop):
		for helper in helpers:
			poller = Thread(target=lambda h=helper: h.publish_battery(loop))
			poller.daemon = True
			poller.start()
		return True

	gobject.timeout_add(args.dbus_poll_interval, lambda: poll_all_batteries(mainloop))
	try:
		mainloop.run()
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	main()
