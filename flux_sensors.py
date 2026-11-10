"""Temperature discovery by hardware identity, independent of USB access."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import subprocess
import tomllib

HWMON_ROOT = Path('/sys/class/hwmon')
CPU_DRIVERS = ('k10temp', 'coretemp', 'zenpower')
GPU_DRIVERS = ('nvidia', 'amdgpu', 'i915', 'xe')


@dataclass(frozen=True)
class SensorConfig:
    driver: str = 'auto'
    pci_address: str | None = None
    label: str | None = None

    def __post_init__(self):
        if not isinstance(self.driver, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', self.driver):
            raise ValueError('sensor driver must be a hwmon driver name or auto')
        if self.pci_address is not None:
            if not isinstance(self.pci_address, str) or not re.fullmatch(
                    r'(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]', self.pci_address):
                raise ValueError('pci_address must look like 0000:03:00.0')
            address = self.pci_address.lower()
            object.__setattr__(self, 'pci_address', address if address.count(':') == 2 else '0000:' + address)
        if self.label is not None and (not isinstance(self.label, str) or not self.label.strip()):
            raise ValueError('sensor label must be a nonempty string')


@dataclass(frozen=True)
class Settings:
    cpu: SensorConfig = field(default_factory=SensorConfig)
    gpu: SensorConfig = field(default_factory=SensorConfig)
    interval: float = 1.0
    reconnect_interval: float = 2.0

    def __post_init__(self):
        for name in ('interval', 'reconnect_interval'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.25 <= value <= 60:
                raise ValueError(f'display.{name} must be between 0.25 and 60 seconds')


@dataclass(frozen=True)
class Reading:
    temperature: float | None
    source: str | None = None
    error: str | None = None


def load_settings(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    with path.open('rb') as stream:
        data = tomllib.load(stream)
    unknown = data.keys() - {'cpu', 'gpu', 'display'}
    if unknown:
        raise ValueError(f'Unknown configuration sections: {sorted(unknown)}')
    try:
        return Settings(cpu=SensorConfig(**data.get('cpu', {})),
                        gpu=SensorConfig(**data.get('gpu', {})), **data.get('display', {}))
    except TypeError as error:
        raise ValueError(f'Invalid configuration: {error}') from error


def hwmon_devices(root: Path = HWMON_ROOT):
    for hwmon in sorted(root.glob('hwmon*')):
        try:
            yield hwmon, (hwmon / 'name').read_text().strip(), (hwmon / 'device').resolve().name.lower()
        except OSError:
            continue


def sensor_inputs(hwmon: Path):
    for path in sorted(hwmon.glob('temp*_input')):
        label_file = path.with_name(path.name.replace('_input', '_label'))
        try:
            label = label_file.read_text().strip() if label_file.exists() else path.name
        except OSError:
            continue
        yield path, label


def read_input(path: Path) -> Reading:
    try:
        value = int(path.read_text().strip()) / 1000
    except (OSError, ValueError) as error:
        return Reading(None, str(path), str(error))
    if value <= 0:
        return Reading(None, str(path), 'sensor returned a nonpositive temperature')
    return Reading(value, str(path))


def nvidia_reading(pci_address: str | None = None) -> Reading:
    command = ['/usr/bin/nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits']
    if pci_address:
        command += ['--id=' + pci_address]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=True, timeout=3)
        rows = result.stdout.strip().splitlines()
        if len(rows) != 1:
            return Reading(None, 'nvidia-smi', 'multiple NVIDIA GPUs; configure gpu.pci_address')
        value = float(rows[0])
        if not math.isfinite(value) or value <= 0:
            raise ValueError('invalid NVIDIA temperature')
        return Reading(value, 'nvidia-smi' + (':' + pci_address if pci_address else ''))
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return Reading(None, 'nvidia-smi', str(error))


def read_sensor(config: SensorConfig, role: str, root: Path = HWMON_ROOT) -> Reading:
    drivers = CPU_DRIVERS if role == 'cpu' else GPU_DRIVERS
    if config.driver != 'auto':
        drivers = (config.driver,)
    devices = list(hwmon_devices(root))
    for driver in drivers:
        matches = [(h, pci) for h, name, pci in devices if name == driver and
                   (config.pci_address is None or pci == config.pci_address)]
        if len(matches) > 1:
            return Reading(None, error=f'multiple {driver} devices; configure {role}.pci_address')
        if not matches:
            continue
        hwmon, _ = matches[0]
        inputs = list(sensor_inputs(hwmon))
        if config.label is not None:
            selected = [p for p, label in inputs if label.casefold() == config.label.casefold()]
            if len(selected) != 1:
                return Reading(None, str(hwmon), f'label {config.label!r} not uniquely available')
            return read_input(selected[0])
        preferred = ('tctl', 'package id 0', 'tdie') if role == 'cpu' else ('edge', 'gpu', 'core')
        for term in preferred:
            for path, label in inputs:
                if label.casefold() == term:
                    return read_input(path)
        default = hwmon / 'temp1_input'
        if default.exists():
            return read_input(default)
        return Reading(None, str(hwmon), 'no default temperature input; configure a label')
    if role == 'gpu' and config.driver in ('auto', 'nvidia') and config.label is None:
        return nvidia_reading(config.pci_address)
    return Reading(None, error=f'no matching {role} hwmon sensor')


def list_sensors(root: Path = HWMON_ROOT) -> list[dict]:
    return [dict(driver=name, pci_address=pci, label=label, path=str(path),
                 temperature=read_input(path).temperature)
            for hwmon, name, pci in hwmon_devices(root)
            for path, label in sensor_inputs(hwmon)]
