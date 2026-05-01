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

"$PICOTOOL_BIN" load "$IMAGE_PATH" -f
"$PICOTOOL_BIN" reboot || true

echo "=== Verify ==="
SERIAL_PORT="$(find_serial_port || true)"
if [[ -n "$SERIAL_PORT" ]]; then
    verify_fw_version "$SERIAL_PORT"
else
    echo "Serial port not detected after flash; skipping VR check."
fi

echo "=== Done ==="
