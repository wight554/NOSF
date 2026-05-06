# Build and Flash Guide

This repo targets RP2040 (ERB v2.0) using the Pico SDK.

## Prerequisites

- `cmake`
- `ninja`
- `arm-none-eabi-gcc`
- Pico SDK checkout
- `picotool` (recommended)
- Python 3 + `pyserial` (for helper scripts)

## 1) Prepare Config Header

`config.ini` is the source of compile-time motor/TMC defaults.

```bash
cp config.ini.example config.ini   # first time only
python3 scripts/gen_config.py
```

Generated file:

- `firmware/include/tune.h`

## 2) Configure and Build

This repo uses `build_local/` for local builds.

```bash
cmake -S firmware -B build_local -G Ninja -DPICO_SDK_PATH=/path/to/pico-sdk
cmake --build build_local
```

Outputs:

- `build_local/nosf_controller.elf`
- `build_local/nosf_controller.uf2`

## 3) Flash (Recommended)

For the very first flash, put the board into BOOTSEL mode manually (from ERB README):

Step 1: Connect 24V (Power on the board)
Step 2: Connect USB-C cable to your Klipper device (usually Raspberry Pi)
Step 3: Push and hold the BOOT button
Step 4: Push the RST button and hold 0.5 seconds
Step 5: Release the RST button, after 3 seconds, release the BOOT button

Verify that the device is in boot mode by running:

```bash
lsusb
```

The output should contain an entry similar to `2e8a:0003 Raspberry Pi RP2 Boot`.

After first-time bring-up, the recommended flashing path is the repo script below.
It performs the flashing flow automatically (build, optional reboot to boot mode, flash, verify):

```bash
bash scripts/flash_nosf.sh
```

What it does:

1. Builds firmware in `build_local/`
2. Auto-detects serial port (if available)
3. Sends `BOOT:` to reboot device into BOOTSEL
4. Flashes with `picotool load ... -f`
5. Attempts firmware version check via `VR:`

## 4) Flash Manually with picotool

```bash
picotool load build_local/nosf_controller.uf2 -f
picotool reboot
```

If UF2 is not available:

```bash
picotool load build_local/nosf_controller.elf -f
picotool reboot
```

## 5) Tuning and Validation Helpers

Please see `README.md` for the full list of tuning and validation helper scripts (for example `scripts/nosf_cmd.py` and `scripts/gen_config.py`).

## Troubleshooting

### CMake cannot find Pico SDK

Set `-DPICO_SDK_PATH=/abs/path/to/pico-sdk` on first configure.

### Flash script says picotool not found

Install `picotool` or set environment variable:

```bash
PICOTOOL=/path/to/picotool bash scripts/flash_nosf.sh
```

### Device does not show as serial after flash

1. Replug USB cable.
2. Ensure firmware built with USB stdio enabled.
3. Run `python3 scripts/nosf_cmd.py --port <port> "VR:"`.

### BOOTSEL trigger fails

Put the board in BOOTSEL mode manually, then re-run flashing step.
