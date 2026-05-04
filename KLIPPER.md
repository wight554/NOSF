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

## ISS StallGuard tuning (`ISS_SG_TARGET`, `ISS_SG_DERIV_THR`)

StallGuard is used **only in ISS (Endless Spool) mode** — it is not involved in
normal buffer sync.  Two parameters control it:

| Parameter | Role |
|-----------|------|
| `ISS_SG_DERIV_THR` | Approach contact sensitivity: SG drop per tick that fires the handoff from fast approach to follow sync |
| `ISS_SG_TARGET` | Follow sync setpoint: motor speed scales from `ISS_PRESS_SPS` (SG ≥ target) down to 0 (SG = 0) |

Run the tuning script from SSH on the Pi:

```bash
# Minimum: free-air baseline only
python3 ~/NOSF/scripts/tune_iss_sg.py --lane 1

# Recommended: include contact calibration
python3 ~/NOSF/scripts/tune_iss_sg.py --lane 1 --contact

# Apply settings automatically
python3 ~/NOSF/scripts/tune_iss_sg.py --lane 1 --contact --apply
```

See `BEHAVIOR.md` → *Tuning ISS StallGuard* for the full procedure and
fine-tuning table.

---

## Buffer sync tuning

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

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `nosf_cmd.py` exits "no serial port found" | Port not present | `ls /dev/ttyACM*`; check `dialout` group |
| `TS:1` not reaching NOSF | Sensor wiring or config | Test: `RUN_SHELL_COMMAND CMD=nosf PARAMS="TS:1"` |
| `TC:` times out | Bowden too long / jam | Increase `TC_LOAD_MS` / `TC_UNLOAD_MS` |
| Sync not enabling after load | No `TS:1` sent | Check sensor or enable `TS_BUF_MS` fallback |
| ISS approach never detects contact | `ISS_SG_DERIV_THR` too high or SG disabled | Run `tune_iss_sg.py --lane N --contact`; verify `CONF_SGT_L1/L2 > 0` in config.h |
| ISS approach fires immediately (false trigger) | `ISS_SG_DERIV_THR` too low | Increase `ISS_SG_DERIV_THR`; or increase `CONF_ISS_SG_MA_LEN` to smooth noise |
| ISS follow sync motor stops mid-bowden | SG dropping to 0 (hard friction) | Check PTFE routing; reduce `ISS_PRESS_SPS` |
