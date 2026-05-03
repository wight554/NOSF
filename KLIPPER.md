# NightOwl — Advance Profiling with Klipper

This document covers tuning the SG-based sync speed correction and the ADVANCE
buffer state using Klipper's extruder as the load source.

For the command reference see `MANUAL.md`; for behavioral details see `BEHAVIOR.md`.

---

## Background

During printing, the MMU motor runs at a speed that tracks the printer's extrusion
rate via the buffer arm.  When the extruder pulls faster than the MMU feeds —
either transiently during acceleration, or because the baseline sync speed is
slightly low — the filament develops tension through the bowden.  That tension
shows up in `SG_RESULT` (lower = more load) before the buffer arm has moved far
enough to trigger a correction.

Two parameters control how the firmware responds to that early tension signal:

| Parameter | Role |
|-----------|------|
| `SG_SYNC_THR` | SG value below which tension trim activates |
| `SG_TENSION_MAX` | SG value at maximum expected operating tension — 100 % trim applied here |

Between `SG_SYNC_THR` and `SG_TENSION_MAX` the trim scales linearly.  Without a
calibrated `SG_TENSION_MAX`, full trim fires only at SG ≈ 0 (near-stall), which
is too late.  The advance profiling procedure sets `SG_TENSION_MAX` to match your
actual bowden + extruder combination.

---

## Prerequisites

- `SG_SYNC_THR` already set (run `tune_stallguard.py --neutral` first; see `BEHAVIOR.md`)
- Filament loaded past the toolhead, extruder engaged
- Hotend at print temperature
- SSH access to the Pi and a browser tab open to Mainsail / Fluidd

---

## Advance profiling procedure

You will run two sessions in parallel:

- **Terminal (SSH)**: runs the NightOwl profiling script, drives the MMU motor
- **Klipper console (Mainsail / Fluidd)**: extrudes filament at increasing speeds
  to create tension

### Step 1 — Start the profiling script (Terminal)

```bash
cd ~/nightowl-standalone-controller
python3 scripts/tune_stallguard.py --advance --lane 1
```

The script:
1. Disables sync (`SM:0`)
2. Sets feed speed to 900 mm/min (15 mm/s) and starts continuous forward motion (`FD:`)
3. Polls `SG_RESULT` every 100 ms, showing current and minimum values:
   ```
   Current SG: 142 | Lowest SG seen: 142
   ```

Leave this running.

### Step 2 — Extrude at increasing speeds (Klipper console)

Open the Mainsail or Fluidd console and run the following sequence.  Each
`G1 E100` move takes 4–7 s; wait for each to complete before issuing the next:

```gcode
M83             ; relative extrusion mode
G1 E100 F900    ; 15 mm/s — matched speed, no tension (SG stays high — this is the baseline)
G1 E100 F1500   ; 25 mm/s — light tension, SG should start dropping
G1 E100 F2400   ; 40 mm/s — maximum tension, SG should reach its floor
```

Watch the profiling terminal: `Lowest SG seen` should drop during F1500 and F2400
moves.  If it stays flat through all three moves, see [Troubleshooting](#troubleshooting).

### Step 3 — Record and apply (Terminal)

Press `Ctrl+C` to stop.  The script prints:

```
Lowest SG recorded under maximum tension: 24
Recommended SG_TENSION_MAX (100% trim applied at or below this SG): 24
  SET:SG_TENSION_MAX:24
```

Apply and save:
```bash
python3 scripts/nightowl_test.py "SET:SG_TENSION_MAX:24" "SV:"
```

> Run the procedure a second time to confirm the value is stable.  Normal
> variation between runs is ± 5–10 SG counts.  Use the lower of the two values.

---

## What the calibration does

```
SG ≥ SG_SYNC_THR              →  0 % of SG_SYNC_TRIM
SG_SYNC_THR > SG > SG_TENSION_MAX  →  proportional (0–100 %)
SG ≤ SG_TENSION_MAX            →  100 % of SG_SYNC_TRIM
```

`SG_SYNC_TRIM` (default ≈ 17 mm/min) is the extra speed added at full tension.
This is intentionally small by default; it acts as a fast inner-loop correction
that fires before the buffer arm reacts, preventing the arm from swinging to
ADVANCE in the first place.

---

## ADVANCE buffer state tuning

After SG parameters are set, tune the buffer-based correction.

### SYNC_KP — proportional buffer correction

`SYNC_KP` (mm/min per unit arm deflection) is the main speed correction when the
buffer arm deflects toward the extruder side.  When `g_buf_pos = +1` (full
ADVANCE), the correction is approximately `SYNC_KP` mm/min above baseline.
Default ≈ 851 mm/min.

Monitor buffer state during a print:
```bash
# EV:BS lines print every 500 ms: zone, sync speed, normalised arm position
python3 scripts/nightowl_test.py "SM:1"
# then watch serial output for EV:BS lines
```

A healthy steady-state print:
```
EV:BS:MID,2125.5,0.01
EV:BS:MID,2126.0,-0.02
EV:BS:ADVANCE,2551.0,0.43    ← extruder accelerating
EV:BS:MID,2250.0,0.11        ← settling back
```

**If the arm stays at ADVANCE during steady extrusion**: increase `SYNC_KP`:
```
SET:SYNC_KP:1200
```

**If speed oscillates MID ↔ ADVANCE ↔ MID rapidly**: decrease `SYNC_KP`.

Target: arm spends most time at MID, touching ADVANCE only on acceleration ramps.
For a stiff bowden or fast printer, 1000–2000 mm/min is typical.

### BUF_ALPHA — EMA weight for arm position

`BUF_ALPHA` (default 0.20) controls how quickly the firmware's internal arm
position ramps to the new zone value (as an EMA, 0 to 1).  For endstop sensors
only.

| BUF_ALPHA | Time to 86 % correction from MID | Character |
|-----------|-----------------------------------|-----------|
| 0.10      | ~400 ms                           | Smooth, slow |
| 0.20      | ~200 ms                           | Default — balanced |
| 0.40      | ~100 ms                           | Fast; some overshoot risk |

Increase if ADVANCE correction builds too slowly.  Decrease if speed oscillates.

> TRAILING→MID: negative `g_buf_pos` is clamped to zero on return to MID, so
> recovery after a TRAILING stop is controlled by `SYNC_UP` alone, not by
> `BUF_ALPHA`.  ADVANCE→MID retains the positive lag for smooth deceleration.

### SG_SYNC_TRIM — magnitude of tension correction

After `SG_SYNC_THR` and `SG_TENSION_MAX` are set, tune the correction magnitude:

1. Print at target speed with sync enabled.
2. If the buffer still frequently hits ADVANCE before `SYNC_KP` catches up,
   increase `SG_SYNC_TRIM`:
   ```
   SET:SG_SYNC_TRIM:300    ; conservative start
   SET:SG_SYNC_TRIM:600    ; increase if arm still swings to ADVANCE
   ```
3. If speed fluctuates during steady printing, raise `SG_SYNC_THR` slightly to
   narrow the trigger window.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Lowest SG seen` stays flat through all extruder speeds | Bowden too long — tension dissipated before reaching MMU | Shorten bowden; or increase `SG_SYNC_TRIM` and use `SG_TENSION_MAX:0` (fires at near-stall only) |
| SG drops immediately at F900 (baseline) | Extruder already pulling faster than MMU due to long bowden | Lower MMU feed speed: `--feed-speed 600` |
| Profiling shows SG = 0 throughout | SG not calibrated, or filament stuck | Verify filament is free; re-run `--neutral` to check baseline SG |
| `G1 E100 F2400` completes instantly | Extruder not homed / cold | Confirm hotend temperature; run `M109 S230` before extruding |
| Recommended SG_TENSION_MAX is very high (> 100) | Insufficient tension created | Check filament is fully engaged in extruder; increase test extruder speeds |
