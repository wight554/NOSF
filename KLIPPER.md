# NOSF — Klipper Integration

This document covers connecting Klipper to NOSF: serial setup, the shell
command helper, Klipper API motion tracking for calibration, toolhead sensor
and toolchange macros, and buffer/sync tuning.

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

## Telemetry and Tuning — nosf_live_tuner.py

Phase 2.7 adds high-speed diagnostic capture. Use `scripts/nosf_live_tuner.py --csv-out` to
stream internal state to a CSV file for offline analysis.

1. Start the tuner on the Pi:
   ```bash
   python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 --csv-out run1.csv --observe-daemon
   ```
2. Run your print.
3. Stop the tuner (Ctrl+C) or leave it running across prints.
4. Analyze the results with `scripts/nosf_analyze.py`:
   ```bash
   python3 scripts/nosf_analyze.py --in run1.csv --out patch.ini
   ```

## Calibration Prints

Phase 2.10 uses calibration prints to bake standalone defaults without pausing
Klipper on every feature marker. The normal flow is observe-only: generate a
sidecar, gather telemetry through the Klipper API socket, run the analyzer,
review a patch, merge chosen values into `config.ini`, rebuild, flash, then
detach the host.

Before running the first 2.9.9 build, please back up your state file:
```bash
cp ~/nosf-state/buckets-<id>.json ~/nosf-state/buckets-<id>.json.schema2.bak
```

Confirm the Klipper API socket path on the Pi:

```bash
ps -ef | grep '[k]lippy.py'
```

Use the `-a` argument from that command. Common modern installs use
`/home/pi/printer_data/comms/klippy.sock`; older examples may show
`/tmp/klippy_uds`.

Generate a sidecar next to the calibration G-code:

```bash
python3 scripts/gcode_marker.py input.gcode --output input.nosf.gcode \
    --emit sidecar
```

By default, layer changes are recognized (both `;LAYER:<n>` and OrcaSlicer
`;LAYER_CHANGE` comments). Use `--no-layer-markers` to disable.

Upload/print the generated `input.nosf.gcode`, and run the observe-only tuner:

```bash
python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 \
    --machine-id myprinter \
    --observe-daemon \
    --csv-out ~/nosf-runs/run1.csv \
    --klipper-uds /home/pi/printer_data/comms/klippy.sock \
    --sidecar /home/pi/printer_data/gcodes/input.nosf.json &
```

`--klipper-mode auto` is the default: the tuner tries the Klipper UDS first and
falls back to marker input if it is unavailable. Use `--klipper-mode on` when
you want a missing socket to fail fast, or `--klipper-mode off` for shell-marker
fallback testing. When both UDS and `--marker-file` are configured, UDS wins
after a sidecar is attached.

The sidecar stores the source G-code SHA-256. If the G-code is re-sliced or
edited without regenerating the sidecar, the tuner refuses to attach it and
prints a loud warning.

In observe mode the tuner persists its tracking state but sends no `SET:`
commands and no `SV:`.

Fallback shell-marker mode is still available for debugging older setups:

```bash
python3 scripts/gcode_marker.py input.gcode --output input.nosf.gcode \
    --emit file

python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 \
    --machine-id myprinter \
    --observe-daemon \
    --csv-out ~/nosf-runs/run1.csv \
    --klipper-mode off \
    --marker-file /tmp/nosf-markers-myprinter.log &
```

`--emit file` inserts `RUN_SHELL_COMMAND CMD=nosf_marker PARAMS="..."` lines.
`nosf_marker.py` appends each marker to `/tmp/nosf-markers-myprinter.log`, and
the tuner tails that file while it remains the only process owning
`/dev/ttyACM0`.
The tuner truncates `--marker-file` when it starts, so each calibration run
starts from fresh marker state. Add `--keep-marker-file` only when attaching to
a print that is already in progress.

Recommended analyzer pass after three or more runs:

```bash
python3 scripts/nosf_analyze.py \
    --in ~/nosf-runs/run1.csv ~/nosf-runs/run2.csv ~/nosf-runs/run3.csv \
    --state ~/nosf-state/buckets-myprinter.json \
    --out config.patch.ini \
    --acceptance-gate
```

If the patch is applied to `config.ini` and flashed, update the watermark:
```bash
python3 scripts/nosf_analyze.py --commit-watermark --state ~/nosf-state/buckets-myprinter.json
```

`nosf_live_tuner.py` owns the NOSF USB TTY. Do not run
multiple instances against the same `/dev/ttyACM*` at the same time.

Debug-only live writes still exist for controlled experiments:
`--allow-bias-writes` and `--allow-baseline-writes`.

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
