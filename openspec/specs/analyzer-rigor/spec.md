# Analyzer Rigor Specification

## Purpose
Analyzer requirements for weighting, safe mode, and contributors behavioral contracts.

## Requirements

### Requirement: Relative Noise Gate
Bucket lock acceptability SHALL be derived from `sigma / rate` after warmup.

#### Scenario: High Rate
- **WHEN** bucket has passed warmup samples
- **AND** `sigma/x` <= threshold
- **THEN** noise gate allows bucket to lock

### Requirement: Safe Mode Enforcement
Safe mode MUST refuse recommendations if zero buckets are LOCKED in the state.

#### Scenario: Zero Locked
- **WHEN** `--mode safe` runs with zero locked buckets
- **THEN** analyzer emits REFUSE banner
- **AND** learned values remain at current defaults
- **AND** process exits non-zero

### Requirement: Explicit Bootstrap Paths
Aggressive and force modes SHALL allow pre-lock estimates with explicit warnings.

#### Scenario: Zero Locked
- **WHEN** `--mode aggressive` runs without locked buckets
- **THEN** analyzer emits recommendations with LOW confidence
- **AND** patch warns about pre-lock status

### Requirement: Precision Weighted Recommendations
Recommendations SHALL use precision-weighted qualifying set (N / Var) with trimmed tails.

#### Scenario: Multiple Buckets
- **WHEN** analyzer computes baseline or bias
- **THEN** 5/95 tails are trimmed
- **AND** buckets are weighted by `n/sigma²`
- **AND** NO fixed safety factor (K) is applied

### Requirement: BP-Derived Sigma
The analyzer SHALL derive `buf_variance_blend_ref_mm` from BP samples, NOT BL field.

#### Scenario: Telemetry Ready
- **WHEN** qualifying buckets have sufficient BP samples
- **THEN** analyzer computes per-bucket BP standard deviations
- **AND** p95 value is taken as recommendation
- **AND** value is clamped to physical millimeter range

### Requirement: Contributors Visibility
The generated patch MUST include a contributor evidence block for learned values.

#### Scenario: Recommendation Emitted
- **WHEN** learned recommendation is included in patch
- **THEN** `[nosf_contributors]` lists contributor count, samples, and weights
- **AND** top buckets are identified with marginal status where applicable

## Historical Rationale and Constants

### Stability
Precision weighting (`n/sigma²`) and 5/95 tail trimming replaced the "dominant pick" method to prevent recommendation oscillation across runs.

### Constants and Constraints
- **Qualifying Set**: Safe mode uses LOCKED buckets ONLY.
- **Sigma Footer**: `[0.1, 5.0]` mm physical range.
- **Weight Cap**: Capped at `5 * median(weights)` to prevent single-bucket dominance.
- **Safe Fallback**: Safe mode is for re-tunes. Aggressive mode is the bootstrap path.
- **Legacy**: `SAFETY_K` is kept as a deprecated stub for compatibility.
