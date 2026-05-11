# Sync Refactor Specification

## Purpose
Durable contract for NOSF sync, tuning, tracking, and analyzer.

## Requirements

### Requirement: Standalone Sync
NOSF runs sync, toolchange, and RELOAD without host after calibration flash.
- **Scenario: Host Detached**: Calibration reviewed + flashed -> run from firmware/runtime ONLY. NO host tuner required.
- **Scenario: Live Debug**: `nosf_live_tuner.py` without writes -> observe only. NO `SET`/`SV` commands.

### Requirement: Observe-Only Calibration
Collect markers, buckets, and patches without mutation unless explicit opt-in.
- **Scenario: Default Tuner**: Markers arrive -> update state/CSV. NO firmware writes.
- **Scenario: Review Patch**: `nosf_analyze.py` -> emit patch. Operator MUST copy to `config.ini`.

### Requirement: Sidecar + UDS Tracking
Prefer sidecar JSON + Klipper UDS over shell markers.
- **Scenario: Sidecar Active**: Sidecar + UDS -> synthesize `NOSF_TUNE` events. `on_m118` contract stable.
- **Scenario: Fallback**: UDS/sidecar fail -> use legacy marker-file or shell flow.

### Requirement: Durable/Migratable State
Tuner MUST persist buckets and migrate schema without data loss.
- **Scenario: Schema 3 -> 4**: Load S3 -> migrate S4. Keep estimates, locks, and `_meta`.
- **Scenario: Future Refusal**: Schema > current -> refuse (no auto-mutation).

### Requirement: Chatter Resistance
LOCKED on evidence/noise pass. UNLOCK on strong mismatch.
- **Scenario: Low Evidence**: Insufficient samples/runs -> stay TRACKING/STABLE.
- **Scenario: Moderate Outlier**: Single outlier -> stay LOCKED.
- **Scenario: Catastrophic/Streak/Drift**: Threshold hit -> UNLOCK to TRACKING.

### Requirement: Relative Noise Gate
Use `sigma/x` ratio, not absolute variance.
- **Scenario: High Flow**: `sigma/x` <= threshold -> LOCK.
- **Scenario: Low Flow**: `sigma/x` > threshold -> STABLE (reason: noise).

### Requirement: State-Aware Recommendations
Weight by precision/count, not raw CSV clusters.
- **Scenario: LOCKED Exists**: Use LOCKED qualifying set ONLY.
- **Scenario: Safe Mode (Zero Locked)**: Safe mode + no locked buckets -> refuse patch. Exit 1.
- **Scenario: Precision Weighting**: Qualifying buckets -> weight by `n/sigma²`. Trim tails.

### Requirement: Comparable Run Consistency
Gate consistency uses recommendation path, filtered to mature runs.
- **Scenario: Recommendation Stable**: Per-bucket medians vary, but path consistent -> PASS.
- **Scenario: Immature Run**: Few rows/low confidence -> SKIP consistency. Report in patch.

### Requirement: FAIL vs WARN
FAIL only on unreliable recommendations or pathological scatter. Stale config/immature soak = WARN.
- **Scenario: Stale Variance Reference**: BP sigma p95 > current reference but < ceiling -> WARN. Emit patch.
- **Scenario: Gray Mass**: Mass > floor but < target -> PASS with mass warning.
- **Scenario: Pathological Scatter**: BP sigma p95 > ceiling -> FAIL (hardware failure).

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
