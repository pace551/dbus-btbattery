#!/usr/bin/env python3
import asyncio
from bleak import BleakScanner


async def main():
    print("Scanning for 10 seconds...")
    results = await BleakScanner.discover(timeout=10.0, return_adv=True)
    for dev, adv in sorted(results.values(), key=lambda x: x[1].rssi, reverse=True):
        print(f"Device {dev.address}, RSSI={adv.rssi} dB, Name={dev.name or '(unknown)'}")


asyncio.run(main())
