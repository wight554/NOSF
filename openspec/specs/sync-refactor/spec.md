# Sync Refactor Specification

## Purpose
Durable contract for NOSF sync, tuning, tracking, and analyzer behavioral requirements and historical rationale.

## Requirements

### Requirement: Standalone Sync
NOSF SHALL run sync, toolchange, and RELOAD without host after calibration flash.

#### Scenario: Host Detached
- **WHEN** calibration reviewed + flashed
- **THEN** NOSF runs from firmware/runtime ONLY
- **AND** host tuner not required during print

#### Scenario: Live Debug
- **WHEN** `nosf_live_tuner.py` runs without writes
- **THEN** tuner observes and persists state
- **AND** NO `SET`/`SV` commands sent to firmware

### Requirement: Observe-Only Calibration
The system SHALL collect markers, buckets, and patches without mutation unless explicit opt-in.

#### Scenario: Default Tuner
- **WHEN** markers arrive at tuner
- **THEN** state and CSV update
- **AND** NO firmware writes occur

#### Scenario: Review Patch
- **WHEN** `nosf_analyze.py` emits a patch
- **THEN** patch is for review only
- **AND** operator MUST copy accepted values to `config.ini`

### Requirement: Sidecar + UDS Tracking
The system SHALL prefer sidecar JSON + Klipper UDS over shell markers when available.

#### Scenario: Sidecar Active
- **WHEN** sidecar + UDS provided
- **THEN** tuner synthesizes `NOSF_TUNE` events
- **AND** `on_m118` contract remains stable

#### Scenario: Fallback
- **WHEN** UDS or sidecar fail
- **THEN** tuner falls back to legacy marker-file or shell flow
- **AND** bucket learning semantics remain consistent

### Requirement: Durable/Migratable State
The tuner MUST persist buckets and migrate schema without data loss across versions.

#### Scenario: Schema 3 -> 4
- **WHEN** tuner loads schema 3 database
- **THEN** state migrates to schema 4
- **AND** estimates, locks, and `_meta` remain intact

#### Scenario: Future Refusal
- **WHEN** state file has schema version newer than tuner
- **THEN** tuner refuses to load
- **AND** NO auto-mutation occurs

### Requirement: Chatter Resistance
Buckets SHALL become LOCKED on evidence/noise pass and UNLOCK only on strong mismatch.

#### Scenario: Low Evidence
- **WHEN** bucket has insufficient samples or runs
- **THEN** bucket stays TRACKING or STABLE

#### Scenario: Moderate Outlier
- **WHEN** LOCKED bucket sees single moderate residual outlier
- **THEN** bucket remains LOCKED
- **AND** sample credited to locked dwell

#### Scenario: Catastrophic/Streak/Drift
- **WHEN** catastrophic, streak, or drift threshold hit
- **THEN** bucket unlocks to TRACKING

### Requirement: Relative Noise Gate
The tuner SHALL use `sigma/x` ratio, not absolute variance, for lock decisions.

#### Scenario: High Flow
- **WHEN** bucket `sigma/x` <= threshold
- **THEN** noise gate allows lock

#### Scenario: Low Flow
- **WHEN** bucket `sigma/x` > threshold after warmup
- **THEN** bucket remains STABLE
- **AND** `state-info` reports noise wait reason

### Requirement: State-Aware Recommendations
The analyzer SHALL weight by precision/count, not raw CSV clusters, during recommendation.

#### Scenario: LOCKED Exists
- **WHEN** LOCKED buckets exist in state
- **THEN** analyzer uses ONLY LOCKED set for recommendations

#### Scenario: Safe Mode (Zero Locked)
- **WHEN** `--mode safe` runs with zero locked buckets
- **THEN** analyzer refuses to emit learned values
- **AND** process exits non-zero

#### Scenario: Precision Weighting
- **WHEN** multiple qualifying buckets exist
- **THEN** analyzer weights by `n/sigma²`
- **AND** 5/95 tails are trimmed

### Requirement: Comparable Run Consistency
The gate SHALL use recommendation path consistency, filtered to mature runs.

#### Scenario: Recommendation Stable
- **WHEN** raw medians vary across runs but recommendation path stable
- **THEN** acceptance gate passes consistency check

#### Scenario: Immature Run
- **WHEN** run has few rows or low confidence
- **THEN** run is skipped from consistency reduction
- **AND** reason reported in patch diagnostics

### Requirement: FAIL vs WARN Separation
The gate SHALL FAIL only on unreliable recommendations or pathological scatter.

#### Scenario: Stale Variance Reference
- **WHEN** BP sigma p95 > current reference but < ceiling
- **THEN** gate warns about stale reference
- **AND** emits corrective recommendation

#### Scenario: Gray Mass
- **WHEN** mass above floor but below target
- **THEN** gate passes with mass warning

#### Scenario: Pathological Scatter
- **WHEN** BP sigma p95 > ceiling
- **THEN** gate fails and reports hardware failure

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
