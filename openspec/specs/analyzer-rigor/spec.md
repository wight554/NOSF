# Analyzer Rigor Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.12 analyzer rigor. This spec defines the requirements for precision-weighted recommendations, safe mode enforcement, and contributor visibility.

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

## Historical Design Rationale and Constants

### Precision Weighting vs. Dominant Pick
Historically, the analyzer picked the single "dominant" bucket for recommendations, which caused oscillation between runs. Phase 2.12 introduced precision weighting (Weight = N / Var) and 5/95 tail trimming across qualifying buckets to stabilize the centroid.

### BP-derived Sigma Rationale
Deriving `buf_variance_blend_ref_mm` from the `BP` (Buffer Position) standard deviation in CSV telemetry ensures the reference reflects the real physical hardware scatter, rather than an aliased estimation from the `BL` (Baseline) field.

### Constants and Constraints
- **Qualifying Set**: Recommendations in `--mode safe` use ONLY LOCKED buckets.
- **Sigma Clamp**: `buf_variance_blend_ref_mm` is clamped to `[0.1, 5.0]` mm to prevent non-physical recommendations.
- **Weight Cap**: To prevent a single ultra-stable bucket from dominating, weights are capped at `5 * median(weights)` of the qualifying set.
- **Bootstrap Path**: Safe mode is for re-tunes; operators are directed to use `--mode aggressive` for the very first calibration print where zero buckets are locked.
- **Legacy Stubs**: `SAFETY_K` is kept as a deprecated stub for compatibility during the transition from fixed-safety-factor to precision-weighted logic.
