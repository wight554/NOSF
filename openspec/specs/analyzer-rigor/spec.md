# Analyzer Rigor Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.12 analyzer rigor and the
relative tuner noise gate. The historical source note remains at
`openspec/design/sync-refactor/SYNC_REFACTOR_PHASE_2_12.md`.

## Requirements

### Requirement: Tuner lock noise shall be relative to learned rate

The tuner SHALL decide lock noise acceptability from the residual sigma divided
by the bucket's learned rate estimate after warmup.

#### Scenario: A high-rate bucket has moderate absolute residual noise

- **WHEN** the bucket has passed the warmup sample threshold
- **AND** `sigma / x` is at or below the configured ratio threshold
- **THEN** the noise gate allows the bucket to lock if all other requirements are
  satisfied

### Requirement: Analyzer safe mode shall require locked evidence

In safe mode, the analyzer SHALL refuse to emit learned values when the state
file contains no LOCKED buckets.

#### Scenario: Safe mode sees zero locked buckets

- **WHEN** `nosf_analyze.py --mode safe` runs on a state file with no LOCKED
  buckets
- **THEN** the patch begins with a refusal banner
- **AND** learned values remain at current values
- **AND** the analyzer exits non-zero

### Requirement: Aggressive and force modes shall be explicit bootstrap paths

Aggressive mode and force mode SHALL allow pre-lock estimates only when the
operator explicitly chooses that risk.

#### Scenario: Aggressive mode sees zero locked buckets

- **WHEN** `nosf_analyze.py --mode aggressive` runs without LOCKED buckets
- **THEN** the patch warns that estimates are pre-lock
- **AND** emitted learned values carry LOW confidence

### Requirement: Baseline and bias recommendations shall be precision weighted

The analyzer SHALL compute baseline rate and trailing-bias recommendations from
qualifying buckets using sample-count and residual-variance weighting with
trimmed rate outliers.

#### Scenario: Multiple qualifying buckets exist

- **WHEN** the analyzer computes baseline and bias
- **THEN** it trims the low and high rate tails before the weighted baseline
- **AND** it weights each qualifying bucket by sample count divided by bounded
  residual variance
- **AND** it does not subtract a fixed safety factor from the centroid

### Requirement: Buffer variance sigma shall come from BP telemetry

The analyzer SHALL derive buffer-position sigma from BP samples in qualifying
CSV rows rather than from stale or aliased BL fields.

#### Scenario: CSV telemetry includes BP samples

- **WHEN** at least five qualifying buckets have enough BP samples
- **THEN** the analyzer computes per-bucket BP standard deviations
- **AND** uses the p95 value clamped to the allowed millimeter range for buffer
  variance recommendations

### Requirement: Patch output shall include contributors

Analyzer patches SHALL include a contributors block for learned tunables so the
operator can inspect the evidence behind recommendations.

#### Scenario: A learned recommendation has contributors

- **WHEN** the patch includes a non-default learned value
- **THEN** `[nosf_contributors]` lists contributor count, total samples, and the
  highest-weight buckets with `n`, `x`, `sigma/x`, normalized weight, and
  marginal status when applicable
