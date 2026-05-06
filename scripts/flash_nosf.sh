#!/usr/bin/env bash
set -euo pipefail

USE_GCC=0
if [[ "${1:-}" == "--gcc" ]]; then
    USE_GCC=1
    shift
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
if [[ $USE_GCC -eq 1 ]]; then
    BUILD_DIR="${BUILD_DIR:-$REPO/build_local}"
    PRESET="gcc"
else
    BUILD_DIR="${BUILD_DIR:-$REPO/build_clang}"
    PRESET="clang"
fi
UF2_PATH="$BUILD_DIR/nosf_controller.uf2"
ELF_PATH="$BUILD_DIR/nosf_controller.elf"

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
        "$REPO/build_clang/_deps/picotool/picotool"
        "$REPO/build_clang/picotool/picotool"
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

find_rp2_device() {
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

    echo "$rp2_dev"
    return 0
}

find_and_mount_rp2() {
    # First check if already mounted.
    local mounted="$(find_rp2_mountpoint || true)"
    if [[ -n "$mounted" ]]; then
        echo "$mounted"
        return 0
    fi

    return 1
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
import errno

port = ${port@Q}
try:
    s = serial.Serial(port, 115200, timeout=2)
    time.sleep(0.1)
    s.write(b"BOOT:\n")
    # Pico reboots immediately; write or close might fail with Errno 5 (I/O error).
    s.close()
    print("Reboot command sent.")
except OSError as e:
    if e.errno == errno.EIO:
        print("Rebooting into BOOTSEL...")
    else:
        print(f"BOOTSEL trigger skipped ({e})")
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
    deadline = time.time() + 6.0
    out = ""
    while time.time() < deadline:
        s.reset_input_buffer()
        s.write(b"VR:\n")
        time.sleep(0.5)
        out = s.read(max(s.in_waiting, 1)).decode(errors="replace").strip()
        if out:
            break
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
        echo "  PICO_SDK_PATH=\$HOME/pico-sdk bash scripts/flash_nosf.sh"
        echo "or clone pico-sdk into one of:"
        echo "  $REPO/pico-sdk"
        echo "  $HOME/pico-sdk"
        exit 1
    fi

    cmake_args=( --preset "$PRESET" -S "$REPO/firmware" -B "$BUILD_DIR" )
    cmake_args+=( "-DPICO_SDK_PATH=$SDK_PATH" )
    echo "Using PICO_SDK_PATH=$SDK_PATH with preset $PRESET"
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
    "$PICOTOOL_BIN" reboot
else
    echo "Warning: picotool has no USB load support; falling back to UF2 mass-storage copy."
    if [[ ! -f "$UF2_PATH" ]]; then
        echo "Error: UF2 image not found at $UF2_PATH (required for mass-storage flashing)."
        exit 1
    fi

    # Wait for RPI-RP2 device to appear after BOOTSEL trigger.
    echo "=== Waiting for RPI-RP2 device ==="
    RP2_DEV=""
    for i in {1..20}; do
        # Look for a small USB block device (RP2040 boot is typically ~128M).
        # Check for /dev/sda1, /dev/sdb1, /dev/sdc1, etc.
        for dev in /dev/sd{a,b,c,d,e,f}1; do
            if [[ -b "$dev" ]]; then
                # Verify it's a small device (likely the RP2040).
                size=$(lsblk -bn -o SIZE "$dev" 2>/dev/null || echo "0")
                if [[ $size -gt 100000000 && $size -lt 600000000 ]]; then  # 100MB - 600MB
                    RP2_DEV="$dev"
                    echo "Found device $dev (${size} bytes) after ${i}s"
                    break 2
                fi
            fi
        done
        
        if [[ $i -lt 20 ]]; then
            echo "Waiting... ${i}s"
            sleep 1
        fi
    done

    if [[ -z "$RP2_DEV" ]]; then
        echo "Error: Could not find RPI-RP2 device."
        echo "lsblk output:"
        lsblk 2>/dev/null || echo "(lsblk unavailable)"
        echo "Available block devices:"
        ls -la /dev/sd* /dev/mmcblk* 2>/dev/null || echo "(none found)"
        echo ""
        echo "Ensure the board is in BOOT mode and connected, then rerun this script."
        exit 1
    fi

    # Mount, copy, and unmount.
    RP2_MOUNT="/mnt/RPI-RP2"
    echo "=== Mounting and flashing ==="
    sudo mkdir -p "$RP2_MOUNT"
    sudo mount "$RP2_DEV" "$RP2_MOUNT" || {
        echo "Error: Failed to mount $RP2_DEV"
        exit 1
    }

    echo "Copying UF2 to $RP2_MOUNT..."
    sudo cp "$UF2_PATH" "$RP2_MOUNT/" || {
        echo "Error: Failed to copy UF2 file"
        sudo umount "$RP2_MOUNT" 2>/dev/null || true
        exit 1
    }

    sync
    echo "Unmounting $RP2_MOUNT..."
    sudo umount "$RP2_MOUNT" || {
        echo "Error: Failed to unmount $RP2_MOUNT"
        exit 1
    }

    # Wait for USB serial to re-enumerate after unmount.
    echo "=== Waiting for USB serial ==="
    SERIAL_PORT=""
    for i in {1..15}; do
        SERIAL_PORT="$(find_serial_port || true)"
        if [[ -n "$SERIAL_PORT" ]]; then
            echo "Serial up after ${i}s"
            break
        fi
        if [[ $i -lt 15 ]]; then
            echo "Waiting... ${i}s"
            sleep 1
        fi
    done
fi

echo "=== Verify ==="
SERIAL_PORT="$(find_serial_port || true)"
if [[ -n "$SERIAL_PORT" ]]; then
    verify_fw_version "$SERIAL_PORT"
else
    echo "Serial port not detected; skipping VR check."
fi

echo "=== Done ==="
