# NOSF – USB Serial Command Reference

All communication is over USB CDC serial at 115200 baud (line-buffered, `\n` terminated).

```
Request:   CMD:PAYLOAD\n   (payload may be empty: CMD:\n or just CMD\n)
Response:  OK:DATA\n       (data absent if not applicable: OK\n)
           ER:REASON\n
Events:    EV:TYPE:DATA\n  (unsolicited, emitted any time)
```

---

## Motion commands

| Command | Description |
|---------|-------------|
| `LO:` | Load active lane — runs forward at `AUTO_RATE` speed until OUT sensor triggers, then retracts `RETRACT_MM`. Parks filament at OUT position. |
| `FD:` | Manual continuous forward feed at `FEED_RATE` speed. Runs until `ST:`. No auto-stop. |
| `FL:` | Full load to toolhead — runs forward at `FEED_RATE` speed until host sends `TS:1` (toolhead sensor). Guards: IN sensor must be present, other lane OUT must be clear. Timeout = `TC_LOAD_MS`. Emits `EV:LOADED:<lane>` on success, `EV:LOAD_TIMEOUT` on timeout. |
| `UL:` | Unload from extruder — runs reverse at `REV_RATE` speed until OUT sensor clears. Use when tip is past OUT (in bowden / extruder). Returns `ER:NOT_LOADED` if OUT is not triggered (use `UM:` instead). |
| `UM:` | Unload from MMU — runs reverse at `REV_RATE` speed until IN sensor clears. Use when tip is inside the MMU path. |
| `MV:<mm>:<f>` | Move active lane exactly `mm` millimetres at feed rate `f` mm/min (Klipper `F` units). Positive `mm` = forward, negative = reverse. Motor ramps up then runs for the computed duration, then stops. Emits `EV:MOVE_DONE:<lane>` on completion. Disables sync mode. Example: `MV:-10:300` = retract 10 mm at 5 mm/s (equivalent to `G1 E-10 F300`). |
| `CU:` | Run cutter sequence on active lane. Returns `ER:CUTTER_DISABLED` if `CUTTER` toggle is off. |
| `ST:` | Stop all motion immediately. Aborts toolchange and cutter. |

Both `UL:` and `UM:` stop automatically when the target sensor clears and emit `EV:UNLOADED:<lane>`. Both have a 30-second safety timeout (`EV:UNLOAD_TIMEOUT`).

---

## Lane / toolchange

| Command | Description |
|---------|-------------|
| `T:<1\|2>` | Set active lane without motion. |
| `TC:<1\|2>` | Full toolchange to lane N. Unloads current lane (cuts if `CUTTER=1`), swaps, then runs the new lane forward at `FEED_RATE` speed until toolhead sensor (`TS:1`). Returns `ER:NO_ACTIVE_LANE` if active lane is unknown. |

---

## Host integration

| Command | Description |
|---------|-------------|
| `TS:<0\|1>` | Report toolhead filament presence (`1` = has filament). Used by `TC_TH_MS` wait logic. |
| `SM:<0\|1>` | Enable (`1`) / disable (`0`) buffer sync mode. |

---

## Status

| Command | Response |
|---------|----------|
| `?:` | Full system status (see below). |
| `VR:` | Firmware version string. |
| `SG:<1\|2>` | Read StallGuard result for lane N → `OK:<lane>:<value>`. |

### Status fields (`?:`)

```
LN:<n>         Active lane (0 = unknown)
TC:<state>     Toolchange FSM state
L1T:<task>     Lane 1 task (IDLE/AUTOLOAD/FEED/UNLOAD/UNLOAD_MMU)
L2T:<task>     Lane 2 task
I1,O1          Lane 1 IN/OUT sensor (1 = filament present)
I2,O2          Lane 2 IN/OUT sensor
TH:<n>         Toolhead filament (from TS:)
YS:<n>         Y-splitter sensor
BUF:<state>    Buffer state (MID/ADVANCE/TRAILING/FAULT)
RATE:<n>       Current sync speed (mm/min)
BL:<n>         Baseline sync speed (mm/min)
BP:<n>         Normalised buffer position (-1.0 = full trailing … +1.0 = full advance)
SM:<n>         Sync mode enabled
BI:<n>         Buffer sensor inverted
AP:<n>         AUTO_PRELOAD enabled
CU:<n>         CUTTER enabled
SG1,SG2        StallGuard raw values
```

---

## Parameters — `SET:` / `GET:`

### Simple (no lane)

```
SET:<param>:<value>
GET:<param>
```

All speed parameters use **mm/min** (same as Klipper `F`).

All speed parameters use **mm/min** (same as Klipper `F`).

### Hardware / Kinematics (Compile-time only)
These settings must be defined in `config.ini` and require a reflash to change.

| config.ini Key | Description | Default |
|----------------|-------------|---------|
| `sense_resistor` | TMC2209 R_SENSE (Ohms) | 0.110 |
| `m1_dir_invert` | Invert rotation for lane 1 (`0`/`1`) | 0 |
| `m2_dir_invert` | Invert rotation for lane 2 (`0`/`1`) | 0 |

### Tunables (Runtime & config.ini)
These can be set in `config.ini` (as single values or comma-separated lists) **and** updated at runtime via `SET:<cmd>:<val>`. Use `_L1` or `_L2` suffixes to target specific lanes (e.g. `SET:SGT_L1:10`).

| Serial Cmd | config.ini Key | Description | Default |
|------------|----------------|-------------|---------|
| `FEED_RATE` | `feed_rate` | Fast feed speed (mm/min) | 2100 |
| `REV_RATE` | `rev_rate` | Reverse speed for unload (mm/min) | 2100 |
| `AUTO_RATE` | `auto_rate` | Autoload speed (mm/min) | 2100 |
| `RUN_CURRENT_MA` | `run_current` | Normal operating current (mA) | |
| `HOLD_CURRENT_MA` | `hold_current` | Idle holding current (mA) | run/2 |
| `MICROSTEPS` | `microsteps` | Stepper microstepping (1-256) | 16 |
| `ROTATION_DIST` | `rotation_distance` | mm of filament per full motor rotation | |
| `FULL_STEPS` | `full_steps_per_rotation` | 200 for 1.8°, 400 for 0.9° motors | 200 |
| `GEAR_RATIO` | `gear_ratio` | Mechanical reduction (e.g. `5:1`) | 1:1 |
| `INTERPOLATE` | `interpolate` | Enable driver-level step interpolation (`0`/`1`) | 1 |
| `STEALTHCHOP` | `stealthchop_threshold` | `0` = SpreadCycle (recommended), `1` = StealthChop | 0 |
| `DRIVER_TBL` | `driver_TBL` | TMC Blank time (0-3) | 2 |
| `DRIVER_TOFF` | `driver_TOFF` | TMC Off time (0-15) | 3 |
| `DRIVER_HSTRT` | `driver_HSTRT` | TMC Hysteresis start (0-7) | 5 |
| `DRIVER_HEND` | `driver_HEND` | TMC Hysteresis end (-3 to 12) | 0 |
| `STARTUP_MS` | `motion_startup_ms` | Stall arm delay after motion start (ms) | 1000 |
| `STALL_MS` | `stall_recovery_ms` | Wait time after a stall detection (ms) | 3000 |
| `AUTO_PRELOAD` | `auto_preload` | Auto-start preload on IN insert (`0`/`1`) | 1 |
| `RETRACT_MM` | `cut_feed_mm` | Back-off distance after OUT trigger (mm) | 10 |
| `CUTTER` | `enable_cutter` | Global cutter enable (`0`/`1`) | 0 |
| `SERVO_OPEN` | `servo_open_us` | Servo open position (µs) | 500 |
| `SERVO_CLOSE` | `servo_close_us` | Servo close position (µs) | 1400 |
| `SERVO_SETTLE` | `servo_settle_ms` | Servo settle time (ms) | 500 |
| `CUT_FEED` | `cut_feed_mm` | Feed distance before cut (mm) | 48 |
| `CUT_LEN` | `cut_length_mm` | Cut stroke length (mm) | 10 |
| `CUT_AMT` | `cut_amount` | Number of cut repetitions | 1 |
| `RELOAD_MODE` | `reload_mode` | `0`=MMU (manual), `1`=RELOAD (auto-switch) | 0 |
| `RELOAD_Y_MS` | `reload_y_timeout_ms` | Max wait for Y-splitter clear (ms) | 10000 |
| `TC_CUT_MS` | `tc_timeout_cut_ms` | Toolchange cut timeout (ms) | 5000 |
| `TC_UNLOAD_MS` | `tc_timeout_unload_ms` | Toolchange unload timeout (ms) | 60000 |
| `TC_Y_MS` | `tc_timeout_y_ms` | Wait for Y-splitter clear after unload (ms) | 5000 |
| `TC_TH_MS` | `tc_timeout_th_ms` | Wait for `TS:` from host (ms) | 3000 |
| `TC_LOAD_MS` | `tc_timeout_load_ms` | Toolchange load timeout (ms) | 60000 |
| `SYNC_MAX_RATE` | `sync_max_rate` | Max sync speed (mm/min) | 2500 |
| `SYNC_MIN_RATE` | `sync_min_rate` | Min sync speed (mm/min) | 0 |
| `SYNC_KP_RATE` | `sync_kp_rate` | Proportional gain for buffer sync | 850 |
| `SYNC_UP_RATE` | `sync_ramp_up_rate` | Sync ramp-up increment | 25 |
| `SYNC_DN_RATE` | `sync_ramp_dn_rate` | Sync ramp-down increment | 13 |
| `RAMP_STEP_RATE` | `ramp_step_rate` | Normal motion ramp increment | 17 |
| `PRE_RAMP_RATE` | `pre_ramp_rate` | Pre-advance speed offset (mm/min) | 35 |
| `BUF_TRAVEL` | `buf_half_travel_mm` | Half-travel of buffer arm (mm) | 5.0 |
| `BUF_HYST` | `buf_hyst_ms` | Buffer zone debounce (ms) | 30 |
| `BASELINE_RATE` | | Baseline sync speed override (mm/min) | adaptive |
| `BUF_SENSOR` | `buf_sensor_type` | `0`=dual endstop, `1`=analog PSF | 0 |
| `BUF_NEUTRAL` | `buf_neutral` | Analog: ADC mechanical neutral (0.0–1.0) | 0.5 |
| `BUF_RANGE` | `buf_range` | Analog: ADC deflection range (0.0–1.0) | 0.45 |
| `BUF_THR` | `buf_thr` | Analog: normalized ADVANCE/TRAILING thr | 0.30 |
| `BUF_ALPHA` | `buf_analog_alpha` | Analog: EMA filter weight | 0.20 |
| `TS_BUF_MS` | `ts_buf_fallback_ms` | Buffer-based TS:1 fallback (ms) | 2000 |
| `SYNC_SG_INTERP` | `sync_sg_interp` | Enable SG interpolation for MMU sync | 0 |
| `RELOAD_SG_INTERP` | `reload_sg_interp` | Enable SG interpolation for RELOAD | 1 |
| `SG_CURRENT_MA` | `sg_current_ma` | Current for SG tasks (mA) | 800 |
| `JOIN_RATE` | `join_rate` | RELOAD fast approach speed (mm/min) | 2100 |
| `PRESS_RATE` | `press_rate` | RELOAD follow top speed (mm/min) | 1275 |
| `TRAILING_RATE` | `trailing_rate` | RELOAD follow coast speed (mm/min) | 42 |
| `SG_MA_LEN` | `sg_ma_len` | SG moving average window | 5 |
| `FOLLOW_MS` | `follow_timeout_ms` | RELOAD follow timeout (ms) (per-lane capable) | 10000 |
| `SGT` | `sgt` | Lane DIAG threshold (-64 to 63) (per-lane) | 0 |
| `TCOOLTHRS` | `tcoolthrs` | SG activation threshold (TSTEP) (per-lane) | 0xFFFFF |
| `SG_DERIV` | `sg_deriv` | SG approach contact threshold (per-lane) | 3 |
| `SG_TARGET` | `sg_target` | SG follow-sync setpoint (per-lane) | 320.0 |
| `MM_PER_STEP` | | (Read-only) Actual mm per step | from tune.h |

### Per-lane

```
SET:<param>_L<lane>:<value>    (e.g. SET:SGT_L1:10)
GET:<param>_L<lane>            (e.g. GET:SGT_L1)
```

| Parameter | Description |
|-----------|-------------|
| `RUN_CURRENT_MA` | Run current for lane N (mA, 0–2000) |
| `HOLD_CURRENT_MA` | Hold current for lane N (mA, 0–2000) |
| `SGT` | StallGuard threshold for lane N (-64 to 63) |
| `TCOOLTHRS` | CoolStep/SG threshold for lane N (TSTEP) |
| `SG_CURRENT_MA` | High-torque current for lane N (mA, 0–2000) |
| `MICROSTEPS` | Microstepping for lane N (1–256) |
| `ROTATION_DIST` | Rotation distance for lane N (mm) |
| `GEAR_RATIO` | Gear ratio for lane N (e.g. 5.0) |
| `FULL_STEPS` | Full steps per rotation for lane N (200/400) |
| `SG_TARGET` | SG follow-sync setpoint for lane N |
| `SG_DERIV` | SG approach contact threshold for lane N |
| `FOLLOW_MS` | RELOAD follow timeout for lane N (ms) |

---

## Settings persistence

| Command | Description |
|---------|-------------|
| `SV:` | Save all current settings to flash. |
| `LD:` | Load settings from flash (also called on boot). |
| `RS:` | Reset to compile-time defaults and save. |

All runtime parameters, including motor currents and kinematics, are restored from flash on boot if a valid settings block exists. `RS:` can be used to return to the hard-coded defaults in `tune.h`.

---

## TMC register access

| Command | Description |
|---------|-------------|
| `TW:<lane>:<reg>:<val>` | Write TMC register. `val` may be decimal or `0x`-prefixed hex. |
| `TR:<lane>:<reg>` | Read TMC register → `OK:<lane>:<reg>:0x<hex>`. IHOLD_IRUN is returned from shadow (no UART read). |
| `RR:<lane>` | Scan TMC addresses 0–3 and raw-read GCONF. Useful for bus debug. |
| `CA:<lane>:<ma>` | Set run current (mA) — shorthand for `TW` to IHOLD_IRUN. |

---

## System

| Command | Description |
|---------|-------------|
| `BOOT:` | Reboot into BOOTSEL (USB mass-storage) for flashing. |

---

## Async events

Events are emitted without being requested. Format: `EV:<type>:<data>\n`.

| Event | Data | Meaning |
|-------|------|---------|
| `EV:ACTIVE` | `1`, `2`, or `NONE` | Active lane changed |
| `EV:PRELOAD` | `<lane>` | Auto-preload started on lane insert |
| `EV:UNLOADED` | `<lane>` | Unload completed (sensor cleared) |
| `EV:UNLOAD_TIMEOUT` | — | Unload timed out (30 s) |
| `EV:RUNOUT` | `<lane>` | IN sensor lost while feeding or during full load (filament tail passed through) |
| `EV:STALL` | `<lane>` | StallGuard triggered |
| `EV:TC:CUTTING` | `<lane>` | Toolchange: starting cut |
| `EV:TC:UNLOADING` | `<lane>` | Toolchange: unloading |
| `EV:TC:SWAPPING` | `<from>-><to>` | Toolchange: swapping lanes |
| `EV:TC:LOADING` | `<lane>` | Toolchange: loading new lane |
| `EV:TC:DONE` | `<lane>` | Toolchange completed |
| `EV:TC:ERROR` | `<reason>` | Toolchange failed |
| `EV:CUT:FEEDING` | — | Cutter feed phase started |
| `EV:BS` | `<zone>,<mm_min>,<buf_pos>` | Buffer sync update (500 ms interval); buf_pos is normalised −1..+1 |

---

## Quick reference

```bash
# Status
python3 scripts/nosf_cmd.py "?:"

# Load lane 1
python3 scripts/nosf_cmd.py "T:1" "LO:"

# Unload from extruder (tip past OUT sensor)
python3 scripts/nosf_cmd.py "UL:"

# Unload from MMU (tip inside MMU, before OUT sensor)
python3 scripts/nosf_cmd.py "UM:"

# Toolchange to lane 2
python3 scripts/nosf_cmd.py "TC:2"

# Monitor StallGuard on lane 1
python3 scripts/sg_monitor.py --lane 1

# Save settings
python3 scripts/nosf_cmd.py "SV:"

# Reboot to BOOTSEL
python3 scripts/nosf_cmd.py "BOOT:"
```
