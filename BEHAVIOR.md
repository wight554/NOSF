# NOSF – Behavioral Reference

This document describes *what the firmware does and why* — state transitions,
failure modes, interlocks, and recovery paths. For the command syntax see
`MANUAL.md`; for hardware pin assignments see `HARDWARE.md`.

---

## Filament states

Each lane tracks filament position inferred from its two sensors.

| IN | OUT | Meaning |
|----|-----|---------|
| 0  | 0   | Absent — filament clear of both sensors (or in transit window) |
| 1  | 0   | Pre-loaded — filament parked between IN and OUT (drive gear engaged) |
| 1  | 1   | Loaded — filament past OUT, in bowden or extruder |
| 0  | 1   | Tail between sensors — tip just cleared IN, body still at OUT |
| 0  | 0*  | In-transit — tail cleared both but within 1.2x DIST_IN_OUT of IN-clear point |

Pre-loaded is the normal parked state after `LO:` or autopreload completes.

---

## Boot sequence

1. Hardware init (GPIOs, PWM, TMC2209 UART).
2. `settings_load()` — restores runtime parameters from flash.
3. **Sensor settling** — `din_update()` is spun for 25 ms so the 10 ms
   debounce threshold can commit correct stable values.
4. **Active lane detection** — two-pass:
   - First pass: if exactly one lane has OUT triggered, that lane is active.
   - Fallback: if no OUT is triggered, check IN sensors — first lane with
     IN=1 and OUT=0 is selected (pre-loaded state).
   - If neither pass finds a lane, `active_lane` stays 0 (unknown).
5. `prev_*_in_present` is initialised from current sensor state so that
   autopreload does **not** re-trigger for filament already present at boot.
6. **Background buffer stabilization** — in dual-endstop mode, if the buffer
  starts in `ADVANCE` or `TRAILING`, firmware nudges it toward `MID` at
  `BUF_STAB_RATE` in the normal main loop. This no longer blocks USB command
  handling or the rest of the control loop during boot.

---

## Autopreload

Fires automatically when an IN sensor rises (filament freshly inserted).

**Conditions to start:**
- Lane is IDLE, no toolchange in progress, cutter not busy.
- That lane's OUT sensor is currently clear (not pre-loaded already).
- `AUTO_PRELOAD` runtime toggle is on (default: 1).

**What it does:**
- Starts `TASK_AUTOLOAD` at `AUTO_RATE` — drives filament forward until OUT
  triggers.
- On OUT trigger: reverses by `RETRACT_MM` (default 10 mm) and stops, leaving
  the tip just before OUT (pre-loaded state).
- Sets `active_lane` to this lane if the other lane's OUT is clear.

---

## Load commands

### `LO:` — Lane autoload (to pre-loaded state)

Runs `TASK_AUTOLOAD` at `AUTO_RATE` until OUT triggers, then retracts by
`RETRACT_MM`. Parks filament just before OUT.

### `FL:` — Full load to toolhead

Runs `TASK_LOAD_FULL` at `FEED_RATE` continuously until the host sends `TS:1`
(toolhead sensor triggered). OUT sensor is a non-stopping checkpoint.

**Interlocks checked before starting:**
- Active lane must be set — `ER:NO_ACTIVE_LANE`.
- IN sensor must be present — `ER:NO_FILAMENT`.
- Other lane must not be idle at OUT (filament blocking the path) —
  `ER:OTHER_LANE_ACTIVE`.

**Failure detection during `FL:`:**

| Condition | Timeout | Event |
|-----------|---------|-------|
| IN goes low >1 s after start | 1.2x DIST_IN_OUT | `EV:RUNOUT:<lane>` (waits for transit) |
| OUT never seen after 10 s | 10 s | `EV:RUNOUT:<lane>` |
| Buffer holds TRAILING after OUT for `TS_BUF_MS` | `TS_BUF_MS` | `EV:LOADED:<lane>` (fallback) |
| Load task exceeds travel limit | `LOAD_MAX` distance | `EV:LOAD_TIMEOUT:<lane>` |

---

## Unload commands

### `UL:` — Unload from extruder

Runs reverse at `REV_RATE` until OUT clears.
**Requires OUT to be triggered before starting** — returns `ER:NOT_LOADED` if
OUT is already clear.

If buffer enters `BUF_ADVANCE` during `UL:`, firmware performs a one-shot
stabilization sequence: stop reverse, feed forward gently by ~half buffer
travel (`BUF_TRAVEL`), then resume reverse unload. Recovery speed is controlled
by `BUF_STAB_RATE` (default 600 mm/min).

### `UM:` — Unload from MMU

Runs reverse at `REV_RATE` until IN clears.
Use this when the filament tip is between IN and OUT (pre-loaded state).

---

## Toolchange — `TC:<lane>`

Full automated cycle. Emits phase events at each step.

```
TC_IDLE
  → TC_UNLOAD_CUT       (if CUTTER=1: run cutter sequence)
  → TC_UNLOAD_REVERSE   (start TASK_UNLOAD on current lane)
  → TC_UNLOAD_WAIT_OUT  (wait for OUT to clear; lane task is bounded by `UNLOAD_MAX`)
  → TC_UNLOAD_WAIT_Y    (wait for Y-splitter to clear, if TC_Y_MS > 0)
  → TC_UNLOAD_WAIT_TH   (wait for TS:0 from host, if TC_TH_MS > 0)
  → TC_UNLOAD_DONE
  → TC_SWAP             (set active_lane = target)
  → TC_LOAD_START       (check Y-splitter clear; start TASK_LOAD_FULL)
  → TC_LOAD_WAIT_OUT    (non-stopping checkpoint)
  → TC_LOAD_WAIT_TH     (wait for TASK_LOAD_FULL to complete; lane task is bounded by `LOAD_MAX`)
  → TC_LOAD_DONE        → EV:TC:DONE:<lane>
```

---

## Motor acceleration ramp

All lane tasks start at `RAMP_STEP_RATE` (default 17 mm/min) and increment by
`RAMP_STEP_RATE` every `RAMP_TICK_MS` (default 5 ms) until the target rate is
reached.

### Buffer sync speed control

The sync controller runs every `SYNC_TICK_MS` (20 ms). In dual-endstop mode it
is no longer a simple proportional controller on `g_buf_pos`; it is now driven
primarily by an extruder-rate estimator and uses the buffer state as bounded
correction.

```
target = extruder_est_sps
       + zone_bias_sps
       + slope_bias_sps
       + PRE_RAMP_RATE  (if predict_advance_coming)

target = sync_apply_scaling(...)
target = clamp(target, SYNC_MIN_RATE, SYNC_MAX_RATE)
```

#### Velocity estimator

Whenever the buffer changes zone, firmware measures the dwell time in the old
zone and converts the arm travel into an estimated arm velocity. Combined with
the MMU speed averaged during that dwell, this yields an instantaneous
extruder-rate estimate.

- `MID→ADVANCE`, `ADVANCE→MID`, `MID→TRAILING`, `TRAILING→MID` use half-buffer
  travel.
- `ADVANCE→TRAILING` and `TRAILING→ADVANCE` use full-buffer travel.
- Half the hysteresis window is subtracted from dwell time before computing arm
  velocity so the estimate is not biased late.
- The instantaneous estimate is clamped to `SYNC_HARD_MAX_RATE` and merged into
  `extruder_est_sps` with an adaptive EMA bounded by `EST_ALPHA_MIN` and
  `EST_ALPHA_MAX`.
- A fast `ADVANCE→TRAILING` transition overwrites the estimator directly so a
  sudden demand collapse is reflected immediately.

If the buffer stays in MID for > 2 s, the estimator decays gently toward the
current MMU speed. This keeps the feed-forward term sane during long steady
sections where no new transitions arrive.

#### Zone bias and recovery behavior

`ZONE_BIAS_BASE` and `ZONE_BIAS_RAMP` provide a bounded centering pull:

- `BUF_ADVANCE` adds a positive bias, growing with time stuck in ADVANCE.
- `BUF_TRAILING` adds a negative bias, growing with time stuck in TRAILING.
- The total bias is capped by `ZONE_BIAS_MAX`.

This bias keeps the arm near MID when the estimator is slightly wrong, while
the estimator remains the dominant term.

#### Scaling, brake, and baseline adaptation

`sync_apply_scaling()` is a limiter on top of the estimator target:

- In analog-buffer mode, `g_buf_pos` scales the target between
  `TRAILING_RATE` and the requested target.
- In dual-endstop mode, the buffer zone alone shapes the target: ADVANCE
  enforces a floor and TRAILING enforces a ceiling.

On a direct `ADVANCE→TRAILING` transition, firmware arms a short fast-brake
window. During that window the sync target is forced to 0 before normal
TRAILING low-speed recovery resumes.

When the buffer returns to MID after a non-MID dwell and settles there for
> 500 ms, `g_baseline_sps` drifts toward the current speed. This baseline is
still used for bootstrapping and conservative limits, but it is no longer the
primary sync controller.

### RELOAD contact and follow

**`TC_RELOAD_APPROACH` — buffer-driven contact detection**

The motor runs at `JOIN_RATE` while the controller waits for the buffer to move
into `BUF_TRAILING`, which is treated as the first reliable sign that the new
lane has made contact and started pushing filament toward the extruder.

If contact never arrives, the approach phase still has hard escape paths: the
lane task has its configured travel limit and the RELOAD state machine has its
own timeout/abort logic, so RELOAD cannot run forever on a bad path or failed
sensor.

**`TC_RELOAD_FOLLOW` — pressure maintenance during bowden journey**

RELOAD follow no longer derives speed from driver-load telemetry.
Instead it reuses the normal sync estimator and deliberately under-feeds:

```
target = extruder_est_sps × RELOAD_LEAN
```

- Target is clamped between `TRAILING_RATE` and `JOIN_RATE`.
- For the brief post-touch boost window, firmware enforces a floor derived from
  `PRESS_RATE × RELOAD_TOUCH_FLOOR_PCT`.
- `BUF_TRAILING` keeps the motor at the low trailing push rate.
- `BUF_ADVANCE` or `TS:1` means the extruder has taken over, so follow exits.

Follow protection is now sensor- and timeout-driven: if the lane task faults or
the state exceeds `FOLLOW_TIMEOUT_MS`, RELOAD aborts instead of trying to infer
jam severity from driver load telemetry.

### Trailing behavior and auto-stop

`BUF_TRAILING` is now a low-speed recovery state, not an immediate hard stop.
Normal sync clamps toward `TRAILING_RATE`, and AUTO mode disables sync only if
TRAILING persists for `SYNC_AUTO_STOP_MS`.

**AUTO sync sequence:**

1. `BUF_ADVANCE` auto-starts sync in `AUTO_MODE` and seeds the estimator from
   the current baseline.
2. Normal sync runs from the estimator, bounded by buffer state.
3. Sustained `BUF_TRAILING` for `SYNC_AUTO_STOP_MS` disables sync and resets the
   estimator to 0.
4. The next `BUF_ADVANCE` event bootstraps sync again.

---

## Sync mode auto-toggle

In `AUTO_MODE`, buffer state is the primary sync toggle. `TS:` still matters for
load completion and RELOAD handover, but it is not the main sync controller.

| Event | Sync state |
|-------|-----------|
| `BUF_ADVANCE` while sync is off | enabled and bootstrapped |
| `UL:`, `UM:`, or `TC:` unload starts | disabled |
| sustained `BUF_TRAILING` for `SYNC_AUTO_STOP_MS` | disabled and estimator reset |
| `ST:` command | disabled |
---

## Dry Spin Protection

To prevent indefinite motor wear if filament is lost or snapped mid-task, the firmware implements a global "Dry Spin" watchdog.

**Conditions for `FAULT:DRY_SPIN`:**
- Motor is spinning (`task != TASK_IDLE`).
- `IN` sensor is clear (no filament present at intake).
- Buffer is **not** in `BUF_ADVANCE` (the printer is not successfully pulling a remaining tail).
- This state persists for > 8 seconds.

**Effects:**
- Motor stops immediately.
- `EV:FAULT:DRY_SPIN` is emitted.
- The lane enters a sticky fault state.

**Interlocks:**
While in `FAULT_DRY_SPIN`, automatic background tasks are blocked:
- **Sync Mode**: `sync_apply_to_active` will not restart the motor if it is faulted.
- **RELOAD Follow**: `TC_RELOAD_FOLLOW` will not restart the motor if it is faulted.

**Clearing the Fault:**
- **Manual Override**: Any manual motion command (`LO:`, `FL:`, `FD:`, etc.) automatically clears the fault and starts the requested task.
- **Auto-Reset**: Inserting new filament (`IN` sensor trigger) clears the fault, allowing `AUTO_PRELOAD` or `AUTO_LOAD` to proceed.
