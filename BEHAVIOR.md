# NOSF – Behavioral Reference

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

### Buffer sync speed control

The sync speed controller runs every `SYNC_TICK_MS` (20 ms).  Each tick
computes a target speed from the buffer arm position and rate-limits the motor
toward it.

```
correction = SYNC_KP × g_buf_pos
           + PRE_RAMP  (if predict_advance_coming)

target = clamp(baseline + correction, SYNC_MIN, SYNC_MAX)
```

| Term | Signal | Latency |
|------|--------|---------|
| `SYNC_KP × g_buf_pos` | Buffer arm position | 100–200 ms — arm must move |
| `PRE_RAMP` | History of short MID dwells | 1–3 state transitions |

#### Baseline adaptation

When the buffer stays in MID for > 500 ms, `g_baseline_sps` drifts slowly
toward `sync_current_sps` via a low-weight EMA, automatically tracking the
printer's long-term average feed rate.  `SET:BASELINE` overrides this with a
fixed value.

#### TRAILING — motor stops

When the buffer is in TRAILING, `sync_current_sps` is forced to 0 each tick.
The motor stays stopped until the extruder draws down the buffer surplus.

### StallGuard in ISS (Endless Spool) mode

**SG is not used during normal buffer sync.**  Buffer arm position alone drives
the sync speed controller.

ISS uses StallGuard in two complementary ways:

| Layer | Mechanism | What it catches |
|-------|-----------|-----------------|
| Soft contact | SG_RESULT MA derivative vs `ISS_SG_DERIV_THR` | Gentle tip-to-tail touch before filament stalls |
| Hard contact | DIAG interrupt via `SGTHRS` (`SGT_L1`/`SGT_L2`) | Jams, hard crashes, cases the derivative misses |

Stall detection (`stall_armed`) is enabled **immediately** when the approach motor
starts — bypassing the `STARTUP_MS` warmup used in normal sync.  This is safe
because `TCOOLTHRS` gates StallGuard below operating speed: DIAG cannot fire
during the slow ramp-up, only once the motor reaches approach speed.

**`TC_ISS_APPROACH` — contact detection at approach speed**

The motor runs at `ISS_JOIN_SPS`.  SG is sampled into a 5-sample moving-average
ring buffer every `SYNC_TICK_MS`.

*Primary path (soft contact — 2-endstop only):*
The per-tick MA derivative is compared to `ISS_SG_DERIV_THR`: a sharp negative
drop means the new tip just hit the old tail.  Transition to follow sync fires
immediately — before the buffer arm moves and before the motor stalls — so
filament is not ground at approach speed.

*Hard-contact fallback (any buffer type):*
If the DIAG pin fires (`SG_RESULT ≤ 2 × SGTHRS`) — a hard crash at approach
speed — the stall IRQ stops the motor and sets `FAULT_STALL`.  The approach
loop detects this, clears the fault, and transitions to follow sync.  This
catches contacts the derivative misses (slow contact rate, noisy SG signal) and
provides a safety net even when `ISS_SG_DERIV_THR = 0`.

`BUF_TRAILING` is an additional fallback (physical buffer sensor driven).

**`TC_ISS_FOLLOW` — pressure maintenance during 1 m bowden journey**

Speed is interpolated linearly from the filtered SG (2-endstop only):

```
sg_frac   = clamp(SG_RESULT / ISS_SG_TARGET,  0, 1)
target    = ISS_PRESS_SPS × sg_frac
```

- `SG ≥ ISS_SG_TARGET` → full `ISS_PRESS_SPS` (light/no contact, catch up)
- `SG = 0` → 0 SPS (hard contact, let old tail lead)
- `BUF_TRAILING` caps speed to `ISS_TRAILING_SPS` regardless of SG
- `BUF_ADVANCE` (extruder pulling faster than we push) → handover detected, exit

A stall during follow (DIAG fires again) is treated as a pressure spike, not an
error: the fault is cleared and speed drops to `ISS_TRAILING_SPS`.

With an analog buffer sensor the arm position provides continuous pressure
feedback; SG_RESULT is not sampled.  SGTHRS/DIAG still fires on hard jams.

### Tuning ISS StallGuard (`ISS_SG_TARGET`, `ISS_SG_DERIV_THR`, `SGT_L1`/`SGT_L2`)

Use `scripts/tune_iss_sg.py`.  Filament must be loaded in the lane with the tip
free (not touching anything).

```bash
# Step 0 — observe free-air SG and verify StallGuard is active:
python3 scripts/sg_monitor.py --lane 1 --iss
# Then manually press filament against the tip to see the SG drop live.
# If SG stays at 0 across all speeds, check TCOOLTHRS covers operating speed.

# Step 1 — free-air baseline only:
python3 scripts/tune_iss_sg.py --lane 1

# Step 2 — add contact calibration (required for accurate SGTHRS):
python3 scripts/tune_iss_sg.py --lane 1 --contact

# Step 3 — apply and save in one pass:
python3 scripts/tune_iss_sg.py --lane 1 --contact --apply

# Repeat for lane 2 (SGT_L2 is per-lane; global params use more conservative):
python3 scripts/tune_iss_sg.py --lane 2 --contact --apply
```

**What the script measures and recommends:**

| Parameter | Scope | Formula | Purpose |
|-----------|-------|---------|---------|
| `ISS_SG_TARGET` | Global | `free_air / 2` | Follow sync pressure setpoint |
| `ISS_SG_DERIV_THR` | Global | `40 % of drop / tick` | Soft contact sensitivity in approach |
| `SGT_L{N}` (SGTHRS) | Per-lane | `contact_floor / 2` | Hard-contact DIAG fallback — fires when SG ≤ 2 × value |

**Why `contact_floor / 2` for SGTHRS:**
TMC fires DIAG when `SG_RESULT ≤ 2 × SGTHRS`.  Setting `SGTHRS = contact_floor / 2`
means DIAG fires exactly at the hard-contact floor — the point where filament is
already jammed, not just lightly touching.

#### Tuning `SGT_L1` / `SGT_L2` (SGTHRS) in detail

**Goal:** DIAG should fire when filament is hard-jammed or crashed into a wall,
*not* on gentle tip-to-tail contact (that is the SG derivative's job).  Set
SGTHRS too high and DIAG fires on soft contacts, competing with the derivative.
Set it too low and real jams go undetected.

**Step 1 — Observe free-air SG at approach speed**

Run `sg_monitor.py` with `--iss` to automatically use the ISS approach speed
stored on the device.  Filament must be loaded with the tip free — not touching
anything:

```bash
python3 scripts/sg_monitor.py --lane 1 --iss
```

The script reads `ISS_JOIN_SPS` from the device, converts to mm/min, and prints
the computed speed before starting.  If the device is not connected, use
`--speed` with the value from `GET:ISS_JOIN_SPS × MM_PER_STEP × 60`.

Let the motor settle for 3–5 s before touching anything.  Note the stable SG
reading — call it `SG_FREE`.  Typical values are 80–300 depending on
`RUN_CURRENT_MA` and bowden friction.

**Step 2 — Observe jam SG floor**

Keep `sg_monitor.py` running from Step 1.

**Use firm finger grip — not a rigid wall crash.**  In a real ISS splice the new
tip hits the old tail inside a PTFE tube, not concrete.  If you calibrate
against a rigid surface (vice, frame), SG drops to near zero and SGTHRS ends up
at 1–3, meaning DIAG only fires when the motor is already fully stalled and
filament is being ground.  Finger grip produces the SG level that corresponds to
"motor straining hard against a filament junction" — exactly the point where
DIAG should intervene.

Grip the moving filament firmly between thumb and index finger about 10–20 cm
from the NOSF exit.  Squeeze as hard as you can sustain for 3–5 s.  You should
feel the motor straining but not hear it completely stop.

```
  3.2s    198   100%   [########################################]  ← free air
  6.1s    155    78%   [███████████████████████████████.........]  ← light grip
  6.8s     62    31%   [████████████............................]  ← firm grip
  7.1s     18     9%   [███▌...................................]  ← maximum grip, motor straining
  7.3s      4     2%   [▌.......................................]  ← motor fully stalled (too far)
```

**Use the stable plateau** at maximum grip (~18 above) as `SG_JAM`.  The reading
will continue falling toward zero if you hold long enough — that is the motor
stalling, past the calibration target.  Release before the motor stops
completely.

Press Ctrl+C.  The session summary shows `SG floor` and computes a suggested
`SGT_L{N}` from it:

```
Session summary  (lane 1, 2120 mm/min):
  SG peak  (free-air estimate) : 198
  SG floor (min observed)      : 4
  Observed drop                : 194  (97%)
  Suggested SGT_L1             : 9  (DIAG fires at SG ≤ 18)
```

Because the script tracks the absolute minimum it may suggest a value from the
full-stall territory.  If the plateau you observed was higher (e.g. 18), compute
manually: `SGT_L1 = 18 / 2 = 9`.

**Step 3 — Calculate and set SGTHRS**

```
SGTHRS = SG_JAM / 2
```

DIAG fires when `SG_RESULT ≤ 2 × SGTHRS = SG_JAM`.

Example: `SG_JAM = 28` → `SGTHRS = 14`.

```
SET:SGT_L1:14
SV:
```

**Step 4 — Verify**

Run the motor again at approach speed, then press the tip into the same surface:

```bash
python3 scripts/sg_monitor.py --lane 1 --speed 2120
```

While pressing hard, watch for the motor to stop abruptly.  From the NOSF
serial console you should see `EV:STALL:1` fired and the motor halted.  If
`EV:STALL` fires on light contact, increase `SGT_L1`; if it never fires on a
hard jam, decrease it.

**Interaction with the SG derivative**

The derivative and SGTHRS are complementary, not competing:

- **Derivative fires first** on any contact that drops SG at a rate greater than
  `ISS_SG_DERIV_THR` per tick.  The motor transitions to follow sync *before*
  SG reaches the SGTHRS level — DIAG never fires.
- **DIAG fires** only when SG drops below `2 × SGTHRS` without the derivative
  catching it first — slow/gradual contact, very noisy SG, or a derivative
  threshold set too high.

As long as `SG_JAM < SG_SOFT_CONTACT` (hard jams produce lower SG than soft
touches — which is always true), there is no conflict between the two
mechanisms.  Setting `SGTHRS = SG_JAM / 2` ensures DIAG stays well below the
soft-contact SG level.

**Per-lane note**

`SGT_L1` and `SGT_L2` are independent.  If the two lanes have different
bowden friction or run at different currents, repeat Steps 1–4 for each lane
separately.  A lane with higher friction shows a lower free-air SG; its SGTHRS
should be proportionally lower.

**Manual adjustment after tuning:**

| Symptom | Fix |
|---------|-----|
| Follow sync pushes too hard (jams) | Decrease `ISS_SG_TARGET` |
| Follow sync barely touches (tips separate) | Increase `ISS_SG_TARGET` |
| Approach misses soft contacts | Decrease `ISS_SG_DERIV_THR` |
| Approach triggers prematurely on speed noise | Increase `ISS_SG_DERIV_THR` |
| Approach grinds filament before stopping | Decrease `SGT_L{N}` (fire DIAG earlier) |
| DIAG fires during free-spin approach | Increase `SGT_L{N}` or check TCOOLTHRS |

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
