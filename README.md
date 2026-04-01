# dbus-btbattery

This is a driver for VenusOS devices (originally tested on
Raspberry Pi running the VenusOS v2.92 image).

The driver will communicate with a Battery Management System (BMS)
via Bluetooth and publish this data to the VenusOS system.

**Requires Python 3.** BLE communication uses
[bleak](https://github.com/hbldh/bleak) (pure Python, no
compilation needed).

This project is a fork of Brad Cagle's
[dbus-btbattery](https://github.com/bradcagle/dbus-btbattery),
which is derived from Louis Van Der Walt's
[dbus-serialbattery](https://github.com/Louisvdw/dbus-serialbattery).
This fork adds parallel battery support, migrates from bluepy to
bleak, and targets Python 3 / VenusOS compatibility.

## Installation

### Cerbo GX (recommended — survives firmware upgrades)

`/opt/victronenergy/` is wiped on OTA firmware upgrades. Install to
`/data/` instead, which is the persistent partition.

SSH into your Cerbo GX, then:

**1. One-time system setup:**
```sh
/opt/victronenergy/swupdate-scripts/resize2fs.sh
opkg update
opkg install python3-pip git
pip3 install bleak
```

**2. Clone to `/data/` (persistent across firmware upgrades):**
```sh
cd /data
git clone https://github.com/pace551/dbus-btbattery.git
```

**3. Symlink into `/opt/victronenergy/`:**
```sh
ln -sf /data/dbus-btbattery /opt/victronenergy/dbus-btbattery
```

**4. Configure your batteries in `config.ini`:**
```sh
cp /data/dbus-btbattery/default_config.ini /data/dbus-btbattery/config.ini
vi /data/dbus-btbattery/config.ini
```

Set at minimum:
```ini
CONNECTION_MODE = parallel   # or single / series
BT_ADDRESSES = AA:BB:CC:DD:EE:FF,11:22:33:44:55:66
```

**5. Install the service:**
```sh
cd /data/dbus-btbattery && ./installservice.sh
```

**6. Add to `/data/rc.local` so the symlink and service survive firmware upgrades:**
```sh
cat >> /data/rc.local << 'EOF'

# dbus-btbattery
ln -sf /data/dbus-btbattery /opt/victronenergy/dbus-btbattery
if [ ! -d /opt/victronenergy/service/dbus-btbattery ]; then
    mkdir -p /opt/victronenergy/service/dbus-btbattery
    cp -a /data/dbus-btbattery/service/* /opt/victronenergy/service/dbus-btbattery/
fi
EOF
chmod +x /data/rc.local
```

**7. Reboot.**

---

### Raspberry Pi 4 (testing)

On RPi4 the firmware is updated by reflashing the SD card, so
persistence is less of a concern. Clone directly to `/opt/victronenergy/`:

1. SSH to IP assigned to venus device
2. Resize/Expand file system:
   `/opt/victronenergy/swupdate-scripts/resize2fs.sh`
3. Update opkg:
   `opkg update`
4. Install pip:
   `opkg install python3-pip`
5. Install bleak (pure Python BLE library):
   `pip3 install bleak`
6. Install git:
   `opkg install git`
7. Clone dbus-btbattery repo:

```sh
cd /opt/victronenergy/
git clone https://github.com/pace551/dbus-btbattery.git
```

Configure `config.ini` as above, then:

```sh
cd /opt/victronenergy/dbus-btbattery && ./installservice.sh
```

Reboot.

You can run `./scan.py` to find Bluetooth devices around you.

## To make dbus-btbattery startup automatically

Configure `config.ini` with your MAC addresses and connection mode,
then run `./installservice.sh` and reboot. The service reads
`config.ini` automatically — no need to edit `service/run` directly.

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
