# Live Tuner Specification

## Purpose
Tuner contract for Phase 2.8 and Phase 2.11 (chatter resistance).

## Requirements

### Requirement: Per-Feature Velocity Buckets
Tuner aggregates telemetry into feature + velocity buckets (rate + bias).
- **Scenario: Marker Active**: Tune samples arrive while marker is active -> update rounded velocity bucket.
- **AND** update rate, uncertainty, bias, sample count, layers, runs, and motion time.

### Requirement: Machine-Scoped Persistence
Persist bucket state in machine-scoped JSON.
- **Scenario: Tuner Restart**: Load machine state -> extend evidence (no starting from zero).

### Requirement: Observe-Only Default
NO firmware writes without explicit permission flags.
- **Scenario: No Write Flags**: Record and report buckets. NO `SET` writes, NO `SV` (save).

### Requirement: Review-Only Workflow
Prefer analyzer review patches over blind tuning.
- **Scenario: Calibration Data Ready**: Analyzer emits patch for review -> operator reviews and flashes.

### Requirement: Diagnostics
Tuner MUST explain bucket states (TRACKING, STABLE, LOCKED).
- **Scenario: state-info**: Output bucket state, evidence counts, and wait reason (e.g. noise).

## Historical Rationale and Constants

### Live-Tune Lock
`LIVE_TUNE_LOCK:1` prevents host-firmware races. LOCKED = accepts host writes; UNLOCKED = reverts to defaults.

### Chatter Resistance (Phase 2.11)
- **Noise Gate (`sigma/x`)**: Relative residual noise threshold required to lock (default 0.25).
- **3-Channel Unlock**:
    - **Catastrophic**: Residual exceeds 10.0 * sigma.
    - **Streak**: 5 consecutive residuals exceed 3.0 * sigma.
    - **Drift**: EWMA of residuals drifts more than 4.0 * sigma.
- **Lock Dwell**: Minimum 100 samples required after warmup.

### Rollback
- **`--reset-runtime`**: Sends `LOCK:0` + `LOAD`. Clears memory and re-applies defaults.
- **Schema 4 Migration**: One-way. Adds scalar residual stats to support 3-channel unlock.
