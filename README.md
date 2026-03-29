# dbus-btbattery

This is a driver for VenusOS devices (as of yet only tested on
Raspberry Pi running the VenusOS v2.92 image).

The driver will communicate with a Battery Management System (BMS)
via Bluetooth and publish this data to the VenusOS system.

This project is derived from Louis Van Der Walt's dbus-serialbattery
found here:
<https://github.com/Louisvdw/dbus-serialbattery>

## Instructions

To get started you need a VenusOS device. I've only tried on
Raspberry Pi, you can follow my instructions here:
<https://www.youtube.com/watch?v=yvGdNOZQ0Rw>
to set one up.

You need to setup some dependencies on your VenusOS first

1. SSH to IP assigned to venus device
2. Resize/Expand file system:
   `/opt/victronenergy/swupdate-scripts/resize2fs.sh`
3. Update opkg:
   `opkg update`
4. Install pip:
   `opkg install python3-pip`
5. Install build essentials as bluepy has some C code that needs
   to be compiled:
   `opkg install packagegroup-core-buildessential`
6. Install glib-dev required by bluepy:
   `opkg install libglib-2.0-dev`
7. Install bluepy:
   `pip3 install bluepy`
8. Install git:
   `opkg install git`
9. Clone dbus-btbattery repo:

```sh
cd /opt/victronenergy/
git clone https://github.com/bradcagle/dbus-btbattery.git
```

Then from the `dbus-btbattery` directory you can run:

```sh
./dbus-btbattery.py 70:3e:97:08:00:62
```

Replace `70:3e:97:08:00:62` with the Bluetooth address of your
BMS/Battery.

You can run `./scan.py` to find Bluetooth devices around you.

## To make dbus-btbattery startup automatically

1. Edit `service/run` and replace `70:3e:97:08:00:62` with the
   Bluetooth address of your BMS/Battery
2. Save with "Ctrl O"
3. Run `./installservice.sh`
4. Reboot

## Multi-Battery Modes

dbus-btbattery supports monitoring multiple batteries in
**series** or **parallel** configurations.

### Series Mode

Combines multiple batteries into a single virtual battery
(e.g., two 12V batteries showing as one 24V battery). Voltages
are summed, current is shared, and cell counts are combined.

```sh
./dbus-btbattery.py --series 70:3e:97:08:00:62 a4:c1:37:40:89:5e
```

### Parallel Mode

Registers each battery as its own D-Bus service **plus** an
aggregate service, so you can monitor per-battery SOC alongside
a combined system view. The aggregate averages voltage and SOC,
sums current and capacity, uses min cell voltages, max
temperatures, AND-gates FET states, and applies worst-case
protection status.

```sh
./dbus-btbattery.py --parallel 70:3e:97:08:00:62 a4:c1:37:40:89:5e
```

For backwards compatibility, passing multiple addresses without
a flag defaults to series mode.

## Configurable Timing

Polling intervals can be set via CLI flags or `config.ini`:

<!-- markdownlint-disable MD013 -->

| Setting              | CLI Flag               | Default | Description                          |
| -------------------- | ---------------------- | ------- | ------------------------------------ |
| `BT_POLL_INTERVAL`   | `--bt-poll-interval`   | 30      | BLE poll interval (seconds)          |
| `BT_WATCHDOG_TIMER`  | `--bt-watchdog-timer`  | 300     | BT watchdog timer (seconds, 0=off)   |
| `DBUS_POLL_INTERVAL` | `--dbus-poll-interval` | 5000    | D-Bus publish interval (milliseconds)|

<!-- markdownlint-enable MD013 -->

CLI flags override `config.ini` values. See `default_config.ini`
for all available settings including connection mode and BT
addresses.

## Configuration via config.ini

You can also configure multi-battery mode and addresses in
`config.ini` instead of the command line:

```ini
CONNECTION_MODE = parallel
BT_ADDRESSES = 70:3e:97:08:00:62,a4:c1:37:40:89:5e
BT_POLL_INTERVAL = 30
```

Then simply run:

```sh
./dbus-btbattery.py
```

NOTES: This driver is far from complete, so some things will
probably be broken. Also only JBD BMS is currently supported.
