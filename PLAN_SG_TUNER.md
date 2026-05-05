# StallGuard Auto-Tuner — Implementation Plan

## Overview
A Python script (`scripts/sg_tuner.py`) that runs a live sweep over SGT values while the
printer executes a speed-varying test model, records SG readings at each (speed, SGT) pair,
then fits a model to recommend optimal SGT vs. speed and motor parameters.

---

## Phase 1 — Data Collection

**Serial protocol (already exists):**
- `NOSF CMD:SG_VALUE` → current raw SG reading
- `NOSF CMD:SET:SGT:<n>` → change SGT on the fly
- `NOSF CMD:STATUS` → speed, task, buf state

**Collection loop:**
1. User starts print of speed-varying model (e.g. Kickstarter test cube — has bridging, perimeters, infill at different speeds).
2. Script connects to `/dev/ttyACM*`, polls STATUS at ~10 Hz.
3. Every N seconds (configurable, default 2 s) step SGT by ±1 within a safe range (e.g. −20..+20).
4. Record tuple: `(timestamp_ms, speed_mm_min, sgt, sg_raw, buf_state, task)`.
5. Write to `sg_tuner_data_<timestamp>.csv` incrementally (no data loss on crash).

**Safety guards:**
- Skip SGT changes when `task != TASK_FEED` or `buf_state == TRAILING`.
- Clamp SGT to `[sgt_min, sgt_max]` CLI args.
- Abort if RUNOUT event received.

---

## Phase 2 — Analysis

**Goal:** find `SGT(speed)` curve that keeps SG readings in a target band
(configurable, default 200–400 — stall-sensitive but not noisy).

**Approach:**
1. Bin samples by speed (50 mm/min buckets).
2. For each bin: fit `sg_raw = f(sgt)` — expect roughly linear or monotone.
3. Find SGT value that maps mean SG into the target band per bin.
4. Fit a smooth curve across speed bins (polynomial or isotonic regression via `scipy`).

**Output:** recommended `sgt` (single value or speed-dependent table), printed + optionally
written back to `config.ini`.

---

## Phase 3 — Motor-Parameter Normalization (optional, flag-gated)

**Motivation:** allow transferring a tuned curve from one motor to another without re-running
the full sweep.

**Motor constants used:**
```
resistance (R, Ω)
inductance (L, H)
holding_torque (T, Nm)
max_current (I, A)
steps_per_revolution
```

**Normalization idea:**
- Back-EMF at speed v: `V_bemf ∝ v * sqrt(L) / R`  (first-order proxy)
- Normalized speed: `v_norm = v / (R / sqrt(L))`
- Fit the SG-vs-speed curve in normalized coordinates.
- To apply to a new motor: scale `v_norm` back using its R/L.

**Motor database:** a small TOML/INI file (`scripts/motors.ini`) with known motor entries,
keyed by name (matching Klipper convention). Script accepts `--motor <name>` to load constants.

---

## CLI Interface

```
python3 scripts/sg_tuner.py \
  --port /dev/ttyACM0 \
  --output sg_data.csv \
  --sgt-min -20 --sgt-max 20 \
  --target-sg-low 200 --target-sg-high 400 \
  --motor fysetc-g36hsy4405-6d-1200 \
  --motors-db scripts/motors.ini \
  [--analyze-only sg_data.csv]   # skip collection, just fit
```

`--analyze-only` lets Gemini/offline analysis run without a live printer.

---

## Files

| File | Purpose |
|---|---|
| `scripts/sg_tuner.py` | Main script: collect, analyze, recommend |
| `scripts/motors.ini` | Motor constant database |
| `scripts/sg_tuner_plot.py` | Optional: matplotlib visualization of collected data |

---

## Dependencies

- `pyserial` (already used by flash scripts)
- `numpy`, `scipy` (fitting)
- `matplotlib` (optional plotting)

---

## Key Implementation Notes

- Use `NOSF CMD:SG_VALUE` in a tight read loop; parse `SG_VALUE:<n>` response.
- SGT changes must be throttled — motor needs ~200 ms to settle after SGT write before
  reading is meaningful. Add configurable `--settle-ms 300`.
- The SG reading is speed-dependent by design; always record instantaneous speed alongside
  each reading (from STATUS).
- Stall threshold = SG drops to 0 for several consecutive reads. Detect and log as an event;
  exclude those samples from the fit.
- Phase 3 normalization is additive — Phase 1+2 must work standalone without motor constants.
