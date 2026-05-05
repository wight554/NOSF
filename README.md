# NOSF

NOSF is a standalone dual-lane filament controller for ERB v2.0 (RP2040).
It can run without a host plugin and handles lane switching, buffer-driven feed,
and TMC2209 register/config tuning over USB serial.

## Tested Configuration
*   **Motors**: [FYSETC G36HSY4405-6D-1200](https://github.com/FYSETC/FYSETC-MOTORS/blob/main/G36HSY4405-6D-1200/G36HSY4405-6D-1200.pdf) (Included in Fysetc NightOwl kits)
*   **Buffer**: [QuattroSync](https://github.com/Batalhoti/QuattroSync) (Superior tension management via springs vs. TurtleNeck design)

## What Is In This Repo

- `firmware/`: RP2040 firmware (C, Pico SDK)
- `scripts/`: build/config/flash/tuning helpers
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

Core control:

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

Other runtime boolean state:

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

## Tuning Scripts

- `scripts/tune_iss_sg.py`: ISS StallGuard tuning wizard (contact detection)
- `scripts/sg_monitor.py`: Real-time StallGuard monitor
- `scripts/klipper_tune.py`: Apply/read Klipper-style parameters
- `scripts/tmc_chopconf.py`: Direct CHOPCONF read/write utility
- `STALLGUARD.md`: Deep-dive guide on StallGuard & ISS tuning
- `scripts/nosf_cmd.py`: Robust single-command serial helper for Klipper integration

All scripts support `--port`; if omitted they auto-detect from available serial devices.

Examples:

```bash
python3 scripts/klipper_tune.py --lane 1 read
python3 scripts/klipper_tune.py --lane 2 apply config.ini
python3 scripts/tmc_chopconf.py --lane 1 read
python3 scripts/nosf_cmd.py "VR:" "?:"
```

## Build Notes

- Build output directory used in this repo is `build_local/`.
- Flash script uses `picotool` if available in PATH, otherwise checks local build outputs.
- For detailed flash/build troubleshooting, see `BUILD_FLASH.md`.

## Hardware and Operation Docs

- `HARDWARE.md`: board wiring and hardware assumptions
- `MANUAL.md`: runtime behavior and operator guidance
- `WORKFLOW.md`: Git branching and release discipline

---

# Development

Branches:

main      primary development branch
feature/* optional experimental branches
fix/*     optional bug fixes

---

# Safety

Always test firmware changes at low speed.

Verify sensor polarity before enabling automatic swap.
