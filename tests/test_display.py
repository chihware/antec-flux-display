import contextlib
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import flux_sensors as sensors

spec = importlib.util.spec_from_file_location('display', ROOT / 'antec-flux-display.py')
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def device(self, number, driver, pci, inputs):
        path = self.root / f'hwmon{number}'
        path.mkdir()
        (path / 'name').write_text(driver)
        device = self.root / 'devices' / pci
        device.mkdir(parents=True, exist_ok=True)
        (path / 'device').symlink_to(device, target_is_directory=True)
        for index, (label, value) in enumerate(inputs, 1):
            (path / f'temp{index}_label').write_text(label)
            (path / f'temp{index}_input').write_text(str(value))
        return path

    def test_pci_selection_ignores_earlier_igpu_and_survives_renumbering(self):
        self.device(0, 'amdgpu', '0000:7b:00.0', [('edge', 42000)])
        discrete = self.device(1, 'amdgpu', '0000:03:00.0', [('edge', 45000), ('junction', 57000)])
        config = sensors.SensorConfig('amdgpu', '03:00.0', 'junction')
        self.assertEqual(sensors.read_sensor(config, 'gpu', self.root).temperature, 57)
        discrete.rename(self.root / 'hwmon99')
        self.assertEqual(sensors.read_sensor(config, 'gpu', self.root).temperature, 57)

    def test_ambiguous_gpu_is_not_silently_guessed(self):
        self.device(1, 'amdgpu', '0000:03:00.0', [('edge', 45000)])
        self.device(2, 'amdgpu', '0000:7b:00.0', [('edge', 42000)])
        result = sensors.read_sensor(sensors.SensorConfig('amdgpu'), 'gpu', self.root)
        self.assertIsNone(result.temperature)
        self.assertIn('pci_address', result.error)

    def test_explicit_label_does_not_fall_back_to_edge(self):
        self.device(1, 'amdgpu', '0000:03:00.0', [('edge', 45000)])
        result = sensors.read_sensor(sensors.SensorConfig('amdgpu', label='junction'), 'gpu', self.root)
        self.assertIsNone(result.temperature)
        self.assertIn('junction', result.error)

    def test_tctl_is_selected_instead_of_first_alphabetic_ccd_label(self):
        self.device(5, 'k10temp', '0000:00:18.3', [('Tccd1', 39000), ('Tctl', 51000)])
        self.assertEqual(sensors.read_sensor(sensors.SensorConfig(), 'cpu', self.root).temperature, 51)

    def test_explicit_amd_missing_does_not_query_nvidia(self):
        with patch.object(sensors.subprocess, 'run', side_effect=AssertionError('wrong GPU backend')):
            self.assertIsNone(sensors.read_sensor(sensors.SensorConfig('amdgpu'), 'gpu', self.root).temperature)

    def test_bad_value_and_missing_input_are_unavailable(self):
        path = self.device(1, 'amdgpu', '0000:03:00.0', [('edge', 0)])
        config = sensors.SensorConfig('amdgpu')
        self.assertIsNone(sensors.read_sensor(config, 'gpu', self.root).temperature)
        (path / 'temp1_input').write_text('not-a-number')
        self.assertIsNone(sensors.read_sensor(config, 'gpu', self.root).temperature)
        (path / 'temp1_input').unlink()
        self.assertIsNone(sensors.read_sensor(config, 'gpu', self.root).temperature)

    def test_config_rejects_typos_and_unsafe_intervals(self):
        path = self.root / 'config.toml'
        for text in ['[gpou]\ndriver="amdgpu"', '[gpu]\npcie_address="03:00.0"',
                     '[display]\ninterval=0', '[display]\ninterval=nan', '[display]\ninterval=true']:
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    sensors.load_settings(path)

    def test_config_validates_pci_and_loads_labels(self):
        path = self.root / 'config.toml'
        path.write_text('[cpu]\nlabel="Tctl"\n[gpu]\ndriver="amdgpu"\npci_address="03:00.0"\nlabel="junction"\n')
        config = sensors.load_settings(path)
        self.assertEqual(config.gpu.pci_address, '0000:03:00.0')
        self.assertEqual(config.cpu.label, 'Tctl')
        with self.assertRaises(ValueError):
            sensors.SensorConfig(pci_address='../../etc/passwd')

    def test_nvidia_multiple_devices_require_identity(self):
        result = subprocess.CompletedProcess([], 0, '40\n50\n', '')
        with patch.object(sensors.subprocess, 'run', return_value=result) as run:
            self.assertIsNone(sensors.nvidia_reading().temperature)
            result.stdout = '50\n'
            self.assertEqual(sensors.nvidia_reading('0000:04:00.0').temperature, 50)
            self.assertIn('--id=0000:04:00.0', run.call_args.args[0])


class PacketTests(unittest.TestCase):
    def test_known_packet_and_checksum(self):
        packet = display.build_packet(45.6, 52.0)
        self.assertEqual(packet[:11], bytes.fromhex('55 aa 01 01 06 04 05 06 05 02 00'))
        self.assertEqual(len(packet), 12)
        self.assertEqual(packet[-1], sum(packet[:-1]) % 256)

    def test_missing_and_out_of_range_never_become_plausible_numbers(self):
        for value in (None, 0, -10, 100, 110, 99.96, 1e308, float('nan'), float('inf')):
            with self.subTest(value=value):
                self.assertEqual(display.encode_temperature(value), [0xEE] * 3)
        self.assertEqual(display.encode_temperature(99.9), [9, 9, 9])

    def test_dry_run_never_opens_usb_and_prints_selected_sources(self):
        values = (sensors.Reading(45, 'cpu-path'), sensors.Reading(52, 'gpu-junction-path'))
        with patch.object(display, 'sample', return_value=values), \
             patch.object(display, 'open_display', side_effect=AssertionError('USB opened')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(display.main(['--dry-run']), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['gpu']['source'], 'gpu-junction-path')
        self.assertEqual(result['packet'], display.build_packet(45, 52).hex(' '))


class USBTests(unittest.TestCase):
    class USBError(Exception):
        def __init__(self, message='USB failure', errno=None):
            super().__init__(message)
            self.errno = errno

    def setUp(self):
        self.fake = SimpleNamespace(core=SimpleNamespace(USBError=self.USBError, find=Mock()),
                                    util=SimpleNamespace(release_interface=Mock(), dispose_resources=Mock(), claim_interface=Mock()))
        self.usb_patch = patch.object(display, 'usb', self.fake)
        self.usb_patch.start()
        self.addCleanup(self.usb_patch.stop)
        signals = patch.object(display.signal, 'signal')
        signals.start()
        self.addCleanup(signals.stop)
        self.readings = (sensors.Reading(45, 'cpu'), sensors.Reading(52, 'gpu'))

    def test_once_sends_exact_packet_and_releases_interface(self):
        device = Mock()
        device.write.return_value = 12
        device._flux_detached = False
        with patch.object(display, 'sample', return_value=self.readings), patch.object(display, 'open_display', return_value=device):
            self.assertEqual(display.run_display(sensors.Settings(), once=True), 0)
        device.write.assert_called_once_with(3, display.build_packet(45, 52), timeout=1000)
        self.fake.util.release_interface.assert_called_once_with(device, 0)

    def test_missing_temperature_prevents_one_shot_usb_write(self):
        with patch.object(display, 'sample', return_value=(sensors.Reading(None), self.readings[1])), patch.object(display, 'open_display') as open_device:
            self.assertEqual(display.run_display(sensors.Settings(), once=True), 1)
        open_device.assert_not_called()

    def test_usb_permission_error_is_not_reported_as_success(self):
        with patch.object(display, 'sample', return_value=self.readings), patch.object(display, 'open_display', side_effect=self.USBError('permission denied', 13)):
            self.assertEqual(display.run_display(sensors.Settings(), once=True), 1)

    def test_short_write_releases_and_reports_failure(self):
        device = Mock()
        device.write.return_value = 3
        device._flux_detached = False
        with patch.object(display, 'sample', return_value=self.readings), patch.object(display, 'open_display', return_value=device):
            self.assertEqual(display.run_display(sensors.Settings(), once=True), 1)
        self.fake.util.dispose_resources.assert_called_once_with(device)

    def test_disconnect_reconnect_uses_fresh_device(self):
        first, second = Mock(), Mock()
        first._flux_detached = second._flux_detached = False
        first.write.side_effect = self.USBError('disconnected', 19)
        second.write.return_value = 12
        stop = {}
        def save_handler(signum, handler):
            stop[signum] = handler
        def sleep(_seconds):
            if second.write.called:
                stop[signal.SIGTERM](signal.SIGTERM, None)
        with patch.object(display, 'sample', return_value=self.readings), \
             patch.object(display, 'open_display', side_effect=[first, second]) as opened, \
             patch.object(display.signal, 'signal', side_effect=save_handler), \
             patch.object(display.time, 'sleep', side_effect=sleep):
            self.assertEqual(display.run_display(sensors.Settings()), 0)
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(self.fake.util.dispose_resources.call_count, 2)
        second.write.assert_called_once()

    def test_unexpected_endpoint_is_not_written_and_kernel_driver_is_restored(self):
        device = Mock()
        device.is_kernel_driver_active.return_value = True
        device.get_active_configuration.return_value = {(0, 0): [SimpleNamespace(bEndpointAddress=4)]}
        self.fake.core.find.return_value = device
        with self.assertRaisesRegex(ValueError, 'endpoint'):
            display.open_display()
        device.write.assert_not_called()
        device.attach_kernel_driver.assert_called_once_with(0)


if __name__ == '__main__':
    unittest.main()
