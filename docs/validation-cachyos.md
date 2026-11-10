# CachyOS hardware validation, 2026-11-10

Development fork: https://github.com/chihware/antec-flux-display

## Tested environment

- CachyOS rolling, Python 3.14.9, PyUSB 1.3.1, libusb 1.0.30.
- Ryzen 9 9950X: `k10temp`, label `Tctl`.
- RX 7900 XTX: `amdgpu`, PCI `0000:03:00.0`, label `junction`.
- Integrated Radeon 610M also present at `0000:7b:00.0`.
- Antec display USB `2022:0522`, interface 0, OUT endpoint `0x03`.

## Results

- `make check`: 18 tests passed, Python compilation and Fedora installer shell
  syntax passed. Tests include sensor renumbering, ambiguous GPUs, invalid
  config/readings, packet boundaries, missing permissions, short writes and
  disconnect/reconnect cleanup.
- `makepkg --cleanbuild --noconfirm`: succeeded with dependency checks and
  `check()` enabled. Development build fetched the recipe's exact pinned Git
  commit from a local mirror using a temporary Git URL rewrite. Public URL
  reproducibility requires that commit to be pushed before distribution.
- Source commit consumed by the installed package:
  `9618c6e9f2374b625db908b35dbd36358e5e9918`.
- Built package: `antec-flux-display-0.1.0-1-any.pkg.tar.zst`.
  SHA256: `205dbfc329ae9d219ebeab3d8bd1c3dbe2a0a81c820096c66aecae19d7dd9285`.
- Bounded `--once` test: USB accepted a 12-byte packet with CPU 47.5 C and
  GPU junction 51 C; the owner confirmed both readings appeared on the case.
- Package installed via pacman, device-specific 0660 rule applied, dedicated
  `antec-flux` account created, machine config selected the sensors above.
- `systemd-analyze verify` passed after installation. Service active/enabled,
  running as `antec-flux`, zero automatic restarts and no startup USB errors.
- Normal service restart succeeded and connected again with a new process.
- Diagnostic readings after installation: CPU 45 C, GPU junction 51 C.
- One 10-second sample after restart: 0.100% of one CPU core; process RSS
  21,760 KiB. These are a short observation, not a performance guarantee.
- USB descriptor advertises 100 mA (0.5 W at 5 V); this is a declared USB budget,
  not measured incremental power consumption or total computer wall power.

## Remaining release checks

- Actual reboot and suspend/resume, and physical USB unplug/replug remain
  untested. Reconnection has automated coverage and a service restart check.
- Fedora 44 installer retained and syntax checked, but not retested on Fedora.
- Gstack automatic selection reported `ENV` because its default Python runner
  needs pytest, which is absent. The project's standard-library unittest suite
  passed directly and inside makepkg. No Gstack ready-for-ship status is claimed.
- No development code push, upstream PR, AUR submission or public release has
  been performed. Complete publishing gates before distribution.
