#!/usr/bin/env python3
import asyncio
from bleak import BleakScanner


async def main():
    print("Scanning for 10 seconds...")
    devices = await BleakScanner.discover(timeout=10.0)
    for dev in sorted(devices, key=lambda d: d.rssi, reverse=True):
        print(f"Device {dev.address}, RSSI={dev.rssi} dB, Name={dev.name or '(unknown)'}")


asyncio.run(main())
