# Sync Refactor Specification

## Purpose
Durable contract for NOSF sync, tuning, tracking, and analyzer.

## Requirements

### REQ: Standalone Sync
RUN sync/TC/RELOAD without host after cal flash.
- **SCEN: Host Detached**: cal reviewed + flashed -> RUN from firmware/runtime ONLY. NO host tuner req.
- **SCEN: Live Debug**: `nosf_live_tuner.py` without writes -> OBSERVE ONLY. NO `SET`/`SV`.

### REQ: Observe-Only Cal
Collect markers + buckets + patches without mutation unless explicit.
- **SCEN: Default Tuner**: markers arrive -> update state/CSV. NO firmware writes.
- **SCEN: Review Patch**: `nosf_analyze.py` -> emit patch. Operator MUST copy to `config.ini`.

### REQ: Sidecar + UDS Tracking
Prefer sidecar JSON + Klipper UDS over shell markers.
- **SCEN: Sidecar Active**: sidecar + UDS -> synth `NOSF_TUNE` events. `on_m118` stable.
- **SCEN: Fallback**: UDS/sidecar fail -> use legacy marker-file/shell flow.

### REQ: Durable/Migratable State
Tuner MUST persist buckets + migrate schema without data loss.
- **SCEN: Schema 3 -> 4**: load S3 -> migrate S4. Keep estimates, locks, `_meta`.
- **SCEN: Future Refusal**: schema > current -> REFUSE (no auto-mutation).

### REQ: Chatter Resistance
LOCKED on evidence/noise pass. UNLOCK on strong mismatch.
- **SCEN: Low Evidence**: few samples/runs -> stay TRACKING/STABLE.
- **SCEN: Moderate Outlier**: single outlier -> stay LOCKED.
- **SCEN: Catastrophic/Streak/Drift**: threshold hit -> UNLOCK to TRACKING.

### REQ: Relative Noise Gate
Use `sigma/x` ratio, not absolute variance.
- **SCEN: High Flow**: `sigma/x` <= threshold -> LOCK.
- **SCEN: Low Flow**: `sigma/x` > threshold -> STABLE (reason: noise).

### REQ: State-Aware Recommendations
Weight by precision/count, not raw CSV clusters.
- **SCEN: LOCKED Exists**: use LOCKED qualifying set ONLY.
- **SCEN: Safe Mode (Zero Locked)**: safe + no locked -> REFUSE patch. exit 1.
- **SCEN: Precision Weighting**: qualifying buckets -> weight by `n/sigma²`. Trim tails.

### REQ: Comparable Run Consistency
Gate consistency uses recommendation path, filtered to mature runs.
- **SCEN: Recommendation Stable**: per-bucket medians vary, but path consistent -> PASS.
- **SCEN: Immature Run**: few rows/low confidence -> SKIP consistency. Report in patch.

### REQ: FAIL vs WARN
FAIL only on unreliable rec or pathol scatter. Stale config/immature soak = WARN.
- **SCEN: Stale Var Ref**: BP sigma p95 > `var_ref` but < ceiling -> WARN. Emit patch.
- **SCEN: Gray Mass**: mass > floor but < target -> PASS + WARN.
- **SCEN: Pathol Scatter**: BP sigma p95 > ceiling -> FAIL (hardware failure).

## Historical Design Decisions (Traceability)
- **D1 (PSF)**: Generic adapter until hardware land.
- **D2 (Advance Dwell)**: Default 6000 ms (400 ms start).
- **D3 (Versioning)**: Bump `SETTINGS_VERSION` ONLY on `settings_t` struct change.
- **D4 (Hot-swap)**: `BUF_SENSOR_TYPE` swap ONLY when IDLE.
- **D5 (Follow)**: Reload follow logic = baseline. Telemetry only.
- **D6 (Overshoot)**: `SYNC_OVERSHOOT_PCT` default OFF.
- **D7 (Status)**: CDC strings additive-at-tail. FROZEN order.
- **D2.5-A (Integral)**: 0.0 gain default. 0.6 mm clamp.
- **D2.5-B (Confidence)**: Physics-based sigma growth.

## Frozen Interfaces and Regression Constraints
- **on_m118 Ingress**: marker parsing stable. Additive only.
- **Motion Tracker**: `klipper_motion_tracker.py`, `gcode_marker.py` logic frozen.
- **Learning Loop**: KF update in tuner frozen. Rec logic stays in analyzer.
- **Data Safety**: State JSON / CSV runs must remain usable. Non-destructive migration.
- **UDS Contract**: Subscription fields frozen.
