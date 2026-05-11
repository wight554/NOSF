# Analyzer Rigor Specification

## Purpose
Analyzer requirements for weighting, safe mode, and contributors.

## Requirements

### Requirement: Relative Noise Gate
Lock acceptability derived from `sigma / rate` after warmup.
- **Scenario: High Rate**: `sigma/x` <= threshold -> allow bucket to lock.

### Requirement: Safe Mode Enforcement
Safe mode MUST refuse recommendations if zero buckets are LOCKED.
- **Scenario: Zero Locked**: `--mode safe` with zero locked buckets -> emit REFUSE banner. NO learned values. Exit 1.

### Requirement: Explicit Bootstrap Paths
Aggressive and force modes allow pre-lock estimates with warnings.
- **Scenario: Zero Locked**: `--mode aggressive` -> emit WARN + recommendations with LOW confidence labels.

### Requirement: Precision Weighted Recommendations
Recommendations use precision-weighted qualifying set (N / Var). Trim tails.
- **Scenario: Multiple Buckets**: Trim 5/95 tails -> weight buckets by `n/sigma²`. NO fixed safety factor (K).

### Requirement: BP-Derived Sigma
Derive `buf_variance_blend_ref_mm` from Buffer Position samples, NOT Baseline field.
- **Scenario: Telemetry Ready**: Compute per-bucket BP standard deviations -> take p95. Clamp to physical MM range.

### Requirement: Contributors Visibility
Patch MUST include contributor evidence block.
- **Scenario: Recommendation Emitted**: `[nosf_contributors]` lists contributor count, samples, weights, and top buckets.

## Historical Rationale and Constants

### Stability
Precision weighting (`n/sigma²`) and 5/95 tail trimming replaced the "dominant pick" method to prevent oscillation across runs.

### Constants and Constraints
- **Qualifying Set**: Safe mode uses LOCKED buckets ONLY.
- **Sigma Clamp**: `[0.1, 5.0]` mm physical range.
- **Weight Cap**: Capped at `5 * median(weights)` to prevent single-bucket dominance.
- **Safe Fallback**: Safe mode is for re-tunes. Aggressive mode is the bootstrap path.
- **Legacy**: `SAFETY_K` is kept as a deprecated stub for compatibility.
