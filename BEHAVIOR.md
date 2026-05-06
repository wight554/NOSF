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
   debounce threshold can commit correct stable values.
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
- Starts `TASK_AUTOLOAD` at `AUTO_RATE` — drives filament forward until OUT
  triggers.
- On OUT trigger: reverses by `RETRACT_MM` (default 10 mm) and stops, leaving
  the tip just before OUT (pre-loaded state).
- Sets `active_lane` to this lane if the other lane's OUT is clear.

---

## Load commands

### `LO:` — Lane autoload (to pre-loaded state)

Runs `TASK_AUTOLOAD` at `AUTO_RATE` until OUT triggers, then retracts
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
| IN goes low >1 s after start | immediate | `EV:RUNOUT:<lane>` |
| OUT never seen after 10 s | 10 s | `EV:RUNOUT:<lane>` |
| Buffer holds TRAILING after OUT for `TS_BUF_MS` | `TS_BUF_MS` | `EV:LOADED:<lane>` (fallback) |
| `TS:1` never received | `TC_LOAD_MS` (60 s) | `EV:LOAD_TIMEOUT:<lane>` |

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
  → TC_UNLOAD_WAIT_OUT  (wait for OUT to clear, timeout = TC_UNLOAD_MS)
  → TC_UNLOAD_WAIT_Y    (wait for Y-splitter to clear, if TC_Y_MS > 0)
  → TC_UNLOAD_WAIT_TH   (wait for TS:0 from host, if TC_TH_MS > 0)
  → TC_UNLOAD_DONE
  → TC_SWAP             (set active_lane = target)
  → TC_LOAD_START       (check Y-splitter clear; start TASK_LOAD_FULL)
  → TC_LOAD_WAIT_OUT    (non-stopping checkpoint)
  → TC_LOAD_WAIT_TH     (wait for TASK_LOAD_FULL to complete)
  → TC_LOAD_DONE        → EV:TC:DONE:<lane>
```

---

## Motor acceleration ramp

All lane tasks start at `RAMP_STEP_RATE` (default 17 mm/min) and increment by
`RAMP_STEP_RATE` every `RAMP_TICK_MS` (default 5 ms) until the target rate is
reached.

StallGuard is not armed until `STARTUP_MS` (default 1000 ms) after motion starts.

---

## StallGuard

StallGuard is used exclusively in **RELOAD mode** (dual-endstop buffer sensor)
to detect filament contact during the approach phase and to gate the DIAG
hard-stop fallback during follow.

### How it works (TMC2209)

`SG_RESULT` is a 10-bit value (0–511) produced continuously by the chip:
- **High value (~200–511)** → low motor load (free spin or light load).
- **Low value (~0–100)** → high load or stall.

The chip asserts the DIAG pin (triggering `EV:STALL`) when
`SG_RESULT ≤ 2 × SGTHRS`. `SGTHRS` is set per lane at runtime via
`SET:SGTHRS_L1:<value>` / `SET:SGTHRS_L2:<value>`.

`TCOOLTHRS` gates when SG_RESULT is computed: StallGuard is **active** when
`TSTEP ≤ TCOOLTHRS`.

### Buffer sync speed control

The sync speed controller runs every `SYNC_TICK_MS` (20 ms). Each tick
computes a target speed from the buffer arm position and rate-limits the motor
toward it.

```
correction = SYNC_KP_RATE × g_buf_pos
           + PRE_RAMP_RATE  (if predict_advance_coming)

target = clamp(baseline + correction, SYNC_MIN_RATE, SYNC_MAX_RATE)
```

#### Baseline adaptation

When the buffer stays in MID for > 500 ms, `g_baseline_sps` (internal baseline)
drifts slowly toward the current speed, automatically tracking the
printer's long-term average feed rate. `SET:BASELINE_RATE` overrides this.

#### TRAILING — motor stops

When the buffer is in TRAILING, the motor stops until the extruder draws down
the buffer surplus.

### StallGuard in Sync modes

StallGuard provides tension-based speed feedback. It can be used in two ways:
1. **Normal Sync (`SYNC_SG_INTERP=1`)**: Interpolates speed based on `SG_TARGET` even before the buffer arm moves significantly.
2. **RELOAD Mode**: Uses both soft-contact detection and speed interpolation.

| Layer | Mechanism | What it catches |
|-------|-----------|-----------------|
| Soft contact | SG_RESULT MA derivative vs `SG_DERIV` | Gentle tip-to-tail touch |
| Hard contact | DIAG interrupt via `SGTHRS` (`SGTHRS_L1`/`SGTHRS_L2`) | Jams, hard crashes |

**`TC_RELOAD_APPROACH` — contact detection at approach speed**

The motor runs at `JOIN_RATE`. The per-tick MA derivative is compared to
`SG_DERIV`: a sharp negative drop **AND** a raw value below 50% of `SG_TARGET`
triggers handoff to follow sync. This ensures that minor path friction does not
cause premature triggering.

**`TC_RELOAD_FOLLOW` — pressure maintenance during bowden journey**

Speed is interpolated linearly from the filtered SG:

```
sg_frac   = clamp(SG_RESULT / SG_TARGET,  0, 1)
target    = PRESS_RATE × sg_frac
```

- `SG ≥ SG_TARGET` → full `PRESS_RATE`
- `SG = 0` → 0 mm/min
- `BUF_TRAILING` caps speed to `TRAILING_RATE`
- `BUF_ADVANCE` (extruder pulling faster than we push) → handover detected, exit

A stall during follow (DIAG fires) drops speed to `TRAILING_RATE`.

### Trailing state — motor stop and recovery

When the buffer enters the TRAILING zone, the motor stops.

**Recovery sequence:**

1. TRAILING declared — motor stops.
2. Motor stays stopped; extruder draws down the surplus.
3. Buffer arm returns to MID — motor begins ramping from 0 toward the
   proportional target at `SYNC_UP_RATE` mm/min per tick.

---

## Sync mode auto-toggle

Buffer sync is managed automatically based on toolhead filament state.

| Event | Sync state |
|-------|-----------|
| `TS:1` received | enabled |
| `FL:`/`TC:` load completes | enabled |
| `UL:`, `UM:`, or `TC:` unload starts | disabled |
| `FL:` command reloadued | disabled |
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
