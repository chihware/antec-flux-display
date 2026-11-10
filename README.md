# Antec Flux Pro display for Arch Linux and CachyOS

Show CPU and GPU temperatures on the Antec Flux Pro case display without
Windows/iUnity. This fork adds Arch Linux/CachyOS packaging, configurable
sensor selection, and diagnostics to
[Mario Estrada's Fedora implementation](https://github.com/marioestradaf/antec-flux-display).
The Fedora 44 installer is retained. This is a community fork, not an official
Antec driver. The original authors' work makes this Linux support possible;
see [credits and license](#credits-and-license).

## Status

Development branch: `arch-cachyos`. Automated tests cover multi-GPU selection,
hwmon renumbering, unavailable sensors, packet boundaries, and USB reconnection.
Validated on CachyOS on 2026-11-10 with a Ryzen 9 9950X and Radeon RX 7900 XTX
alongside the integrated Radeon 610M: both physical readings confirmed, Arch
package installed, unprivileged service running, and normal service restart
reconnected successfully. Boot enablement is configured; an actual reboot and
suspend/resume are not yet tested. See [validation notes](docs/validation-cachyos.md).

## Hardware and requirements

- Antec Flux Pro internal USB display, USB ID **2022:0522**.
- Python 3.11 or newer, PyUSB, and libusb for the display service.
- CPU temperatures exposed through `k10temp`, `coretemp`, or `zenpower`.
- GPU temperatures from `nvidia`, `amdgpu`, `i915`, or `xe` hwmon. NVIDIA's
  `nvidia-smi` is also supported when hwmon is unavailable.

No fan, RGB, GPU-clock, chipset-driver, or firmware controls are changed.

## Check sensors without installation

These commands do not access the USB display and do not require PyUSB or root:

```bash
python3 antec-flux-display.py --list-sensors
python3 antec-flux-display.py --dry-run \
  --gpu-pci 0000:03:00.0 --gpu-sensor junction --cpu-sensor Tctl
```

Use the PCI address and label reported on **your** computer. If several GPUs
use the same driver, configure the intended device; the program reports an
ambiguous selection instead of guessing the first GPU. Sensor discovery is
repeated during operation, so changing `hwmonN` numbers does not select another
device. `--dry-run` exits nonzero if a required reading cannot be displayed.

## Arch Linux / CachyOS

Install from the fork's `arch-cachyos` branch. This is a source-build package;
it has not been submitted to the AUR and there is no prebuilt release.
The recipe in `packaging/arch/PKGBUILD` checks out a pinned source commit and
runs the tests while building. Inspect the recipe before running `makepkg`.

```bash
git clone --branch arch-cachyos https://github.com/chihware/antec-flux-display.git
cd antec-flux-display/packaging/arch
makepkg -si
antec-flux-display --list-sensors
sudoedit /etc/antec-flux-display/config.toml
antec-flux-display --dry-run
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb \
  --attr-match=idVendor=2022 --attr-match=idProduct=0522 --action=change
sudo systemctl enable --now antec-flux-display.service
```

The package installs a dedicated `antec-flux` service user, a device-specific
0660 udev rule, a hardened systemd service and a config file protected by
pacman's backup handling. Installing the package does not enable the service.
Remove it with `sudo pacman -Rns antec-flux-display` after stopping/disabling
the service. The old Fedora installer uses different `/usr/local` locations;
remove a previous manual installation before installing the Arch package.

## Configuration

The service reads `/etc/antec-flux-display/config.toml`. Defaults work for a
single unambiguous supported CPU/GPU; `--config PATH` selects another file.
An explicitly requested missing config, unknown key, invalid interval or
invalid PCI address is an error before USB access.

Example for a Ryzen system with an AMD discrete GPU:

```toml
[cpu]
driver = "k10temp"
label = "Tctl"

[gpu]
driver = "amdgpu"
pci_address = "0000:03:00.0"
label = "junction"

[display]
interval = 1.0
reconnect_interval = 2.0
```

GPU label `edge` is the regular GPU temperature; `junction` is the hotspot;
`mem` is GPU memory when available. CPU labels depend on the driver: Ryzen
usually exposes `Tctl` and individual CCD labels, Intel often `Package id 0`.
Labels are matched exactly, ignoring case. An explicit missing label never
silently falls back to another reading. Restart the service after config edits.

## Fedora 44

The existing Fedora installer remains available:

```bash
sudo ./install.sh
```

It installs PyUSB/libusb, the dedicated service account and USB permissions,
and enables the service. Configuration is optional at the default path above.
Fedora installation has not been retested by this fork.

## Display behavior and troubleshooting

The display receives a 12-byte temperature packet once per configured interval.
Missing, nonpositive, or unrepresentable values show `--.-`, not a fabricated
zero. The known protocol has two whole-number digits and one decimal digit;
values rounding to 100.0 C or higher show `--.-` and a journal warning with the
actual temperature, rather than being silently clamped to 99.9 C.

```bash
lsusb -d 2022:0522
antec-flux-display --dry-run
systemctl status antec-flux-display.service
journalctl -u antec-flux-display.service -b
```

For a bounded hardware check with appropriate USB permissions:

```bash
antec-flux-display --once
```

This sends exactly one current CPU/GPU sample and exits. It performs no write
if either reading is unavailable. Do not run it concurrently with another
display service. The case's temperature-display button can switch the display
off; select the mode showing both readings during testing.

On USB failure the daemon releases its device handle and retries with a fresh
one. On exit it restores a kernel driver it detached and releases USB resources.
The service runs without root privileges; avoid world-writable USB rules.

## Development

```bash
make check
```

Tests use temporary sensor trees and fake USB devices, so they need neither
hardware nor root. They exercise protocol encoding, hardware identity changes,
ambiguous selection, bad/missing readings, one-shot limits, permission errors,
short writes, reconnection and driver cleanup.

## Credits and license

MIT license; preserve the copyright notice in [LICENSE](LICENSE).

This project builds on community work; the Arch/CachyOS additions are not the
original reverse engineering or original Python implementation.

- **[Mario Estrada](https://github.com/marioestradaf/antec-flux-display)**:
  the upstream Python service, Fedora installer, USB integration and service
  hardening on which this fork is based. The original MIT copyright notice
  remains intact in `LICENSE`.
- **[Nish Tahir](https://github.com/nishtahir/antec-flux-pro-display)**:
  the original Linux project and
  [write-up documenting the USB reverse engineering and packet format](https://nishtahir.com/building-an-ubuntu-service-for-my-antec-flux-display/).
- **[AKoskovich](https://github.com/AKoskovich)**:
  earlier community work acknowledged by Nish's project and by the Python
  upstream. See those projects for their original attribution and history.
- **[Chih's fork](https://github.com/chihware/antec-flux-display)**:
  Arch/CachyOS packaging and validation, configurable PCI/label sensor selection,
  diagnostics, reconnection handling and automated tests.

Please preserve the license and these upstream acknowledgements when sharing
or adapting the project.
