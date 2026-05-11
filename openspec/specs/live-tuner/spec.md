# Live Tuner Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.8 live tuning. The historical
source note remains at
`openspec/design/sync-refactor/SYNC_REFACTOR_PHASE_2_8.md`; this spec is the
readable behavioral contract for the live tuner area.

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
