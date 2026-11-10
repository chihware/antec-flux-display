#!/usr/bin/env python3
"""Send Linux CPU/GPU temperatures to the Antec Flux Pro USB display."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import errno
import json
import logging
import math
from pathlib import Path
import signal
import time

from flux_sensors import load_settings, read_sensor, list_sensors

VENDOR_ID = 0x2022
PRODUCT_ID = 0x0522
INTERFACE_NUMBER = 0
ENDPOINT_OUT = 0x03
USB_WRITE_TIMEOUT_MS = 1000
DEFAULT_CONFIG = Path('/etc/antec-flux-display/config.toml')
LOG = logging.getLogger('antec-flux-display')
usb = None  # Diagnostics and tests do not require PyUSB or USB access.


def encode_temperature(temperature: float | None) -> list[int]:
    """Encode two integer digits and one decimal, or the missing-data marker.

    Never clamp an out-of-range temperature into a plausible displayed value.
    """
    if temperature is None or not math.isfinite(temperature) or not 0 < temperature < 100:
        return [0xEE] * 3
    tenths = round(temperature * 10)
    if tenths > 999:
        return [0xEE] * 3
    return [tenths // 100, (tenths // 10) % 10, tenths % 10]


def build_packet(cpu_temperature: float | None, gpu_temperature: float | None) -> bytes:
    payload = [0x55, 0xAA, 0x01, 0x01, 0x06]
    payload += encode_temperature(cpu_temperature) + encode_temperature(gpu_temperature)
    return bytes(payload + [sum(payload) & 0xFF])


def close_display(device):
    if device is None:
        return
    try:
        try:
            usb.util.release_interface(device, INTERFACE_NUMBER)
        except usb.core.USBError:
            pass
        if getattr(device, '_flux_detached', False):
            try:
                device.attach_kernel_driver(INTERFACE_NUMBER)
            except (usb.core.USBError, NotImplementedError):
                pass
    finally:
        usb.util.dispose_resources(device)


def open_display():
    device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if device is None:
        return None
    device._flux_detached = False
    try:
        try:
            if device.is_kernel_driver_active(INTERFACE_NUMBER):
                device.detach_kernel_driver(INTERFACE_NUMBER)
                device._flux_detached = True
        except NotImplementedError:
            pass
        try:
            device.set_configuration()
        except usb.core.USBError as error:
            if error.errno != errno.EBUSY:
                raise
        interface = device.get_active_configuration()[(INTERFACE_NUMBER, 0)]
        if not any(endpoint.bEndpointAddress == ENDPOINT_OUT for endpoint in interface):
            raise ValueError('Antec display does not expose the expected USB OUT endpoint 0x03')
        usb.util.claim_interface(device, INTERFACE_NUMBER)
        return device
    except BaseException:
        close_display(device)
        raise


def sample(settings):
    return read_sensor(settings.cpu, 'cpu'), read_sensor(settings.gpu, 'gpu')


def report_readings(readings, previous):
    for name, reading in zip(('CPU', 'GPU'), readings):
        status = (reading.source, reading.error,
                  reading.temperature is not None and encode_temperature(reading.temperature) == [0xEE] * 3)
        if previous.get(name) == status:
            continue
        previous[name] = status
        if reading.error:
            LOG.warning('%s unavailable: %s', name, reading.error)
        elif status[2]:
            LOG.warning('%s %.2f C cannot fit the display; showing --.-', name, reading.temperature)
        else:
            LOG.info('%s source: %s', name, reading.source)


def run_display(settings, once=False):
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    def wait(seconds):
        deadline = time.monotonic() + seconds
        while not stopped:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            # The signal handler only changes a flag; never acquire a lock there.
            time.sleep(min(remaining, 0.25))

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    device = None
    previous = {}
    try:
        while not stopped:
            readings = sample(settings)
            report_readings(readings, previous)
            if once and any(r.temperature is None or encode_temperature(r.temperature) == [0xEE] * 3 for r in readings):
                LOG.error('One-shot check failed; no USB write attempted')
                return 1
            try:
                if device is None:
                    device = open_display()
                    if device is None:
                        if once:
                            LOG.error('Antec display 2022:0522 not found')
                            return 1
                        if previous.get('usb') != 'missing':
                            LOG.warning('Antec display missing; waiting for USB connection')
                        previous['usb'] = 'missing'
                        wait(settings.reconnect_interval)
                        continue
                    previous['usb'] = 'connected'
                    LOG.info('Connected to Antec display 2022:0522')
                packet = build_packet(*(r.temperature for r in readings))
                written = device.write(ENDPOINT_OUT, packet, timeout=USB_WRITE_TIMEOUT_MS)
                if written != len(packet):
                    raise usb.core.USBError(f'short USB write: {written}/{len(packet)}')
                if once:
                    LOG.info('One packet accepted: CPU %.1f C; GPU %.1f C', *(r.temperature for r in readings))
                    return 0
            except (usb.core.USBError, ValueError) as error:
                LOG.warning('USB connection/write failed: %s', error)
                close_display(device)
                device = None
                if once:
                    return 1
                wait(settings.reconnect_interval)
                continue
            wait(settings.interval)
    finally:
        close_display(device)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='TOML config (default: /etc/antec-flux-display/config.toml if present)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--list-sensors', action='store_true', help='List sensor identities as JSON without USB access')
    mode.add_argument('--dry-run', action='store_true', help='Read sensors and print a packet without USB access')
    mode.add_argument('--once', action='store_true', help='Send one live reading and exit; fail if either sensor is unavailable')
    parser.add_argument('--gpu-pci', help='GPU PCI address, e.g. 0000:03:00.0')
    parser.add_argument('--gpu-sensor', help='Exact temperature label, e.g. edge or junction')
    parser.add_argument('--cpu-sensor', help='Exact CPU label, e.g. Tctl')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    if args.list_sensors:
        print(json.dumps(list_sensors(), indent=2))
        return 0
    try:
        config_path = args.config if args.config else (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
        settings = load_settings(config_path)
        if args.gpu_pci:
            settings = replace(settings, gpu=replace(settings.gpu, pci_address=args.gpu_pci))
        if args.gpu_sensor:
            settings = replace(settings, gpu=replace(settings.gpu, label=args.gpu_sensor))
        if args.cpu_sensor:
            settings = replace(settings, cpu=replace(settings.cpu, label=args.cpu_sensor))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.dry_run:
        readings = sample(settings)
        packet = build_packet(*(r.temperature for r in readings))
        print(json.dumps(dict(cpu=asdict(readings[0]), gpu=asdict(readings[1]), packet=packet.hex(' ')), indent=2))
        return int(any(r.temperature is None or encode_temperature(r.temperature) == [0xEE] * 3 for r in readings))
    global usb
    try:
        import usb.core
        import usb.util
    except ImportError:
        parser.error('PyUSB is required; install python-pyusb (Arch/CachyOS) or python3-pyusb (Fedora)')
    return run_display(settings, args.once)


if __name__ == '__main__':
    raise SystemExit(main())
