# Live Tuner Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.8 live tuning and Phase 2.11 chatter resistance. This spec is the readable behavioral contract for the live tuner area; old planning prose is available through git history when needed.

## Requirements

### Requirement: Tuner shall learn per-feature velocity buckets

The live tuner SHALL aggregate telemetry into feature-and-filament-velocity
buckets that estimate stable sync rate and trailing bias.

#### Scenario: Telemetry arrives with marker context

- **WHEN** tune samples arrive while a feature marker is active
- **THEN** samples are credited to the matching rounded velocity bucket
- **AND** the bucket updates its rate estimate, uncertainty, bias, sample count,
  layer count, run count, and cumulative motion time

### Requirement: Bucket state shall be persisted by machine

Learned bucket state SHALL be stored in a machine-scoped JSON state file so
calibration evidence can accumulate across runs.

#### Scenario: The operator restarts the tuner

- **WHEN** a previous state file exists for the selected machine id
- **THEN** the tuner loads existing bucket evidence
- **AND** new samples extend that evidence instead of starting from zero

### Requirement: Live writes shall require explicit opt-in

The tuner SHALL NOT send firmware-setting writes during normal observe-only use.
Any live write behavior SHALL be guarded by explicit command-line flags.

#### Scenario: Tuner runs without write flags

- **WHEN** the operator starts the tuner with no baseline-write or bias-write
  permission
- **THEN** the tuner records and reports bucket evidence
- **AND** it sends no firmware `SET` writes and no save command

### Requirement: Review patches shall be preferred over blind live tuning

The tuning workflow SHALL prefer analyzer-generated review patches over applying
live learned values directly.

#### Scenario: Calibration data is available

- **WHEN** the operator has state and CSV telemetry from calibration prints
- **THEN** the analyzer emits a review patch for human inspection
- **AND** operator-facing docs direct the operator to review and flash explicit
  settings rather than blindly trusting live updates

### Requirement: Tuner diagnostics shall explain non-locking buckets

The tuner SHALL provide human-readable state and wait reasons that explain why a
bucket is TRACKING, STABLE, or LOCKED.

#### Scenario: The operator runs state-info

- **WHEN** `nosf_live_tuner.py --state-info` is invoked
- **THEN** the output lists bucket state, evidence counts, and a wait reason
- **AND** the reason is specific enough to guide the next calibration step

## Historical Design Rationale and Constants

### Live-Tune Lock Rationale
The `LIVE_TUNE_LOCK` protocol (SET:LIVE_TUNE_LOCK:1) was introduced to prevent race conditions between the host tuner and the firmware's internal state. When locked, the firmware accepts live tuning writes; when unlocked, it reverts to persisted defaults.

### Chatter Resistance (Phase 2.11)
To prevent "chatter" (rapid locking/unlocking due to noise), the following thresholds were established:
- **Noise Ratio Gate (`sigma/x`)**: Buckets must have relative residual noise below the configured threshold (default 0.25) to lock.
- **Three-Channel Unlock**:
    - **Catastrophic**: Single residual exceeds 10.0 * sigma.
    - **Streak**: 5 consecutive residuals exceed 3.0 * sigma.
    - **Drift**: EWMA of residuals drifts by more than 4.0 * sigma.
- **Lock Dwell**: A bucket must remain stable for at least 100 samples after warmup before locking.

### Rollback Mechanics
- **`--reset-runtime`**: Sends `SET:LIVE_TUNE_LOCK:0` and `LOAD` to the firmware, clearing in-memory tuner state and re-applying persisted defaults.
- **Schema 4 Migration**: The migration from schema 3 to 4 is one-way. It adds scalar residual statistics to buckets to support the three-channel unlock logic without requiring full sample history.
