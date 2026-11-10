#!/usr/bin/env bash
#
# Fedora 44 installer for the Antec Flux Pro Display Service.
#
# Run with:
#   sudo ./install.sh
#
# Expected alongside this installer:
#   antec-flux-display.py

set -Eeuo pipefail
IFS=$'\n\t'

readonly SERVICE_NAME="antec-flux-display"
readonly SERVICE_USER="antec-flux"
readonly SERVICE_GROUP="antec-flux"

readonly INSTALL_DIRECTORY="/usr/local/libexec/antec-flux-display"
readonly SCRIPT_DESTINATION="${INSTALL_DIRECTORY}/antec-flux-display.py"
readonly SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
readonly UDEV_RULE_FILE="/etc/udev/rules.d/70-antec-flux-display.rules"

readonly VENDOR_ID="2022"
readonly PRODUCT_ID="0522"

SCRIPT_DIRECTORY="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1
    pwd -P
)"
readonly SOURCE_SCRIPT="${SCRIPT_DIRECTORY}/antec-flux-display.py"
readonly SOURCE_SENSOR_MODULE="${SCRIPT_DIRECTORY}/flux_sensors.py"


function fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}


function require_root() {
    if (( EUID != 0 )); then
        fail "Run this installer as root: sudo ./install.sh"
    fi
}


function verify_fedora() {
    [[ -r /etc/os-release ]] || fail "Cannot identify the operating system."

    # shellcheck disable=SC1091
    source /etc/os-release

    [[ "${ID:-}" == "fedora" ]] ||
        fail "This installer supports Fedora only."

    [[ "${VERSION_ID:-}" == "44" ]] ||
        fail "This installer targets Fedora 44; detected Fedora ${VERSION_ID:-unknown}."
}


function verify_source_script() {
    [[ -f "${SOURCE_SCRIPT}" ]] ||
        fail "Missing ${SOURCE_SCRIPT}"

    [[ ! -L "${SOURCE_SCRIPT}" ]] ||
        fail "Refusing to install a symbolic link: ${SOURCE_SCRIPT}"

    [[ -r "${SOURCE_SCRIPT}" ]] ||
        fail "Cannot read ${SOURCE_SCRIPT}"

    [[ -f "${SOURCE_SENSOR_MODULE}" && ! -L "${SOURCE_SENSOR_MODULE}" ]] ||
        fail "Missing regular sensor module: ${SOURCE_SENSOR_MODULE}"
    /usr/bin/python3 -m py_compile "${SOURCE_SCRIPT}" "${SOURCE_SENSOR_MODULE}" ||
        fail "Python syntax validation failed."
}


function install_dependencies() {
    printf '[1/7] Installing Fedora dependencies...\n'

    /usr/bin/dnf --assumeyes install \
        python3 \
        python3-pyusb \
        libusb1 \
        systemd-udev
}


function create_service_account() {
    printf '[2/7] Creating restricted service account...\n'

    if ! /usr/bin/getent group "${SERVICE_GROUP}" >/dev/null; then
        /usr/sbin/groupadd --system "${SERVICE_GROUP}"
    fi

    if ! /usr/bin/getent passwd "${SERVICE_USER}" >/dev/null; then
        /usr/sbin/useradd \
            --system \
            --gid "${SERVICE_GROUP}" \
            --home-dir "/" \
            --no-create-home \
            --shell /usr/sbin/nologin \
            --comment "Antec Flux display service" \
            "${SERVICE_USER}"
    fi

    for supplemental_group in video render; do
        if /usr/bin/getent group "${supplemental_group}" >/dev/null; then
            /usr/sbin/usermod \
                --append \
                --groups "${supplemental_group}" \
                "${SERVICE_USER}"
        fi
    done
}


function install_service_script() {
    printf '[3/7] Installing service script...\n'

    /usr/bin/install \
        --directory \
        --owner=root \
        --group=root \
        --mode=0755 \
        "${INSTALL_DIRECTORY}"

    /usr/bin/install \
        --owner=root \
        --group=root \
        --mode=0755 \
        "${SOURCE_SCRIPT}" \
        "${SCRIPT_DESTINATION}"

    /usr/bin/install --owner=root --group=root --mode=0644 \
        "${SOURCE_SENSOR_MODULE}" "${INSTALL_DIRECTORY}/flux_sensors.py"

    # Apply Fedora's normal SELinux context for this location.
    if command -v restorecon >/dev/null 2>&1; then
        /usr/sbin/restorecon -RF "${INSTALL_DIRECTORY}"
    fi
}


function install_udev_rule() {
    printf '[4/7] Installing udev rule...\n'

    local temporary_rule
    temporary_rule="$(mktemp)"

    cat >"${temporary_rule}" <<EOF
# Antec Flux Pro front-panel display
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="${VENDOR_ID}", ATTR{idProduct}=="${PRODUCT_ID}", GROUP="${SERVICE_GROUP}", MODE="0660"
EOF

    /usr/bin/install \
        --owner=root \
        --group=root \
        --mode=0644 \
        "${temporary_rule}" \
        "${UDEV_RULE_FILE}"

    rm -f -- "${temporary_rule}"

    if command -v restorecon >/dev/null 2>&1; then
        /usr/sbin/restorecon "${UDEV_RULE_FILE}"
    fi

    /usr/bin/udevadm control --reload-rules
    /usr/bin/udevadm trigger \
        --subsystem-match=usb \
        --attr-match=idVendor="${VENDOR_ID}" \
        --attr-match=idProduct="${PRODUCT_ID}" \
        --action=change
}


function install_systemd_service() {
    printf '[5/7] Installing hardened systemd service...\n'

    local temporary_service
    temporary_service="$(mktemp)"

    cat >"${temporary_service}" <<EOF
[Unit]
Description=Antec Flux Pro temperature display
Documentation=https://nishtahir.com/building-an-ubuntu-service-for-my-antec-flux-display/
After=systemd-udevd.service
Wants=systemd-udevd.service

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
ExecStart=/usr/bin/python3 ${SCRIPT_DESTINATION}

Restart=on-failure
RestartSec=3s
TimeoutStopSec=5s

NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=

PrivateTmp=yes
PrivateDevices=no
PrivateUsers=no

ProtectSystem=strict
ProtectHome=yes
ProtectClock=yes
ProtectControlGroups=yes
ProtectKernelLogs=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes

ProtectHostname=yes
ProtectProc=invisible
ProcSubset=pid

RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native

RemoveIPC=yes
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

    /usr/bin/install \
        --owner=root \
        --group=root \
        --mode=0644 \
        "${temporary_service}" \
        "${SERVICE_FILE}"

    rm -f -- "${temporary_service}"

    if command -v restorecon >/dev/null 2>&1; then
        /usr/sbin/restorecon "${SERVICE_FILE}"
    fi

    /usr/bin/systemd-analyze verify "${SERVICE_FILE}"
    /usr/bin/systemctl daemon-reload
}


function enable_service() {
    printf '[6/7] Enabling service...\n'
    /usr/bin/systemctl enable "${SERVICE_NAME}.service"

    printf '[7/7] Starting service...\n'
    /usr/bin/systemctl restart "${SERVICE_NAME}.service"
}


function show_result() {
    printf '\n=== Installation complete ===\n\n'
    printf 'Status:    systemctl status %s\n' "${SERVICE_NAME}"
    printf 'Logs:      journalctl -u %s -f\n' "${SERVICE_NAME}"
    printf 'Security:  systemd-analyze security %s.service\n' "${SERVICE_NAME}"
    printf '\n'

    if ! /usr/bin/systemctl --quiet is-active "${SERVICE_NAME}.service"; then
        printf 'WARNING: The service is installed but did not start successfully.\n' >&2
        printf 'Inspect it with:\n' >&2
        printf '  journalctl -u %s.service -b --no-pager\n' "${SERVICE_NAME}" >&2
        exit 1
    fi
}


function main() {
    require_root
    verify_fedora
    verify_source_script
    install_dependencies
    create_service_account
    install_service_script
    install_udev_rule
    install_systemd_service
    enable_service
    show_result
}


main "$@"
