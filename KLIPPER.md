# NOSF — Klipper Integration

This document covers connecting Klipper to the NOSF: serial
setup, the shell command helper, toolhead sensor and toolchange macros, and the
advance profiling workflow for sync tuning.

For the NOSF command reference see `MANUAL.md`; for behavioral details see
`BEHAVIOR.md`.

---

## Serial port setup

The NOSF appears as a USB CDC serial device on the Raspberry Pi
(`/dev/ttyACM0` or `/dev/ttyACM1`).

Confirm the port:
```bash
ls /dev/ttyACM*
# identify which is which if more than one device:
dmesg | grep ttyACM
```

The Pi user must be in the `dialout` group:
```bash
sudo usermod -a -G dialout pi   # substitute your username if not 'pi'
# log out and back in for the change to take effect
```

---

## Shell command helper — nosf_cmd.py

`scripts/nosf_cmd.py` sends a single NOSF command and blocks until the
response arrives.  Simple commands (SET:, GET:, T:, SM:, TS:, SG:, FD:, ST:,
…) return on the first `OK:`/`ER:`.  Long-running commands (`TC:`, `FL:`,
`UL:`, `UM:`) wait for their completion event (`EV:TC:DONE`, `EV:LOADED`,
`EV:UNLOADED`, …) or the corresponding error/timeout event.  Exit code is 0 on
success, 1 on error or timeout.  All received lines are printed so Klipper's
`VERBOSE` output shows them in the Mainsail / Fluidd console.

Install the Klipper `gcode_shell_command` extension if not already present
(available via KIAUH → Advanced, or copy `gcode_shell_command.py` to
`~/klipper/klippy/extras/`).

Add to `printer.cfg`:
```ini
[gcode_shell_command nosf]
command: python3 /home/pi/NOSF/scripts/nosf_cmd.py
timeout: 130.0
verbose: True
```

Adjust the path to match your Pi home directory.  Test:
```
RUN_SHELL_COMMAND CMD=nosf PARAMS="?:"
```

---

## Toolhead filament sensor — TS:

NOSF needs to know when filament reaches or leaves the toolhead extruder.
This signal drives completion of `FL:`/`TC:` load phases and enables/disables
buffer sync.

### Option A — Physical sensor (recommended)

Wire a microswitch or optical sensor to a free GPIO on the printer MCU.  Add to
`printer.cfg`:

```ini
[filament_switch_sensor toolhead_sensor]
switch_pin: ^!toolhead:PA0   ; adjust pin and MCU name
pause_on_runout: False
insert_gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="TS:1"
runout_gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="TS:0"
```

### Option B — Buffer fallback (no sensor)

When filament presses against the extruder gears, the buffer arm holds TRAILING
for `TS_BUF_MS` milliseconds and NOSF self-triggers the loaded state.
Tune to your bowden length:

```
SET:TS_BUF_MS:2000    ; default 2000 ms
SV:
```

`TS:0` after unload is still required if you want sync to stop cleanly:
```ini
[gcode_macro NOSF_TS_CLEAR]
gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="TS:0"
```

---

## Toolchange macros — TC:

`TC:<lane>` unloads the current lane (optionally cuts), swaps, loads the new
lane, and waits for `TS:1`.  `nosf_cmd.py` blocks until `EV:TC:DONE` or
`EV:TC:ERROR`, so Klipper naturally pauses printing during the change.

```ini
[gcode_macro T1]
gcode:
    M400
    SAVE_GCODE_STATE NAME=_tc_state
    RUN_SHELL_COMMAND CMD=nosf PARAMS="TC:1"
    RESTORE_GCODE_STATE NAME=_tc_state

[gcode_macro T2]
gcode:
    M400
    SAVE_GCODE_STATE NAME=_tc_state
    RUN_SHELL_COMMAND CMD=nosf PARAMS="TC:2"
    RESTORE_GCODE_STATE NAME=_tc_state
```

> **Temperature management:** `gcode_shell_command` holds the Klipper scheduler
> while the shell process runs — heaters stay regulated, but no additional G-code
> is processed until the command returns.  Set `TC_LOAD_MS` and `TC_UNLOAD_MS`
> conservatively so a jam does not hold Klipper indefinitely.

If `TC:` returns an error, `nosf_cmd.py` exits with code 1.
`gcode_shell_command` logs the failure; add a PAUSE if you want automatic
handling:

```ini
[gcode_macro T1]
gcode:
    M400
    SAVE_GCODE_STATE NAME=_tc_state
    RUN_SHELL_COMMAND CMD=nosf PARAMS="TC:1"
    {% if printer['gcode_shell_command nosf'].return_code != 0 %}
        PAUSE
        { action_respond_info("NOSF TC:1 failed") }
    {% endif %}
    RESTORE_GCODE_STATE NAME=_tc_state
```

---

## Manual load / unload

```ini
[gcode_macro NOSF_LOAD]
description: Full load active lane to toolhead
gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="FL:"

[gcode_macro NOSF_UNLOAD]
description: Unload from extruder (tip past OUT sensor)
gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="UL:"

[gcode_macro NOSF_PRELOAD]
description: Pre-load active lane to parked position (OUT sensor)
gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="LO:"
```

---

## Sync mode

Buffer sync enables automatically when `TS:1` is received and disables when
unload starts.  No explicit `SM:` calls are normally needed.

For manual override — e.g., before tip-shaping retraction moves:
```ini
[gcode_macro SYNC_OFF]
gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="SM:0"

[gcode_macro SYNC_ON]
gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="SM:1"
```

---

## Advance profiling — tuning SG_TENSION_MAX

This one-time procedure calibrates `SG_TENSION_MAX`: the SG value at maximum
expected bowden tension, so the sync speed trim applies proportionally between
`SG_SYNC_THR` (threshold) and `SG_TENSION_MAX` (100 % correction point).

**Prerequisites:**
- `SG_SYNC_THR` already set — run `tune_stallguard.py --neutral` first (see `BEHAVIOR.md`)
- Filament loaded past the toolhead, extruder engaged
- Hotend at print temperature

### Using the Klipper macro (recommended)

The `NOSF_ADVANCE_PROFILE` macro drives the MMU motor and the extruder in
one coordinated sequence.  This avoids pushing filament into stationary extruder
gears — the extruder starts within milliseconds of the MMU motor.

Add to `printer.cfg`:

```ini
[gcode_macro NOSF_ADVANCE_PROFILE]
description: Advance profiling — drives MMU + extruder to calibrate SG_TENSION_MAX
gcode:
    {% set LANE = params.LANE|default(1)|int %}
    M83                                                 ; relative extrusion
    ; Setup
    RUN_SHELL_COMMAND CMD=nosf PARAMS="T:{LANE}"
    RUN_SHELL_COMMAND CMD=nosf PARAMS="SM:0"
    RUN_SHELL_COMMAND CMD=nosf PARAMS="SET:FEED:900"
    ; Start MMU motor, then immediately extrude at matched speed
    ; (< 200 ms gap = < 3 mm at 15 mm/s — within bowden slack)
    RUN_SHELL_COMMAND CMD=nosf PARAMS="FD:"
    G1 E100 F900                                        ; 15 mm/s matched — SG baseline, no tension
    ; Build tension progressively
    G1 E100 F1500                                       ; 25 mm/s — light tension
    ; Maximum tension: split move so SG is read mid-run while motor is still loaded
    G1 E50 F2400                                        ; 40 mm/s — first half
    RUN_SHELL_COMMAND CMD=nosf PARAMS="SG:{LANE}"   ; capture SG under tension
    G1 E50 F2400                                        ; 40 mm/s — second half
    RUN_SHELL_COMMAND CMD=nosf PARAMS="SG:{LANE}"   ; capture SG again
    ; Stop
    RUN_SHELL_COMMAND CMD=nosf PARAMS="ST:"
    { action_respond_info("Profiling complete. Use the lowest SG value above as SG_TENSION_MAX.") }
    { action_respond_info("Apply: python3 scripts/nosf_test.py SET:SG_TENSION_MAX:<value> SV:") }
```

Run from the Mainsail / Fluidd console:
```
NOSF_ADVANCE_PROFILE LANE=1
```

The two `SG:` reads appear in the console output.  The lower of the two values
is your `SG_TENSION_MAX`.  Apply it:
```
RUN_SHELL_COMMAND CMD=nosf PARAMS="SET:SG_TENSION_MAX:24"
RUN_SHELL_COMMAND CMD=nosf PARAMS="SV:"
```

> The SG read happens a few hundred milliseconds after each G1 segment
> completes — there is slight tension decay before the read.  If the value seems
> high (> 80 % of the free-spin SG), repeat the run and use the minimum across
> all readings.

### Using the SSH script (alternative — real-time monitoring)

If you prefer to watch SG in real time from a second terminal:

**Terminal 1 (SSH):**
```bash
cd ~/NOSF
python3 scripts/tune_stallguard.py --advance --lane 1
```
The script starts the MMU motor at 900 mm/min and polls SG every 100 ms.

**Terminal 2 (Mainsail console or second SSH):**
```gcode
M83
G1 E100 F900    ; matched speed — SG baseline
G1 E100 F1500   ; light tension
G1 E100 F2400   ; maximum tension
```
Press `Ctrl+C` in Terminal 1 when done.  The script prints the minimum SG.

> Do **not** run both the SSH script and the macro simultaneously — they both
> open the serial port and will conflict.

### Applying the result

```bash
python3 scripts/nosf_test.py "SET:SG_TENSION_MAX:24" "SV:"
```

Replace `24` with the lowest SG recorded under maximum tension.

### What this calibrates

```
SG ≥ SG_SYNC_THR                    →  0 % of SG_SYNC_TRIM
SG_SYNC_THR > SG > SG_TENSION_MAX   →  proportional (0–100 %)
SG ≤ SG_TENSION_MAX                 →  100 % of SG_SYNC_TRIM
```

Without a calibrated `SG_TENSION_MAX` (default 0), full trim fires only at
SG ≈ 0 (near-stall).  After profiling, the 100 % point matches your actual
bowden + extruder combination.

---

## ADVANCE buffer state tuning

After SG parameters are set, tune the buffer-based correction.

### SYNC_KP — proportional buffer correction

`SYNC_KP` (mm/min per unit arm deflection) is the main speed correction when
the buffer arm deflects toward the extruder side.  Default ≈ 851 mm/min.

Monitor buffer state during a print:
```bash
# EV:BS lines print every 500 ms: zone, sync speed, normalised arm position
python3 scripts/nosf_test.py "SM:1"
```

A healthy steady-state print:
```
EV:BS:MID,2125.5,0.01
EV:BS:MID,2126.0,-0.02
EV:BS:ADVANCE,2551.0,0.43    ← extruder accelerating
EV:BS:MID,2250.0,0.11        ← settling back
```

**If the arm stays at ADVANCE during steady extrusion:** increase `SYNC_KP`:
```
SET:SYNC_KP:1200
```

**If speed oscillates MID ↔ ADVANCE ↔ MID rapidly:** decrease `SYNC_KP`.

Target: arm at MID during steady extrusion, touching ADVANCE only on
acceleration ramps.  For a stiff bowden or fast printer, 1000–2000 mm/min is
typical.

### BUF_ALPHA — EMA weight for arm position

`BUF_ALPHA` (default 0.20) controls how quickly `g_buf_pos` ramps to the new
zone value (endstop sensors only).

| BUF_ALPHA | Time to 86 % correction from MID | Character |
|-----------|-----------------------------------|-----------|
| 0.10      | ~400 ms                           | Smooth, slow |
| 0.20      | ~200 ms                           | Default — balanced |
| 0.40      | ~100 ms                           | Fast; some overshoot risk |

Increase if ADVANCE correction builds too slowly.  Decrease if motor speed
oscillates.

> **TRAILING→MID:** Negative `g_buf_pos` is clamped to zero on return to MID,
> so recovery after a TRAILING stop is controlled by `SYNC_UP` alone.
> ADVANCE→MID retains the positive lag for smooth deceleration.

### SG_SYNC_TRIM — magnitude of tension correction

`SG_SYNC_TRIM` (default ≈ 17 mm/min) is the extra speed added at full tension.
After `SG_SYNC_THR` and `SG_TENSION_MAX` are calibrated, tune the magnitude:

1. Print at target speed with sync enabled.
2. If the buffer still frequently hits ADVANCE before `SYNC_KP` catches up,
   increase `SG_SYNC_TRIM`:
   ```
   SET:SG_SYNC_TRIM:300
   SET:SG_SYNC_TRIM:600    ; increase further if arm still swings to ADVANCE
   ```
3. If speed fluctuates during steady printing, raise `SG_SYNC_THR` slightly to
   narrow the trigger window.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `nosf_cmd.py` exits "no serial port found" | Port not present | `ls /dev/ttyACM*`; check `dialout` group |
| `TS:1` not reaching NOSF | Sensor wiring or config | Test: `RUN_SHELL_COMMAND CMD=nosf PARAMS="TS:1"` |
| `TC:` times out | Bowden too long / jam | Increase `TC_LOAD_MS` / `TC_UNLOAD_MS` |
| Sync not enabling after load | No `TS:1` sent | Check sensor or enable `TS_BUF_MS` fallback |
| SG flat through all profiling speeds | Bowden too long — tension dissipated | Shorten bowden; or use `SG_TENSION_MAX:0` (100 % trim at near-stall only) |
| SG drops at baseline F900 | Extruder pulling harder than MMU at matched speed | Lower MMU feed speed: edit macro `SET:FEED:600` |
| Lowest SG very high (> 80 % of free-spin) | Tension not fully building | Check filament is fully engaged in extruder; run profiling again |
