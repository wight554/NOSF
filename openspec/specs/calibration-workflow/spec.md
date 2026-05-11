# Calibration Workflow Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.9 observe-only calibration.
The detailed historical plan remains at
`openspec/design/sync-refactor/SYNC_REFACTOR_PHASE_2_9.md`.

## Requirements

### Requirement: Calibration shall be observe-only by default

The calibration workflow SHALL collect evidence without mutating firmware
settings unless the operator passes explicit write flags.

#### Scenario: Tuner starts with default flags

- **WHEN** the operator starts `scripts/nosf_live_tuner.py` for calibration
- **THEN** the tuner records state and optional CSV telemetry
- **AND** it does not send firmware setting writes
- **AND** it does not save firmware settings at print finish

### Requirement: State schema migrations shall be chained

Bucket state migrations SHALL be registered in a migration table and applied in
sequence without rewriting the migration loop for each new schema.

#### Scenario: A schema 1 state file is loaded by a newer tuner

- **WHEN** `migrate_state_data` receives an old state file
- **THEN** every registered migration is applied in order until the current
  schema is reached
- **AND** existing bucket data and `_meta` data are preserved

### Requirement: Bucket locking shall require cumulative evidence

A bucket SHALL lock only after satisfying cumulative evidence requirements for
samples, runs, layers, stability, and motion time.

#### Scenario: A bucket has many samples from one short run

- **WHEN** the bucket has stable estimates but insufficient run or motion-time
  evidence
- **THEN** the bucket remains STABLE
- **AND** `state-info` reports the unmet requirement

### Requirement: Analyzer patches shall be review-only outputs

`scripts/nosf_analyze.py` SHALL emit review patches that preserve current values
for unavailable recommendations and label recommendation confidence.

#### Scenario: Evidence is insufficient for a tunable

- **WHEN** the analyzer cannot make a supported recommendation
- **THEN** the emitted patch shows the current value as the suggested value
- **AND** the confidence is DEFAULT or an explicit non-apply status

### Requirement: Long-running daemon calibration shall survive stale data

Daemon-mode calibration SHALL tolerate stale buckets and repeated runs without
allowing stale evidence to dominate current recommendations.

#### Scenario: Old buckets exist in state

- **WHEN** the analyzer or tuner reports current calibration status
- **THEN** stale data is either excluded or clearly identified according to the
  current workflow rules
- **AND** fresh evidence can continue accumulating in the same state file
