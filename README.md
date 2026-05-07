# NOSF

NOSF is a standalone dual-lane filament controller for ERB v2.0 (RP2040).
It can run without a host plugin and handles lane switching, buffer-driven feed,
and TMC2209 configuration and diagnostics over USB serial.

## Tested Configuration
- **Motors**: [FYSETC G36HSY4405-6D-1200](https://github.com/FYSETC/FYSETC-MOTORS/blob/main/G36HSY4405-6D-1200/G36HSY4405-6D-1200.pdf) (included in Fysetc NightOwl kits)
- **Buffer**: [QuattroSync](https://github.com/Batalhoti/QuattroSync) (spring-managed dual-lane buffer with more consistent RELOAD behavior than gravity-based TurtleNeck-style designs)

## What Is In This Repo

- `firmware/`: RP2040 firmware (C, Pico SDK)
- `scripts/`: serial/config/flash helpers
- `config.ini`: user tuning source for motor and TMC defaults
- `config.ini.example`: template for new setups

## Quick Start

1. Copy and edit config:

```bash
cp config.ini.example config.ini
```

2. Generate compile-time tuning header from `config.ini`:

```bash
python3 scripts/gen_config.py
```

3. Build firmware:

```bash
cmake -S firmware -B build_local -G Ninja -DPICO_SDK_PATH=/path/to/pico-sdk
cmake --build build_local
```

4. Flash firmware (auto-detect serial, trigger BOOTSEL when possible):

```bash
bash scripts/flash_nosf.sh
```

## Configuration Model

`config.ini` is the source of compile-time motor/TMC defaults.
`scripts/gen_config.py` generates `firmware/include/tune.h`.

Mandatory keys:

- `microsteps`
- `rotation_distance`
- `run_current`

Typical workflow:

```bash
python3 scripts/gen_config.py
cmake --build build_local
```

Runtime changes (serial protocol `SET:/GET:/TW:/TR:`) can be saved to flash via `SV:`.

## Serial Runtime Commands

Common control commands:

- `T:<lane>`: set active lane (`1` or `2`)
- `LO:`: autoload active lane until output sensor or timeout
- `UL:`: reverse/unload active lane
- `TC:<lane>`: toolchange to target lane
- `ST:`: stop motors and abort active operations
- `?:` status snapshot (`I1/O1/I2/O2/YS`, tasks, sync state, `AP`)

Active lane behavior:

- `LN` is active lane (`1` or `2`), or `0` when unknown.
- Boot initialization uses OUT sensors: only `O1` active -> `LN:1`, only `O2` active -> `LN:2`, both/none -> `LN:0`.
- During preload/autoload, when a lane reaches OUT it becomes active automatically.
- If `LN:0`, `LO`, `UL`, `CU`, and `TC` return `ER:NO_ACTIVE_LANE` until you select with `T:1`/`T:2` or preload reaches OUT.

Runtime toggles (`SET:/GET:`):

- `SM` (`0/1`): sync mode enable
- `BI` (`0/1`): buffer sensor invert
- `AUTO_PRELOAD` (`0/1`): auto-start preload on IN sensor rising edge

Other runtime state:

- `TS:<0|1>`: host-reported toolhead filament presence

Examples:

```bash
python3 scripts/nosf_cmd.py "SET:AUTO_PRELOAD:1" "GET:AUTO_PRELOAD"
python3 scripts/nosf_cmd.py "SET:SM:1" "GET:SM"
python3 scripts/nosf_cmd.py "SET:BI:0" "GET:BI"
```

Persist runtime values to flash:

```bash
python3 scripts/nosf_cmd.py "SV:"
```

## Helper Scripts

- `scripts/nosf_cmd.py`: Serial helper — send commands and dump live config
- `scripts/gen_config.py`: Generate `tune.h` from `config.ini`
- `scripts/validate_regression.sh`: One-command static regression gate before flashing hardware

All scripts support `--port`; if omitted they auto-detect the serial device.

Examples:

```bash
# Send commands
python3 scripts/nosf_cmd.py "VR:" "?:"
python3 scripts/nosf_cmd.py "SET:JOIN_RATE:1600" "SV:"

# Read a full live settings snapshot as config-style key/value output
python3 scripts/nosf_cmd.py --dump

# Terse key: value dump
python3 scripts/nosf_cmd.py --dump --raw

# Static regression gate before hardware testing
bash scripts/validate_regression.sh
```

## Build Notes

- Build output directory used in this repo is `build_local/`.
- Flash script uses `picotool` if available in PATH, otherwise checks local build outputs.
- For detailed flash/build troubleshooting, see `BUILD_FLASH.md`.

## Hardware and Operation Docs

- `HARDWARE.md`: board wiring and hardware assumptions
- `MANUAL.md`: runtime behavior and operator guidance
- `TEST_CASES.md`: bring-up and regression checklist for real hardware
- `WORKFLOW.md`: current Git workflow for `main` and optional short-lived branches

---

## Development

- `main`: primary branch, expected to stay buildable and flashable
- `feature/*`, `fix/*`, `hw/*`: optional short-lived branches for risky or long-running work

---

## Safety

Always test firmware changes at low speed.

Verify sensor polarity before enabling automatic swap.
