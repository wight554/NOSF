#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$REPO/build_local}"
UF2_PATH="$BUILD_DIR/nightowl_controller.uf2"
ELF_PATH="$BUILD_DIR/nightowl_controller.elf"

find_pico_sdk_path() {
    local candidates=()

    # Respect explicit user-provided path first.
    if [[ -n "${PICO_SDK_PATH:-}" ]]; then
        candidates+=("$PICO_SDK_PATH")
    fi

    # Common local layouts.
    candidates+=(
        "$REPO/pico-sdk"
        "$HOME/pico-sdk"
        "/opt/pico-sdk"
        "/usr/local/pico-sdk"
    )

    local path
    for path in "${candidates[@]}"; do
        if [[ -f "$path/pico_sdk_init.cmake" ]]; then
            echo "$path"
            return 0
        fi
    done

    return 1
}

find_picotool() {
    if [[ -n "${PICOTOOL:-}" && -x "${PICOTOOL:-}" ]]; then
        echo "$PICOTOOL"
        return 0
    fi

    if command -v picotool >/dev/null 2>&1; then
        command -v picotool
        return 0
    fi

    local candidates=(
        "$REPO/build_local/_deps/picotool/picotool"
        "$REPO/build_local/picotool/picotool"
        "$REPO/build_audit/_deps/picotool/picotool"
    )

    for c in "${candidates[@]}"; do
        if [[ -x "$c" ]]; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

picotool_supports_load() {
    local bin="$1"
    "$bin" help 2>&1 | grep -qE '(^|[[:space:]])load([[:space:]]|$)'
}

find_rp2_mountpoint() {
    local candidates=(
        "/media/$USER/RPI-RP2"
        "/media/pi/RPI-RP2"
        "/run/media/$USER/RPI-RP2"
        "/mnt/RPI-RP2"
        "/Volumes/RPI-RP2"
    )

    local p
    for p in "${candidates[@]}"; do
        if [[ -d "$p" ]]; then
            echo "$p"
            return 0
        fi
    done

    return 1
}

find_and_mount_rp2() {
    # First check if already mounted.
    local mounted="$(find_rp2_mountpoint || true)"
    if [[ -n "$mounted" ]]; then
        echo "$mounted"
        return 0
    fi

    # Wait and retry loop: device may take a moment to appear after BOOT command.
    local max_retries=10
    local retry=0
    local rp2_dev=""

    while [[ $retry -lt $max_retries ]]; do
        # Try to find the RPI-RP2 block device.
        if command -v lsblk >/dev/null 2>&1; then
            rp2_dev="$(lsblk -n -d -o NAME,MODEL 2>/dev/null | grep -i 'rpi-rp2' | head -1 | awk '{print "/dev/" $1}')"
        fi

        if [[ -z "$rp2_dev" ]]; then
            # Fallback: try common sd* names (usually /dev/sda1 on Raspberry Pi).
            if [[ -b "/dev/sda1" ]]; then
                rp2_dev="/dev/sda1"
            fi
        fi

        if [[ -n "$rp2_dev" ]]; then
            break
        fi

        retry=$((retry + 1))
        if [[ $retry -lt $max_retries ]]; then
            sleep 0.5
        fi
    done

    if [[ -z "$rp2_dev" ]]; then
        # Unable to find device.
        return 1
    fi

    # Try to mount at a suitable location.
    local mount_point="/media/$USER/RPI-RP2"
    if [[ ! -d "$mount_point" ]]; then
        if ! mkdir -p "$mount_point" 2>/dev/null; then
            # Fallback to /tmp if user doesn't have permission.
            mount_point="/tmp/RPI-RP2"
            mkdir -p "$mount_point" 2>/dev/null || return 1
        fi
    fi

    # Try mount without sudo first, then with sudo if needed.
    if ! mount "$rp2_dev" "$mount_point" 2>/dev/null; then
        if ! sudo mount "$rp2_dev" "$mount_point" 2>/dev/null; then
            echo "Failed to mount $rp2_dev at $mount_point (tried both with and without sudo)" >&2
            return 1
        fi
    fi

    echo "$mount_point"
    return 0
}

find_serial_port() {
    PYTHONPATH="$REPO/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PYEOF'
from serial_utils import find_port
port = find_port()
print(port or "")
PYEOF
}

send_bootsel() {
    local port="$1"
    PYTHONPATH="$REPO/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - <<PYEOF
import serial
import time

port = ${port@Q}
try:
    s = serial.Serial(port, 115200, timeout=2)
    time.sleep(0.3)
    s.reset_input_buffer()
    s.write(b"BOOT:\n")
    time.sleep(0.3)
    out = s.read(s.in_waiting).decode(errors="replace").strip()
    if out:
        print(out)
    s.close()
except Exception as e:
    print(f"BOOTSEL trigger skipped ({e})")
PYEOF
}

verify_fw_version() {
    local port="$1"
    PYTHONPATH="$REPO/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 - <<PYEOF
import serial
import time

port = ${port@Q}
try:
    s = serial.Serial(port, 115200, timeout=2)
    time.sleep(0.3)
    s.reset_input_buffer()
    s.write(b"VR:\n")
    time.sleep(0.3)
    out = s.read(s.in_waiting).decode(errors="replace").strip()
    print(out or "No VR response")
    s.close()
except Exception as e:
    print(f"Unable to verify firmware version on {port}: {e}")
PYEOF
}

echo "=== Build ==="
mkdir -p "$BUILD_DIR"
if [[ ! -f "$BUILD_DIR/build.ninja" ]]; then
    echo "Configuring CMake in $BUILD_DIR"
    SDK_PATH="$(find_pico_sdk_path || true)"
    if [[ -z "$SDK_PATH" ]]; then
        echo "Error: Pico SDK not found."
        echo "Set PICO_SDK_PATH and rerun, for example:"
        echo "  PICO_SDK_PATH=\$HOME/pico-sdk bash scripts/flash_nightowl.sh"
        echo "or clone pico-sdk into one of:"
        echo "  $REPO/pico-sdk"
        echo "  $HOME/pico-sdk"
        exit 1
    fi

    cmake_args=( -S "$REPO/firmware" -B "$BUILD_DIR" -G Ninja )
    cmake_args+=( "-DPICO_SDK_PATH=$SDK_PATH" )
    echo "Using PICO_SDK_PATH=$SDK_PATH"
    cmake "${cmake_args[@]}"
fi
cmake --build "$BUILD_DIR"

SERIAL_PORT="$(find_serial_port || true)"
if [[ -n "$SERIAL_PORT" ]]; then
    echo "=== Trigger BOOTSEL on $SERIAL_PORT ==="
    send_bootsel "$SERIAL_PORT"
else
    echo "=== No serial port found; assuming device already in BOOTSEL ==="
fi

echo "=== Flash ==="
PICOTOOL_BIN="$(find_picotool || true)"
if [[ -z "$PICOTOOL_BIN" ]]; then
    echo "Error: picotool not found. Install picotool or set PICOTOOL=/path/to/picotool"
    exit 1
fi

IMAGE_PATH="$UF2_PATH"
if [[ ! -f "$IMAGE_PATH" ]]; then
    IMAGE_PATH="$ELF_PATH"
fi
if [[ ! -f "$IMAGE_PATH" ]]; then
    echo "Error: build output not found ($UF2_PATH or $ELF_PATH)"
    exit 1
fi

if picotool_supports_load "$PICOTOOL_BIN"; then
    "$PICOTOOL_BIN" load "$IMAGE_PATH" -f
    "$PICOTOOL_BIN" reboot || true
else
    echo "Warning: picotool has no USB load support; falling back to UF2 mass-storage copy."
    if [[ ! -f "$UF2_PATH" ]]; then
        echo "Error: UF2 image not found at $UF2_PATH (required for mass-storage flashing)."
        exit 1
    fi

    echo "Attempting to locate and mount RPI-RP2 mass-storage device..."
    RP2_MOUNT="$(find_and_mount_rp2 || true)"
    if [[ -z "$RP2_MOUNT" ]]; then
        echo "Error: Could not find or mount RPI-RP2 device."
        echo "Ensure the board is in BOOT mode and connected, then rerun this script."
        echo "Alternatively, manually mount it and rerun: sudo mount /dev/sda1 /mnt/RPI-RP2"
        exit 1
    fi

    echo "Copying UF2 to $RP2_MOUNT..."
    cp "$UF2_PATH" "$RP2_MOUNT/"
    sync
fi

echo "=== Verify ==="
SERIAL_PORT="$(find_serial_port || true)"
if [[ -n "$SERIAL_PORT" ]]; then
    verify_fw_version "$SERIAL_PORT"
else
    echo "Serial port not detected after flash; skipping VR check."
fi

echo "=== Done ==="
