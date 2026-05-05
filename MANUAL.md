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

| Parameter | Description | Default |
|-----------|-------------|---------|
| `FEED_RATE` | Feed speed for `FL:`, `FD:`, TC load (mm/min) | 2100 |
| `REV_RATE` | Reverse speed for `UL:`, `UM:` (mm/min) | 2100 |
| `AUTO_RATE` | Autoload / `LO:` speed (mm/min) | 2100 |
| `STARTUP_MS` | Stall arm delay after motion start (ms) | 1000 |
| `AUTO_PRELOAD` | Auto-start preload on IN insert (`0`/`1`) | 1 |
| `RETRACT_MM` | Back-off distance after OUT trigger on autoload (mm) | 10 |
| `CUTTER` | Enable cutter (`0`/`1`) | 0 |
| `MM_PER_STEP` | mm of filament per step (derived from config.ini) | from tune.h |
| `SERVO_OPEN` | Servo open position (µs) | 500 |
| `SERVO_CLOSE` | Servo close position (µs) | 1400 |
| `SERVO_SETTLE` | Servo settle time (ms) | 500 |
| `CUT_FEED` | Feed distance before cut (mm) | 48 |
| `CUT_LEN` | Cut stroke length (mm) | 10 |
| `CUT_AMT` | Number of cut repetitions | 1 |
| `RELOAD_MODE` | Operating mode: `0` = MMU (manual/Klipper), `1` = RELOAD (auto-switch) | 0 |
| `RELOAD_Y_MS` | Wait for Y-splitter to clear after runout (ms) | 10000 |
| `TC_CUT_MS` | Toolchange cut timeout (ms) | 5000 |
| `TC_UNLOAD_MS` | Toolchange unload timeout (ms) | 60000 |
| `TC_Y_MS` | Wait for Y-splitter to clear after unload (ms, 0 = skip) | 5000 |
| `TC_TH_MS` | Wait for `TS:` from host (ms, 0 = skip) | 3000 |
| `TC_LOAD_MS` | Toolchange load timeout (ms) | 60000 |
| `SYNC_MAX_RATE` | Max sync speed (mm/min) | 2500 |
| `SYNC_MIN_RATE` | Min sync speed (mm/min) | 0 |
| `SYNC_KP_RATE` | Proportional gain: speed correction at full buffer deflection (mm/min per unit) | 850 |
| `SYNC_UP_RATE` | Sync ramp-up increment (mm/min per 20 ms tick) | 25 |
| `SYNC_DN_RATE` | Sync ramp-down increment (mm/min per 20 ms tick) | 13 |
| `RAMP_STEP_RATE` | Normal motion ramp increment (mm/min per 5 ms tick) | 17 |
| `PRE_RAMP_RATE` | Pre-advance speed offset (mm/min) | 35 |
| `BUF_TRAVEL` | Half-travel of buffer arm (mm) | 5.0 |
| `BUF_HYST` | Buffer zone debounce (ms) | 30 |
| `BASELINE_RATE` | Baseline sync speed override (mm/min) | adaptive |
| `BUF_SENSOR` | Buffer sensor type: `0` = dual endstop, `1` = analog PSF | 0 |
| `BUF_NEUTRAL` | Analog sensor: ADC fraction at mechanical neutral (0.0–1.0) | 0.5 |
| `BUF_RANGE` | Analog sensor: ADC fraction from neutral to full deflection | 0.45 |
| `BUF_THR` | Analog sensor: normalised threshold to declare ADVANCE/TRAILING | 0.30 |
| `BUF_ALPHA` | Analog sensor: EMA filter weight (higher = faster response) | 0.20 |
| `TS_BUF_MS` | Buffer-based TS:1 fallback: ms buffer must hold TRAILING after OUT seen — tip pressed against extruder gears (0 = disabled) | 2000 |
| `SYNC_SG` | Enable StallGuard-based speed scaling (pseudo-analog) for all sync and RELOAD tasks (`0`/`1`). When `0`, system uses digital bang-bang (switch between `TRAILING_RATE` and target). | 0 |
| `SG_CURRENT_MA` | Motor current used when StallGuard is active (RELOAD or `SYNC_SG=1`) (mA, 0–2000) | 800 |
| `JOIN_RATE` | Fast approach speed (mm/min); must exceed max print speed | 2100 |
| `PRESS_RATE` | Follow sync top speed (mm/min) — used when buffer is MID/ADVANCE | 1275 |
| `TRAILING_RATE` | Follow sync coast speed (mm/min) — used when buffer is TRAILING | 42 |
| `SG_DERIV` | StallGuard approach derivative threshold: drop/tick that fires contact detection | 3 |
| `SG_TARGET` | Follow sync SG setpoint: motor speed scales from `PRESS_RATE` (SG ≥ target) to 0 (SG = 0) (0 = disabled) | 320.0 |
| `SG_MA_LEN` | StallGuard moving average window length | 5 |
| `FOLLOW_MS` | Follow sync timeout (ms) before error | 10000 |
| `SGT_L1` | Lane 1 SGTHRS — DIAG fires when SG_RESULT ≤ 2 × value; 0 = disabled | 0 |
| `SGT_L2` | Lane 2 SGTHRS — DIAG fires when SG_RESULT ≤ 2 × value; 0 = disabled | 0 |
| `TCOOLTHRS` | StallGuard activation: SG active when TSTEP ≤ this value (TSTEP = ~12.5 MHz ÷ SPS). Default `0xFFFFF` (max) ensures StallGuard is active at all speeds. | 0xFFFFF |

### Per-lane

```
SET:<param>:<lane>:<value>
GET:<param>:<lane>
```

| Parameter | Description |
|-----------|-------------|
| `RUN_CURRENT_MA` | Run current for lane N (mA, 0–2000) |
| `HOLD_CURRENT_MA` | Hold current for lane N (mA, 0–2000) |

---

## Settings persistence

| Command | Description |
|---------|-------------|
| `SV:` | Save all current settings to flash. |
| `LD:` | Load settings from flash (also called on boot). |
| `RS:` | Reset to compile-time defaults and save. |

Motor parameters (`RUN_CURRENT_MA`, `HOLD_CURRENT_MA`, `MM_PER_STEP`, `MICROSTEPS`) always come from the compiled `tune.h` on boot — flash values for these are ignored. All other parameters are restored from flash.

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
