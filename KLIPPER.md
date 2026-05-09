# NOSF — Klipper Integration

This document covers connecting Klipper to NOSF: serial setup, the shell
command helper, toolhead sensor and toolchange macros, and buffer/sync tuning.

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
response arrives. Simple commands (SET:, GET:, T:, SM:, TS:, FD:, ST:,
…) return on the first `OK:`/`ER:`. Long-running commands (`TC:`, `FL:`,
`UL:`, `UM:`) wait for their completion event (`EV:TC:DONE`, `EV:LOADED`,
`EV:UNLOADED`, …) or the corresponding error/timeout event. Exit code is 0 on
success, 1 on error or timeout. All received lines are printed so Klipper's
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

[gcode_shell_command nosf_marker]
command: python3 /home/pi/NOSF/scripts/nosf_marker.py --file /tmp/nosf-markers-myprinter.log
timeout: 2.0
verbose: False
```

Adjust the path to match your Pi home directory. Test it with:
```
RUN_SHELL_COMMAND CMD=nosf PARAMS="?:"
```

---

## Toolhead filament sensor — TS:

NOSF needs to know when filament reaches or leaves the toolhead extruder.
This signal drives completion of `FL:`/`TC:` load phases and enables/disables
buffer sync.

### Option A — Physical sensor (recommended)

Wire a microswitch or optical sensor to a free GPIO on the printer MCU. Add to
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
lane, and waits for `TS:1`. `nosf_cmd.py` blocks until `EV:TC:DONE` or
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
> is processed until the command returns. Keep `LOAD_MAX` / `UNLOAD_MAX`
> conservative enough that a jam cannot hold Klipper indefinitely, and tune
> `TC_TH_MS` / `TC_Y_MS` only for the host-facing wait phases.

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
unload starts. No explicit `SM:` calls are normally needed.

For manual override, for example before tip-shaping retraction moves:
```ini
[gcode_macro SYNC_OFF]
gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="SM:0"

[gcode_macro SYNC_ON]
gcode:
    RUN_SHELL_COMMAND CMD=nosf PARAMS="SM:1"
```

---

## Buffer sync tuning

### BASELINE_RATE and SYNC_KP_RATE

The current sync controller is estimator-driven. `BASELINE_RATE` seeds and
stabilizes the controller around your expected steady-state print speed, while
`SYNC_KP_RATE` adds bounded correction when the buffer keeps leaning away from
MID.

Monitor buffer state during a print:
```bash
# EV:BS lines print every 500 ms: zone, sync speed, normalised arm position
python3 scripts/nosf_cmd.py "?:"
```

A healthy steady-state print:
```
EV:BS:MID,2100.0,0.01
EV:BS:MID,2100.0,-0.02
EV:BS:ADVANCE,2500.0,0.43    ← extruder accelerating
EV:BS:MID,2250.0,0.11        ← settling back
```

**If the arm stays at ADVANCE during steady extrusion:** first raise `BASELINE_RATE`, then increase `SYNC_KP_RATE` only if recovery is still too weak:
```bash
python3 scripts/nosf_cmd.py "SET:BASELINE_RATE:2300" "SET:SYNC_KP_RATE:1200"
```

**If speed oscillates MID ↔ ADVANCE ↔ MID rapidly:** decrease `SYNC_KP_RATE` or lower `BASELINE_RATE` if the whole controller is biased too fast.

Target: MID during steady extrusion, with brief ADVANCE / TRAILING excursions
only on real flow changes.

### BUF_ALPHA — EMA weight for arm position

`BUF_ALPHA` (default 0.20) controls how quickly `g_buf_pos` ramps to the new
zone value (endstop sensors only).

| BUF_ALPHA | Time to 86 % correction from MID | Character |
|-----------|-----------------------------------|-----------|
| 0.10      | ~400 ms                           | Smooth, slow |
| 0.20      | ~200 ms                           | Default — balanced |
| 0.40      | ~100 ms                           | Fast; some overshoot risk |

Increase if ADVANCE correction builds too slowly. Decrease if motor speed
oscillates.

---

## Telemetry and Tuning — nosf_logger.py

Phase 2.7 adds high-speed diagnostic capture. Use `scripts/nosf_logger.py` to
stream internal state to a CSV file for offline analysis.

1. Start the logger on the Pi:
   ```bash
   python3 scripts/nosf_logger.py --port /dev/ttyACM0 --out run1.csv
   ```
2. Run your print.
3. Stop the logger (Ctrl+C).
4. Analyze the results with `scripts/nosf_analyze.py`:
   ```bash
   python3 scripts/nosf_analyze.py --in run1.csv --out patch.ini
   ```

## Closed-Loop Live Tuning

Phase 2.8 adds `scripts/nosf_live_tuner.py` for online bucket learning during
tuning prints. It consumes the same `NOSF_TUNE` markers as the logger, learns
per `feature_v_fil` buckets, and writes guarded live `SET:` updates for
trailing bias. Runtime baseline writes are disabled by default because `EST` is
a live flow estimate, not a safe global baseline target during a print.

Recommended tuning-print invocation:

```bash
python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 \
    --machine-id myprinter --commit-on-idle \
    --marker-file /tmp/nosf-markers-myprinter.log &
```

`--commit-on-idle` waits until NOSF reports idle for at least 30 s, then sends
`SET:LIVE_TUNE_LOCK:0`, sends `SV:`, emits `/tmp/nosf-patch.ini`, logs that path
to stderr, and exits. Review the patch before merging it into repo
`config.ini`. The emitted baseline suggestion is commented as experimental;
keep the known-good baseline unless you validate a new target manually.

For live tuning, preprocess with file markers so Klipper never opens the NOSF
USB serial port for marker delivery:

```bash
python3 scripts/gcode_marker.py input.gcode --output input.nosf.gcode --emit file
```

`--emit file` inserts `RUN_SHELL_COMMAND CMD=nosf_marker PARAMS="..."` lines.
`nosf_marker.py` appends each marker to `/tmp/nosf-markers-myprinter.log`, and
the tuner tails that file while it remains the only process owning
`/dev/ttyACM0`. `--commit-on-idle` waits for the final `FINISH` marker from
`gcode_marker.py` before it tries to save or emit a patch.

`--emit m118` remains available for passive console/log workflows. `--emit mark`
forwards markers through firmware `MARK:`/`MK:`, but should not be used while
the live tuner owns the serial port.

`nosf_live_tuner.py` and `nosf_logger.py` both own the NOSF USB TTY. Do not run
them against the same `/dev/ttyACM*` at the same time. Use the live tuner for
tuning prints, and use `nosf_logger.py` for passive reference soaks or debugging
runs where no online writes should occur.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `nosf_cmd.py` exits "no serial port found" | Port not present | `ls /dev/ttyACM*`; check `dialout` group |
| `TS:1` not reaching NOSF | Sensor wiring or config | Test: `RUN_SHELL_COMMAND CMD=nosf PARAMS="TS:1"` |
| `TC:` times out | Bowden too long / jam | Increase `LOAD_MAX` / `UNLOAD_MAX` if travel is genuinely too short; otherwise tune `TC_TH_MS` / `TC_Y_MS` or fix the path |
| Sync not enabling after load | No `TS:1` sent | Check sensor or enable `TS_BUF_MS` fallback |
| RELOAD approach never detects contact | Buffer sensor never reaches `TRAILING` | Verify buffer wiring and travel; reduce `JOIN_RATE` if the path is too aggressive |
| RELOAD approach exits too early | Buffer sensor chatter or preload already trailing | Verify hysteresis/sensor state and make sure the standby path starts with real slack |
| RELOAD follow times out mid-bowden | Drag too high or follow speed too low | Check PTFE routing; reduce `PRESS_RATE` or increase `FOLLOW_TIMEOUT_MS` |
