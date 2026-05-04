# NightOwl Controller – Behavioral Reference

This document describes *what the firmware does and why* — state transitions,
failure modes, interlocks, and recovery paths. For the command syntax see
`MANUAL.md`; for hardware pin assignments see `HARDWARE.md`.

---

## Filament states

Each lane tracks filament position inferred from its two sensors.

| IN | OUT | Meaning |
|----|-----|---------|
| 0  | 0   | Absent — no filament in this lane |
| 1  | 0   | Pre-loaded — filament parked between IN and OUT (drive gear engaged) |
| 1  | 1   | Loaded — filament past OUT, in bowden or extruder |
| 0  | 1   | In-transit — tip just cleared IN, body still at OUT (brief, during unload) |

Pre-loaded is the normal parked state after `LO:` or autopreload completes.

---

## Boot sequence

1. Hardware init (GPIOs, PWM, TMC2209 UART).
2. `settings_load()` — restores runtime parameters from flash.
3. **Sensor settling** — `din_update()` is spun for 25 ms so the 10 ms
   debounce threshold can commit correct stable values.  Without this step,
   sensors read by `gpio_get()` in `din_init()` may reflect power-on
   transients.
4. **Active lane detection** — two-pass:
   - First pass: if exactly one lane has OUT triggered, that lane is active.
   - Fallback: if no OUT is triggered, check IN sensors — first lane with
     IN=1 and OUT=0 is selected (pre-loaded state).
   - If neither pass finds a lane, `active_lane` stays 0 (unknown).
5. `prev_*_in_present` is initialised from current sensor state so that
   autopreload does **not** re-trigger for filament already present at boot.

---

## Autopreload

Fires automatically when an IN sensor rises (filament freshly inserted).

**Conditions to start:**
- Lane is IDLE, no toolchange in progress, cutter not busy.
- That lane's OUT sensor is currently clear (not pre-loaded already).
- `AUTO_PRELOAD` runtime toggle is on (default: 1).

**What it does:**
- Starts `TASK_AUTOLOAD` at `AUTO_SPS` — drives filament forward until OUT
  triggers.
- On OUT trigger: reverses by `RETRACT_MM` (default 10 mm) and stops, leaving
  the tip just before OUT (pre-loaded state).
- Sets `active_lane` to this lane if the other lane's OUT is clear.

Autopreload does **not** fire for filament that was already present at boot
(rising-edge detection, with `prev_*` initialised at startup).

---

## Load commands

### `LO:` — Lane autoload (to pre-loaded state)

Runs `TASK_AUTOLOAD` at `AUTO_SPS` until OUT triggers, then retracts
`RETRACT_MM`. Parks filament just before OUT. Does **not** load to toolhead.
Use this to pre-load a lane before a print starts.

### `FL:` — Full load to toolhead

Runs `TASK_LOAD_FULL` at `FEED_SPS` continuously until the host sends `TS:1`
(toolhead sensor triggered). OUT sensor is a non-stopping checkpoint.

**Interlocks checked before starting:**
- Active lane must be set — `ER:NO_ACTIVE_LANE`.
- IN sensor must be present — `ER:NO_FILAMENT`.
- Other lane must not be idle at OUT (filament blocking the path) —
  `ER:OTHER_LANE_ACTIVE`.

**Failure detection during `FL:`:**

| Condition | Timeout | Event |
|-----------|---------|-------|
| IN goes low >1 s after start | immediate | `EV:RUNOUT:<lane>` |
| OUT never seen after 10 s | 10 s | `EV:RUNOUT:<lane>` |
| Buffer holds TRAILING after OUT for `TS_BUF_MS` | `TS_BUF_MS` | `EV:LOADED:<lane>` (fallback) |
| `TS:1` never received | `TC_LOAD_MS` (60 s) | `EV:LOAD_TIMEOUT:<lane>` |

**Why two runout paths:**

- *Tail in drive gear* — a short disconnected piece is in the gear, gets
  pushed forward, passes IN within a second or two, then IN goes low.
  Detected by the 1 s IN-loss check.

- *Tail behind drive gear* — the IN sensor is positioned behind (spool side
  of) the drive gear.  If filament is at IN but not engaged in the gear, the
  motor spins freely, IN stays high, and OUT never triggers.  Detected by the
  10 s OUT checkpoint.  User must manually remove the tail from the IN sensor
  area.

---

## Unload commands

### `UL:` — Unload from extruder

Runs reverse at `REV_SPS` until OUT clears.
**Requires OUT to be triggered before starting** — returns `ER:NOT_LOADED` if
OUT is already clear.  Use this when the filament tip is past the OUT sensor
(inside the bowden or extruder).

### `UM:` — Unload from MMU

Runs reverse at `REV_SPS` until IN clears.
Use this when the filament tip is between IN and OUT (pre-loaded state).
No OUT precondition required.

**Choosing the right command:**

| Filament position | Command |
|-------------------|---------|
| Tip in extruder / bowden (OUT=1) | `UL:` |
| Tip parked before OUT (OUT=0, IN=1) | `UM:` |

Both commands emit `EV:UNLOADED:<lane>` on success or `EV:UNLOAD_TIMEOUT`
after `TC_UNLOAD_MS` (60 s).

---

## Toolchange — `TC:<lane>`

Full automated cycle. Emits phase events at each step.

```
TC_IDLE
  → TC_UNLOAD_CUT       (if CUTTER=1: run cutter sequence)
  → TC_UNLOAD_REVERSE   (start TASK_UNLOAD on current lane)
      if OUT already clear: skip motor, jump directly to post-unload
  → TC_UNLOAD_WAIT_OUT  (wait for OUT to clear, timeout = TC_UNLOAD_MS)
  → TC_UNLOAD_WAIT_Y    (wait for Y-splitter to clear, if TC_Y_MS > 0)
  → TC_UNLOAD_WAIT_TH   (wait for TS:0 from host, if TC_TH_MS > 0)
  → TC_UNLOAD_DONE
  → TC_SWAP             (set active_lane = target)
  → TC_LOAD_START       (check Y-splitter clear; start TASK_LOAD_FULL)
  → TC_LOAD_WAIT_OUT    (non-stopping checkpoint; error if task idles first)
  → TC_LOAD_WAIT_TH     (wait for TASK_LOAD_FULL to complete)
      success: toolhead_has_filament=true → TC_LOAD_DONE
      failure: task idled without TS:1 → TC error LOAD_TIMEOUT
  → TC_LOAD_DONE        → EV:TC:DONE:<lane>
```

**Pre-loaded unload shortcut:** If the current lane's OUT is clear when
`TC_UNLOAD_REVERSE` runs (filament parked before OUT), the motor is not
started at all — the unload phase is skipped immediately.  This avoids a
spurious motor pulse followed by an immediate stop.

**Error conditions** emit `EV:TC:ERROR:<reason>`:

| Reason | Cause |
|--------|-------|
| `NO_ACTIVE_LANE` | `active_lane` is 0 at TC start |
| `UNLOAD_TIMEOUT` | OUT did not clear within `TC_UNLOAD_MS` |
| `Y_TIMEOUT` | Y-splitter did not clear within `TC_Y_MS` |
| `HUB_NOT_CLEAR` | Y-splitter still occupied at load start |
| `LOAD_TIMEOUT` | TASK_LOAD_FULL timed out without `TS:1` |

---

## Motor acceleration ramp

All lane tasks start at `RAMP_STEP_SPS` (default 200 SPS) and increment by
`RAMP_STEP_SPS` every `RAMP_TICK_MS` (default 5 ms) until `target_sps` is
reached.  At 200 SPS / 5 ms the ramp to 25 000 SPS takes ~625 ms.

The autopreload retract (direction reversal after OUT trigger) also starts
from zero to avoid hammering the motor on direction change.

StallGuard is not armed until `STARTUP_MS` (default 10 s) after motion starts,
providing clearance for the ramp and bowden pressure to stabilise before stall
detection can fire.

---

## StallGuard

StallGuard detects motor **stall** (excess load).  It fires via DIAG GPIO IRQ
and emits `EV:STALL:<lane>`.

StallGuard is **not** useful for detecting absent filament (free-spinning
motor = low load = SG value high, no trigger).  Filament absence is detected
by sensor events (IN/OUT) as described in the load failure section above.

### How it works (TMC2209)

`SG_RESULT` is a 10-bit value produced continuously by the chip:
- **High value (~200–510)** → low motor load (free spin or light load).
- **Low value (~0–100)** → high load or stall.

The chip asserts the DIAG pin (triggering `EV:STALL`) when
`SG_RESULT ≤ 2 × SGTHRS`. `SGTHRS` is set per lane via `config.h`
(`CONF_SGT_L1`, `CONF_SGT_L2`) and is written to the TMC on init.
StallGuard is only active when motor speed is below `TCOOLTHRS` (steps/s);
above that speed it is suppressed by the chip to avoid false triggers.

### Tuning for stall detection

1. Run the motor at printing speed with filament loaded:
   ```
   T:1
   FD:
   ```
2. Read `SG_RESULT` repeatedly:
   ```
   SG:1
   SG:1
   SG:1
   ```
   Note the **minimum** value seen during normal, unobstructed run — call it `SG_RUN`.
3. Stop the motor (`ST:`).
4. Set `SGTHRS` so that `2 × SGTHRS` is roughly 50 % of `SG_RUN`.
   Use the TMC register write command (register 0x40 on TMC2209):
   ```
   TW:1:0x40:<value>
   ```
   Example: if `SG_RUN ≈ 160`, set `SGTHRS = 40` → threshold = 80 (50 % of 160):
   ```
   TW:1:0x40:40
   ```
5. Test: push filament against a hard stop by hand while the motor runs — confirm
   `EV:STALL:1` fires. If it fires during normal run, increase `SGTHRS`; if it
   never fires on a real stall, decrease it.
6. Persist the value by updating `CONF_SGT_L1` / `CONF_SGT_L2` in `config.h`.

`STARTUP_MS` (default 10 s) delays StallGuard arming after motion starts —
keep it above your ramp + bowden stabilisation time to prevent false triggers
at the beginning of a move.

`TCOOLTHRS` is compared against `TSTEP` (clock cycles per step, so it
*decreases* as speed increases).  StallGuard is **active** when
`TSTEP ≤ TCOOLTHRS`.  With the TMC2209 internal clock at ~12.5 MHz and a
feed rate of 25 000 SPS, `TSTEP ≈ 500`.  The default `CONF_TCOOLTHRS = 1000`
keeps StallGuard enabled across the full operating speed range whenever
`SGTHRS > 0`.  Set `TCOOLTHRS` lower than your minimum TSTEP to disable
StallGuard below a certain speed (useful to suppress false triggers during
ramp-up, though `STARTUP_MS` already handles that in firmware).

### Buffer + StallGuard combined speed control

The sync speed controller runs every 20 ms (`SYNC_TICK_MS`).  Each tick
computes an instantaneous **target speed**, then rate-limits the motor toward it.

#### Target speed computation

```
correction = SYNC_KP × g_buf_pos
           + PRE_RAMP  (if predict_advance_coming)
           + sg_frac × SG_SYNC_TRIM

where  sg_frac = clamp((SG_SYNC_THR − SG_RESULT) /
                       (SG_SYNC_THR − SG_TENSION_MAX),  0, 1)

target = clamp(baseline + correction, SYNC_MIN, SYNC_MAX)
```

The three additive correction terms:

| Term | Signal | What it measures | Typical latency to react |
|------|--------|-----------------|--------------------------|
| `SYNC_KP × g_buf_pos` | Buffer arm position | Velocity error (accumulated displacement) | 100–200 ms — arm must physically move |
| `sg_frac × SG_SYNC_TRIM` | `SG_RESULT` (motor load) | Instantaneous filament tension | ~20 ms — 1 tick after tension appears |
| `PRE_RAMP` | History of short MID dwells | Extruder repeatedly returning to MID quickly | 1–3 state transitions |

#### Motor speed rate-limiting

`target` is recalculated every tick and can jump as soon as a signal changes.
The motor does **not** jump to `target` immediately — it ramps:

```
each 20 ms tick:
    if sync_current_sps > target:  sync_current_sps −= SYNC_DN
    if sync_current_sps < target:  sync_current_sps += SYNC_UP
```

SG raises `target` within one tick of detecting tension — before the buffer arm
has moved far enough to register.  `SYNC_UP` then controls how fast the motor
actually reaches that new target.  The practical result: the motor begins
accelerating ~100 ms earlier than with buffer correction alone, reducing maximum
arm deflection.

#### TRAILING — both corrections disabled, SG polling continues

When the buffer is in TRAILING, `sync_current_sps` is forced to 0 each tick.
The target computation (buffer + SG corrections) is **skipped entirely**.

SG **polling still runs** during TRAILING — `g_sg_load` continues to track
motor load so the EMA is not stale when the motor restarts.  No speed
correction is applied from it until the buffer leaves TRAILING.

Applying SG correction during TRAILING would be counterproductive: the surplus
is mechanical (arm against endstop), not a tension problem.  The motor must stay
stopped until the extruder draws down the slack.

#### Baseline adaptation

When the buffer stays in MID for > 500 ms, `g_baseline_sps` drifts slowly
toward `sync_current_sps` via a low-weight EMA.  The baseline therefore
tracks the printer's long-term average feed rate automatically.  `SET:BASELINE`
overrides this adaptive tracking with a fixed value.

All three correction terms are deviations from this baseline — when the printer
runs steadily and the arm stays at MID, all corrections converge to zero and
`sync_current_sps` ≈ `baseline`.

#### Timeline during a printer acceleration event

| Time | Event | Buffer state | SG | Motor target | Motor speed |
|------|-------|--------------|----|--------------|-------------|
| 0 ms | Extruder begins accelerating | MID | normal | baseline | ≈ baseline |
| 20 ms | Tension builds in bowden (arm has not moved yet) | MID | SG drops below `SG_SYNC_THR` | +`SG_SYNC_TRIM` × sg_frac | ramps up at SYNC_UP |
| 100–200 ms | Arm reaches ADVANCE endstop | ADVANCE, g_buf_pos → +1 | SG still low | +`SYNC_KP` added on top | ramps up further |
| 300–400 ms | Motor catches extruder speed | arm returns toward MID | tension eases, SG recovers | both terms fall | ramps down at SYNC_DN |

Without SG correction the motor only reacts when the arm physically hits
ADVANCE (~100–200 ms in).  With SG, ramping begins at ~20 ms — the arm deflects
less and may never reach the ADVANCE endstop at all.

#### When SG correction is off

`SG_SYNC_THR` defaults to 0 (disabled).  `g_sg_load` is still tracked each tick
but adds nothing to the correction.  The buffer arm alone drives speed
corrections — no tuning required, but slower to react.

### Tuning `SG_SYNC_THR` and `SG_TENSION_MAX` (sync load trim)

Use `scripts/tune_stallguard.py --neutral` and `--advance` to profile SG values
automatically, then apply the recommendations.

1. Run `tune_stallguard.py --neutral` with filament loaded but not touching the
   extruder.  Pass `--feed-speed` matching your actual sync/print speed so the
   recommendation reflects free-spin SG at the speed the motor actually runs.
   It profiles the SpreadCycle range and recommends `SG_SYNC_THR` (~75% of
   free-spin SG at the target speed):
   ```
   python3 scripts/tune_stallguard.py --neutral --feed-speed 2400   # 40 mm/s max
   SET:SG_SYNC_THR:109    # example: free-spin SG ≈ 145 at 2400 mm/min
   ```
2. Run `tune_stallguard.py --advance` while commanding the extruder to pull
   faster than the MMU feeds.  Apply the recommended `SG_TENSION_MAX`:
   ```
   SET:SG_TENSION_MAX:20  # SG at maximum observed tension
   ```
   `SG_TENSION_MAX = 0` applies full trim only when `SG_RESULT = 0` (safe
   default before profiling).
3. Set `SG_SYNC_TRIM` to the extra speed you want at maximum tension.  Start
   conservatively (e.g., 500–1000 mm/min) and increase if the buffer still
   spikes to ADVANCE before the correction catches up:
   ```
   SET:SG_SYNC_TRIM:600
   ```
4. `SG_SYNC_THR:0` disables the trim entirely (safe default).

### Trailing state — motor stop and recovery

When the buffer enters the TRAILING zone (arm deflects toward "too much fed"),
`sync_current_sps` is forced to zero every sync tick (20 ms) until the buffer
returns to MID.  The SG correction loop does not run while the motor is
stopped.

**Recovery sequence:**

1. TRAILING declared (after `BUF_HYST` ms in zone) — motor stops within one
   sync tick (≤ 20 ms).
2. Motor stays stopped; extruder draws down the surplus in the buffer.
3. Buffer arm returns to MID — motor begins ramping from 0 toward the
   proportional target at `SYNC_UP` SPS per tick.
4. `g_buf_pos` still reflects the recently-trailing position (EMA lag with
   `BUF_ALPHA`), so the initial target is slightly below baseline.  This
   acts as a natural brake — the motor does not overshoot immediately back
   into ADVANCE.

**Why SYNC_UP matters for your print speed**

`SYNC_UP` (default 300 SPS/tick) is the ramp increment per 20 ms sync tick:

```
ramp rate  =  SYNC_UP (SPS) / 0.020 s  =  50 × SYNC_UP  SPS/s
time to speed  =  target_SPS / (50 × SYNC_UP)  seconds
```

At 40 mm/s the target is ~28 200 SPS; with the default SYNC_UP = 300 the ramp
takes **1.9 s**.  A 5 mm buffer half-travel is emptied by the extruder in
~125 ms at 40 mm/s — the motor cannot keep up and the buffer swings straight to
ADVANCE, causing TRAILING ↔ ADVANCE oscillation.

Recommended starting points (ramp to speed in ~0.5 s):

| Print speed | SPS (MM_PER_STEP = 0.001417) | SYNC_UP |
|-------------|------------------------------|---------|
| 20 mm/s (1200 mm/min) | ≈14 100 | 600 |
| 30 mm/s (1800 mm/min) | ≈21 200 | 850 |
| 40 mm/s (2400 mm/min) | ≈28 200 | 1200 |

```
SET:SYNC_UP:1200     # 40 mm/s printer
```

**Symptom → fix table:**

| Symptom | Cause | Fix |
|---------|-------|-----|
| Buffer oscillates TRAILING ↔ ADVANCE every few seconds | Ramp too slow — motor can't recover before buffer empties | Increase `SYNC_UP` |
| TRAILING fires on every corner / retraction | `BUF_HYST` too short or `SYNC_KP` too high | Increase `BUF_HYST` (100–200 ms) or reduce `SYNC_KP` |
| Motor overshoots into TRAILING right after recovery | Ramp too fast or `SYNC_KP` too high | Decrease `SYNC_UP` or `SYNC_KP` |
| TRAILING persists even after surplus should be consumed | Sensor fault or endstop wiring | Check `?:` — verify TRAILING endstop reads correctly |

**Monitoring TRAILING recovery:**

`EV:BS` events (every 500 ms) report `<state>,<speed mm/min>,<buf_pos −1..+1>`:
```
EV:BS:TRAILING,0.0,-0.84
EV:BS:MID,341.2,-0.54
EV:BS:MID,1024.5,-0.21
EV:BS:MID,2125.5,-0.01
```
A healthy recovery shows speed climbing and `buf_pos` converging to zero.
Oscillation (TRAILING → ADVANCE → TRAILING) means `SYNC_UP` is too low.

### Stall recovery during sync

A stall during buffer sync usually means a tension spike (extruder briefly
pulled harder than the correction could handle), not a physical jam.

**First stall** — the IRQ stops the motor, then `stall_pump` detects sync mode
and instead of hard-stopping:
- Sets `stall_recovery = true` (3 s window, `CONF_STALL_RECOVERY_MS`).
- Resets `sync_current_sps = 0` so the motor ramps up from zero.
- Emits `EV:STALL:<lane>` (host is informed regardless).
- `sync_tick` restarts the motor and the proportional controller — the fresh
  ramp gives the tension a chance to release before speed builds back up.

**Second stall within the recovery window** — the motor is hard-stopped and
`EV:STALL:<lane>` is emitted again.  This means a real jam.

Recovery is **disabled** (`CONF_STALL_RECOVERY_MS = 0`) when StallGuard is
not tuned (default `SGTHRS = 0` → DIAG never fires → recovery never triggers).

---

## Sync mode auto-toggle

Buffer sync is managed automatically based on toolhead filament state.

| Event | Sync state |
|-------|-----------|
| `TS:1` received | enabled |
| `FL:`/`TC:` load completes (TS:1 or buffer fallback) | enabled |
| `UL:`, `UM:`, or `TC:` unload starts | disabled |
| `FL:` command issued | disabled |
| `ST:` command | disabled |

Sync is never active during loading, unloading, or pre-load operations.
Autopreload (`LO:` or IN-sensor insert) on a non-active lane does **not**
interrupt sync running on the active lane.

**Manual override — `SM:`**

`SM:0` / `SM:1` override sync immediately and take effect until the next
automatic lifecycle event clears them.  Use this when you need sync off for
a specific operation without triggering a full load/unload cycle:

- *Tip shaping before TC:* send `SM:0`, run your tip-shaping moves, then
  issue `TC:` — the toolchange cycle auto-disables sync at start and
  auto-enables it when the new lane is loaded.
- *Manual extrusion test:* `SM:0` → `FD:` / `MV:` → `SM:1` when done.
- *Pause mid-print:* `SM:0` to stop sync; `SM:1` to resume (or re-send
  `TS:1` to let the firmware re-enable it automatically).

---

## Active lane tracking

`active_lane` (0 = unknown, 1 or 2) is changed by:

| Event | New value |
|-------|-----------|
| Boot: OUT sensor detected on exactly one lane | that lane |
| Boot fallback: IN=1, OUT=0 on a lane | first such lane (L1 priority) |
| `T:<n>` command | `n` |
| Autopreload insert, other lane OUT clear | inserted lane |
| `TC:` SWAP phase | target lane |

`active_lane = 0` blocks `LO:`, `FL:`, `FD:`, `UL:`, `UM:`, and `TC:`.
Set it manually with `T:1` or `T:2` if boot detection failed.

---

## Common workflows

### First use — both lanes pre-loaded (IN=1, OUT=0)

```
# Boot sets active_lane=1 automatically.
FL:          # load lane 1 to toolhead
TS:1         # confirm filament reached toolhead
```

### Switch lanes during a print

```
TC:2         # full toolchange: unload L1, load L2, wait for TS:1
TS:1         # confirm after filament reaches toolhead
```

### Recover from a failed load (EV:RUNOUT)

1. Manually remove the filament tail from the IN sensor area.
2. If filament is genuinely present, re-insert past the IN sensor.
3. Autopreload will trigger (if `AUTO_PRELOAD=1`), or run `LO:` manually.
4. Then `FL:` to load to toolhead.

### Recover from EV:STALL

StallGuard tripped — a jam is likely.  Stop (`ST:`), clear the blockage
manually, then restart the load sequence.
