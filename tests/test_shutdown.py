import selectors
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ShutdownTests(unittest.TestCase):
    # Value: protects=graceful SIGTERM exit and USB cleanup before systemd kills;
    # fails_when=long waits ignore SIGTERM or its handler deadlocks during wait setup;
    # why_new=mocked reconnect tests and ordinary signals miss the lock-held window;
    # seam=none; no_claim=real USB/kernel behavior or systemd execution; fake USB.
    def test_sigterm_interrupts_long_waits_and_cleans_up(self):
        child = textwrap.dedent('''
            import importlib.util
            import linecache
            import os
            from pathlib import Path
            import signal
            import sys
            import threading
            import time
            from types import SimpleNamespace
            from unittest.mock import Mock

            root = Path(sys.argv[1])
            mode = sys.argv[2]
            delivery = sys.argv[3]
            sys.path.insert(0, str(root))
            import flux_sensors as sensors
            spec = importlib.util.spec_from_file_location('display', root / 'antec-flux-display.py')
            display = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(display)

            class USBError(Exception):
                pass

            device = Mock()
            device._flux_detached = True
            device.attach_kernel_driver.side_effect = lambda _: print('RESTORED', flush=True)
            display.usb = SimpleNamespace(
                core=SimpleNamespace(USBError=USBError),
                util=SimpleNamespace(
                    release_interface=lambda *_: print('RELEASED', flush=True),
                    dispose_resources=lambda _: print('DISPOSED', flush=True)))
            display.sample = lambda _: (sensors.Reading(45), sensors.Reading(50))

            def open_display():
                if mode == 'missing':
                    print('READY', flush=True)
                    return None
                return device

            def write(*args, **kwargs):
                print('READY', flush=True)
                if mode == 'failed-write':
                    raise USBError('disconnected')
                return 12

            display.open_display = open_display
            device.write.side_effect = write
            fired = False

            def interrupt_wait(frame, event, arg):
                global fired
                if not fired:
                    locked_event_wait = (
                        event == 'line'
                        and frame.f_code is threading.Event.wait.__code__
                        and linecache.getline(frame.f_code.co_filename, frame.f_lineno).strip()
                        == 'signaled = self._flag')
                    native_sleep = event == 'c_call' and arg is time.sleep
                    if locked_event_wait or native_sleep:
                        fired = True
                        print('SIGNALLED', flush=True)
                        os.kill(os.getpid(), signal.SIGTERM)
                return interrupt_wait

            if delivery == 'wait-setup':
                # Trace the old Event's lock-held window; profile the lock-free
                # implementation immediately before its native sleep begins.
                sys.settrace(interrupt_wait)
                sys.setprofile(interrupt_wait)
            result = display.run_display(sensors.Settings(interval=60, reconnect_interval=60))
            print('EXIT', result, flush=True)
        ''')
        for mode, delivery in ((mode, delivery)
                               for mode in ('connected', 'missing', 'failed-write')
                               for delivery in ('external', 'wait-setup')):
            with self.subTest(mode=mode, delivery=delivery):
                process = subprocess.Popen(
                    [sys.executable, '-c', child, str(ROOT), mode, delivery],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                try:
                    with selectors.DefaultSelector() as ready:
                        ready.register(process.stdout, selectors.EVENT_READ)
                        # gstack-timing-ok: bound the child handshake by the service's
                        # TimeoutStopSec=5; a stalled test child must not hang the suite.
                        self.assertTrue(ready.select(timeout=5), 'child never became ready')
                    self.assertEqual(process.stdout.readline().strip(), b'READY')
                    if delivery == 'external':
                        process.send_signal(signal.SIGTERM)
                    try:
                        # gstack-timing-ok: both packaged and Fedora services specify
                        # TimeoutStopSec=5; cleanup must finish before that deadline.
                        output, errors = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.fail('SIGTERM did not stop the daemon within TimeoutStopSec=5')
                    self.assertEqual(process.returncode, 0, errors)
                    expected = [] if mode == 'missing' else ['RELEASED', 'RESTORED', 'DISPOSED']
                    if delivery == 'wait-setup':
                        # Failed writes clean up before entering their retry wait.
                        expected.insert(len(expected) if mode == 'failed-write' else 0, 'SIGNALLED')
                    self.assertEqual(output.decode().splitlines(), expected + ['EXIT 0'])
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()


if __name__ == '__main__':
    unittest.main()
