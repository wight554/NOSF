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
travel (`BUF_HALF_TRAVEL`), then resume reverse unload. Recovery speed is controlled
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
tracks a virtual buffer position in millimeters instead of treating `MID` as
the steady-state target. The controller still uses the extruder-rate estimator,
but it now drives toward a buffered-reserve target on the trailing side.

```
target = extruder_est_sps
  + reserve_correction_sps
  + zone_bias_sps
  + slope_bias_sps
  - overshoot_trim_sps
  + PRE_RAMP_RATE  (if predict_advance_coming)

target = sync_apply_scaling(...)
target = clamp(target, SYNC_MIN_RATE, SYNC_MAX_RATE)
```

#### Velocity estimator

Whenever the buffer changes zone, firmware measures the dwell time in the old
zone and converts the switch-threshold travel into an estimated arm velocity.
Combined with the MMU speed averaged during that dwell, this yields an
instantaneous extruder-rate estimate.

- `BUF_HALF_TRAVEL` is the switch distance from `MID`, not the total arm half-travel.
- `BUF_SIZE / 2` is the physical half-travel used to clamp the virtual
  position beyond the switch.
- `MID→ADVANCE`, `ADVANCE→MID`, `MID→TRAILING`, `TRAILING→MID` use the switch
  threshold distance.
- `ADVANCE→TRAILING` and `TRAILING→ADVANCE` use twice the switch threshold.
- Half the hysteresis window is subtracted from dwell time before computing arm
  velocity so the estimate is not biased late.
- The instantaneous estimate is clamped to `GLOBAL_MAX_RATE` and merged into
  `extruder_est_sps` with an adaptive EMA bounded by `EST_ALPHA_MIN` and
  `EST_ALPHA_MAX`.
- A fast `ADVANCE→TRAILING` transition overwrites the estimator directly so a
  sudden demand collapse is reflected immediately.

If the buffer stays in MID for > 2 s, the estimator decays gently toward the
current MMU speed. This keeps the feed-forward term sane during long steady
sections where no new transitions arrive.

The `EA:` field in the `?:` status response exposes the estimator age in
milliseconds since the last meaningful update. `ES:` and `EC:` expose the current
estimator sigma (uncertainty in mm) and confidence percentage. A large `EA:`
value while the arm is in `BUF_MID` is normal; a large `EA:` while in `BUF_ADVANCE`
may indicate the bleed path is the only update source.

#### Zone bias and recovery behavior

In dual-endstop mode, firmware anchors the virtual position to the switch edge
on each transition, then integrates the mismatch between estimated extruder
draw and commanded MMU feed inside the physical travel envelope.

The normal sync target is not `MID`. It is a buffered-reserve target on the
trailing side set by `SYNC_RESERVE_PCT`, expressed as a percentage of
`BUF_HALF_TRAVEL`. Firmware also keeps a small built-in center guard on top of
that percentage target so steady sync stays slightly farther away from the
advance-side switch. This keeps reserve in the buffer without hard-coding a
deep hidden-margin target into firmware.

`ZONE_BIAS_BASE` and `ZONE_BIAS_RAMP` provide a bounded reserve-recovery pull:

- If the virtual position is more depleted than the target, sync adds positive
  correction to refill the buffer.
- If the virtual position is fuller than the target, sync removes speed and can
  apply extra trailing-side trim.
- The total bias is capped by `ZONE_BIAS_MAX`.
- `SYNC_OVERSHOOT_PCT` adds extra braking only after reserve overshoots into
  the full/trailing side.

This bias keeps the arm near the desired reserve target when the estimator is
slightly wrong, while the estimator remains the dominant term.

The `RT:` and `RD:` fields in `?:` status expose the current reserve target
and deadband in mm, so tuning of `SYNC_RESERVE_PCT`, `BUF_HALF_TRAVEL`, and
`SYNC_KP_RATE` can be observed in real time. `AD:` and `TD:` expose how long
the arm has been continuously pinned at the advance or trailing endstop. `TW:`
shows estimated time-to-trailing-wall in ms (99999 when not applicable).

Phase 2.5 adds a low-gain integral centering term (`RI:`) to correct for slow
rate mismatches that could otherwise settle the arm near the advance side over
long runs. This term is active only in `BUF_MID` when estimator confidence is
high. It is capped by `SYNC_INT_CLAMP` and frozen during pin events, toolchanges,
or low-confidence dwells. `RC:` shows the active gain percentage (0% = disabled
or frozen). If the integral saturates toward the advance side,
`EV:SYNC,ADV_DWELL_WARN` is emitted as an upstream warning before an advance
pin occurs.

After a deep negative reserve excursion, firmware also latches a
positive-relaunch damp state. During that state, positive reserve correction
and positive zone bias stay reduced until two conditions are both true:

- the minimum relaunch hold time has elapsed
- reserve error has actually unwound back near the reserve target

This avoids a late refill re-acceleration when real print slowdowns or
retractions produce a long recovery that would otherwise outlive the old
fixed-time damp window.

The damp predicate is now stateless — it is derived purely from the hold
timer and current conditions, with no write-back on read. This makes it
safe to call from status dumps and other observers without affecting control
state.

#### Advance-dwell guard

If the buffer arm is continuously pinned at the advance endstop for longer
than `SYNC_ADV_RAMP_MS` (default 400 ms), the sync controller bypasses the
estimator ceiling and forces the target speed toward `SYNC_MAX_RATE`. This
prevents the circular estimator ceiling that could otherwise stall refill
when the extruder is outpacing the MMU.

If the arm remains pinned for longer than `SYNC_ADV_STOP_MS` (default 6000
ms), sync auto-stops with `EV:SYNC,ADV_DWELL_STOP`. This is the safety net
for genuine extruder-overload conditions where no amount of speed increase
will refill the buffer. `SYNC_ADV_STOP_MS: 0` disables the hard stop.

The `AD:` status field exposes the current advance-dwell timer in real time
for tuning and regression monitoring.

#### Scaling, brake, and baseline adaptation

`sync_apply_scaling()` is a limiter on top of the estimator target:

- In analog-buffer mode, `g_buf_pos` scales the target between
  `TRAILING_RATE` and the requested target.
- In dual-endstop mode, the virtual reserve target shapes the controller.
  If the estimated position moves past the target into “too full”, sync tapers
  the requested target down toward `TRAILING_RATE` across the remaining
  full-side virtual travel instead of dropping there in one step.
- The controller also computes a dynamic trailing-wall time from remaining
  physical margin and current relative push. If time-to-wall collapses while
  sync is still driving toward `TRAILING`, firmware adds urgency trim and can
  immediately auto-stop AUTO sync instead of waiting for a long static trailing
  dwell once the condition becomes critically unsafe.

On a direct `ADVANCE→TRAILING` transition, firmware arms a short fast-brake
window. During that window the sync target is forced to 0 before normal
TRAILING low-speed recovery resumes.

When the buffer returns to MID after a non-MID dwell and settles there for
> 500 ms, the runtime control baseline drifts toward the current speed. The
configured `BASELINE_RATE` remains a separate bootstrap target and persistence
value; the learned runtime baseline cannot pull control below that configured
floor. AUTO start seeds sync from that floor and no longer overwrites the
configured baseline with `BUF_STAB_RATE`.

#### Buffer signal abstraction

Each `buf_sensor_tick()` cycle produces a `buf_signal_t` snapshot in `g_buf_signal`. It carries normalized position (`pos_norm` in −1..+1), physical position in mm (`pos_mm`), a confidence score (0.0..1.0), the current zone, the source kind (`BUF_SRC_VIRTUAL_ENDSTOP` or `BUF_SRC_ANALOG`), and a fault flag.

- **Virtual-endstop sources** (Phase 2.5): Confidence is derived from a physics-based sigma model. Uncertainty (`ES`) grows as the square root of integrated motion steps. Re-anchoring at a switch threshold resets uncertainty to a baseline (0.05 mm). Confidence (`EC`) decays as uncertainty approaches `EST_SIGMA_CAP`.
- **Analog sources**: Confidence drops to 0.5 if the signal saturates at a rail for more than 250 ms; it returns to 1.0 once the signal moves off the rail.

The `SK:` field in `?:` status exposes the source kind. `CF:` exposes the current source confidence score, while `EC:` and `ES:` provide the internal estimator certainty.

The sensor kind may not be changed while sync is active, a toolchange is in progress, or either lane is running. The `SET:BUF_SENSOR:n` command returns `ER:BUSY` if any of these conditions are true at the time of the call (D4).

### RELOAD contact and follow

After the old lane tail clears `OUT` and the Y path is clear, firmware waits
`RELOAD_JOIN_MS` before `RELOAD:JOINING` starts. This RELOAD-only grace period
lets the printer pull the old tail clear of unsupported buffer geometry before
the new lane begins its join approach.

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
It benefits from the same estimator and virtual-position updates, but its speed
policy stays deliberately trailing-centric and does not inherit the normal-sync
reserve target:

```
target = extruder_est_sps × RELOAD_LEAN
```

- Target is clamped between `TRAILING_RATE` and `JOIN_RATE`.
- First contact enters a brief settle window at `TRAILING_RATE` instead of
  jumping straight to `PRESS_RATE`.
- After that settle window, firmware enforces the post-touch boost floor
  derived from `PRESS_RATE × RELOAD_TOUCH_FLOOR_PCT` only if the buffer has
  already relaxed out of `BUF_TRAILING`.
- RELOAD completion accepts either the debounced buffer state or an
  instantaneous `BUF_ADVANCE` pulse, so a brief real pickup event is not lost
  behind normal buffer hysteresis.
- `BUF_TRAILING` keeps the motor at the low trailing push rate.
- `BUF_ADVANCE` or `TS:1` means the extruder has taken over, so follow exits.
- RELOAD follow also watches geometry-aware trailing-wall time. If the lane is
  still pushing deeper into the trailing wall and the predicted remaining time
  collapses, `FOLLOW_JAM` is raised early instead of waiting only on the static
  `FOLLOW_TIMEOUT_MS` dwell.

Follow protection is now sensor- and timeout-driven: if the lane task faults or
the state exceeds `FOLLOW_TIMEOUT_MS`, RELOAD aborts instead of trying to infer
jam severity from driver load telemetry.

### Trailing behavior and auto-stop

`BUF_TRAILING` is now a valid low-speed recovery state, not an immediate hard
stop. In normal print sync, entering `BUF_TRAILING` latches a recovery phase:
sync caps speed below the estimator until the buffer returns to `MID`, then
applies a brief re-acceleration bump. If trailing recovery still persists, the
controller tightens that cap and ramps down more aggressively until sync hits
its trailing floor. The hard trailing-wall guard still remains the true stop
path if recovery cannot pull the buffer back safely.

`SYNC_AUTO_STOP_MS` is no longer a generic normal-sync trailing dwell timeout.
Instead:

- tail-assist auto-starts still stop if `BUF_TRAILING` persists for
  `SYNC_AUTO_STOP_MS`;
- normal auto-started print sync requires **continuous `TRAILING` dwell** exceeding 
  `SYNC_AUTO_STOP_MS` **and** that the recovery speed has collapsed to the minimum 
  trailing-floor speed (ignoring micro-fluctuations). The configured `SYNC_AUTO_STOP_MS` 
  applies directly without relying on an internal deadman multiplier since the 
  dwell timer no longer falsely resets.

The same low-speed stabilization helper used at boot can also be run on demand
with `BS:` when the controller is idle.

In idle loaded states, firmware also runs a negative-sync / retract-sync flow:
if the raw buffer state is `TRAILING`, it can wait `POST_PRINT_STAB_MS`
(legacy name, now used as the idle trailing delay), then reverse slowly until
the raw buffer reaches `MID`. If the move somehow overshoots before the
control loop catches that center crossing, firmware falls back to the
advance-side handoff and then settles the buffer back toward `MID`.

**AUTO sync sequence:**

1. `BUF_ADVANCE` auto-starts sync in `AUTO_MODE` and seeds the estimator from
   the current baseline.
2. If the active lane is in the `IN=0`, `OUT=1` tail-between-sensors state,
  that same auto-start acts as a temporary tail-clear assist so the printer's
  pull can drag the remaining filament past `OUT`.
3. Once `OUT` clears in that assist path, firmware disables sync immediately
  and then continues with the normal `RUNOUT` / optional RELOAD handling.
4. Normal sync runs from the estimator, bounded by buffer state.
5. During normal print sync, `BUF_TRAILING` enters a bounded recovery phase
  until the buffer returns to `MID`; if that recovery persists, sync ramps down
  aggressively toward the trailing floor.
6. During tail assist, sustained `BUF_TRAILING` for `SYNC_AUTO_STOP_MS`
  disables sync.
7. During normal auto-started print sync, sustained `BUF_TRAILING` only
   disables sync after the continuous dwell exceeds `SYNC_AUTO_STOP_MS`
   and the controller speed has collapsed to the trailing-floor limit.
8. The next eligible `BUF_ADVANCE` event bootstraps sync again.

---

## Sync mode auto-toggle

In `AUTO_MODE`, buffer state is the primary sync toggle. `TS:` still matters for
load completion and RELOAD handover, but it is not the main sync controller.

| Event | Sync state |
|-------|-----------|
| `BUF_ADVANCE` while sync is off | enabled and bootstrapped |
| `UL:`, `UM:`, or `TC:` unload starts | disabled |
| tail-assist `BUF_TRAILING` for `SYNC_AUTO_STOP_MS` | disabled and estimator reset |
| normal-sync `BUF_TRAILING` at trailing-floor speed for `SYNC_AUTO_STOP_MS` | disabled and estimator reset |
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
