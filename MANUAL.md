# NightOwl Controller – USB Serial Command Reference

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
| `LO:` | Load active lane — runs forward at `AUTO_SPS` until OUT sensor triggers, then retracts `RETRACT_MM`. Parks filament at OUT position. |
| `FD:` | Manual continuous forward feed at `FEED_SPS`. Runs until `ST:`. No auto-stop. |
| `FL:` | Full load to toolhead — runs forward at `FEED_SPS` until host sends `TS:1` (toolhead sensor). Guards: IN sensor must be present, other lane OUT must be clear. Timeout = `TC_LOAD_MS`. Emits `EV:LOADED:<lane>` on success, `EV:LOAD_TIMEOUT` on timeout. |
| `UL:` | Unload from extruder — runs reverse at `REV_SPS` until OUT sensor clears. Use when tip is past OUT (in bowden / extruder). |
| `UM:` | Unload from MMU — runs reverse at `REV_SPS` until IN sensor clears. Use when tip is inside the MMU path. |
| `CU:` | Run cutter sequence on active lane. Returns `ER:CUTTER_DISABLED` if `CUTTER` toggle is off. |
| `ST:` | Stop all motion immediately. Aborts toolchange and cutter. |

Both `UL:` and `UM:` stop automatically when the target sensor clears and emit `EV:UNLOADED:<lane>`. Both have a 30-second safety timeout (`EV:UNLOAD_TIMEOUT`).

---

## Lane / toolchange

| Command | Description |
|---------|-------------|
| `T:<1\|2>` | Set active lane without motion. |
| `TC:<1\|2>` | Full toolchange to lane N. Unloads current lane (cuts if `CUTTER=1`), swaps, then runs the new lane forward at `FEED_SPS` until toolhead sensor (`TS:1`). Returns `ER:NO_ACTIVE_LANE` if active lane is unknown. |

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
SPS:<n>        Current sync speed (steps/s)
BL:<n>         Baseline sync speed
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

| Parameter | Description | Default |
|-----------|-------------|---------|
| `FEED_SPS` | Feed speed (steps/s) | 5000 |
| `REV_SPS` | Reverse speed (steps/s) | 4000 |
| `AUTO_SPS` | Autoload speed (steps/s) | 6000 |
| `STARTUP_MS` | Stall arm delay after motion start (ms) | 10000 |
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
| `TC_CUT_MS` | Toolchange cut timeout (ms) | 5000 |
| `TC_UNLOAD_MS` | Toolchange unload timeout (ms) | 8000 |
| `TC_Y_MS` | Wait for Y-splitter to clear after unload (ms, 0 = skip) | 5000 |
| `TC_TH_MS` | Wait for `TS:` from host (ms, 0 = skip) | 3000 |
| `TC_LOAD_MS` | Toolchange load timeout (ms) | 15000 |
| `SYNC_MAX` | Max sync speed (steps/s) | 8000 |
| `SYNC_MIN` | Min sync speed (steps/s) | 0 |
| `SYNC_UP` | Sync ramp-up increment (steps/s per tick) | 300 |
| `SYNC_DN` | Sync ramp-down increment (steps/s per tick) | 150 |
| `SYNC_RATIO` | Buffer arm velocity → speed scale factor | 1.0 |
| `PRE_RAMP` | Pre-advance speed offset (steps/s) | 400 |
| `BUF_TRAVEL` | Half-travel of buffer arm (mm) | 5.0 |
| `BUF_HYST` | Buffer zone debounce (ms) | 30 |
| `BASELINE` | Baseline sync speed override (steps/s) | adaptive |

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
| `EV:RUNOUT` | `<lane>` | IN sensor lost while feeding |
| `EV:STALL` | `<lane>` | StallGuard triggered |
| `EV:TC:CUTTING` | `<lane>` | Toolchange: starting cut |
| `EV:TC:UNLOADING` | `<lane>` | Toolchange: unloading |
| `EV:TC:SWAPPING` | `<from>-><to>` | Toolchange: swapping lanes |
| `EV:TC:LOADING` | `<lane>` | Toolchange: loading new lane |
| `EV:TC:DONE` | `<lane>` | Toolchange completed |
| `EV:TC:ERROR` | `<reason>` | Toolchange failed |
| `EV:CUT:FEEDING` | — | Cutter feed phase started |
| `EV:BS` | `<zone>,<sps>` | Buffer sync update (500 ms interval) |

---

## Quick reference

```bash
# Status
python3 scripts/nightowl_test.py "?:"

# Load lane 1
python3 scripts/nightowl_test.py "T:1" "LO:"

# Unload from extruder (tip past OUT sensor)
python3 scripts/nightowl_test.py "UL:"

# Unload from MMU (tip inside MMU, before OUT sensor)
python3 scripts/nightowl_test.py "UM:"

# Toolchange to lane 2
python3 scripts/nightowl_test.py "TC:2"

# Tune stall threshold
python3 scripts/nightowl_test.py "SET:STARTUP_MS:500" "SG:1"

# Save settings
python3 scripts/nightowl_test.py "SV:"

# Reboot to BOOTSEL
python3 scripts/nightowl_test.py "BOOT:"
```
