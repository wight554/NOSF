# Task: SYNC_REFACTOR_PLAN — Phased Implementation

Implementing the approved sync refactor plan from SYNC_REFACTOR_PLAN.md.
Maintainer decisions D1–D7 are hard constraints (see SYNC_REFACTOR_PLAN.md §0).

... (previous content) ...

## Phase 2.7 — Implementation + Baseline Tuning

### Goal
Implement trailing-bias setpoint shift, mid-zone creep, and variance-aware position blend.
Restore telemetry pipeline for offline analysis and auto-tuning.
All behavior default-OFF or legacy-equivalent until operator-tuned.
Settings version 46u → 47u.

### Sub-phases completed
- **2.7.0 Trailing-Bias Setpoint Shift:** Added `sync_trailing_bias_frac`. Updated `buf_target_reserve_mm()` and integrated gate logic.
- **2.7.1 Mid-Zone Creep:** Added `mid_creep_timeout_ms`, `mid_creep_rate_sps_per_s`, `mid_creep_cap_frac`. Active wall-seek during MID dwells.
- **2.7.2 Variance-Aware Position Blend:** Added `buf_variance_blend_frac`, `buf_variance_blend_ref_mm`. Bayesian pull toward setpoint on full distrust.
- **2.7.3a MARK: Command:** Added `MARK:<tag>` and `MK:seq:tag` status for telemetry correlation.
- **2.7.3b G-code Marker:** Restored `scripts/gcode_marker.py` with `--every-layer` flag.
- **2.7.3c Logger:** Added `scripts/nosf_logger.py` for high-speed CSV capture.
- **2.7.4 Analyzer:** Added `scripts/nosf_analyze.py` for offline auto-tuning.
- **2.7.5 PID:** Deferred (documented skip in `SYNC_REFACTOR_PLAN.md`).

### Files modified
- `config.ini`, `config.ini.example`
- `scripts/gen_config.py`, `scripts/gcode_marker.py` (new), `scripts/nosf_logger.py` (new), `scripts/nosf_analyze.py` (new)
- `firmware/include/controller_shared.h`, `firmware/include/tune.h` (generated)
- `firmware/src/main.c`, `firmware/src/sync.c`, `firmware/src/protocol.c`, `firmware/src/settings_store.c`
- `MANUAL.md`, `BEHAVIOR.md`, `KLIPPER.md`, `README.md`, `CONTEXT.md`, `SYNC_REFACTOR_PLAN.md`

### Final baseline tunables (converged)
- `sync_trailing_bias_frac: 0.4`
- `mid_creep_timeout_ms: 4000`
- `mid_creep_rate_sps_per_s: 5`
- `mid_creep_cap_frac: 10`
- `buf_variance_blend_frac: 0.5`
- `buf_variance_blend_ref_mm: 1.0`

## Phase 2.7 Status: DONE — completed integration of all sub-phases.
