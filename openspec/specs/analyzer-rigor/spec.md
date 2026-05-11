# Analyzer Rigor Specification

## Purpose
Analyzer requirements for weighting, safe mode, and contributors.

## Requirements

### REQ: Relative Noise Gate
Lock acceptability from `sigma / rate` after warmup.
- **SCEN: High Rate**: `sigma/x` <= threshold -> allow lock.

### REQ: Safe Mode Enforcement
Safe mode MUST refuse values if zero LOCKED buckets.
- **SCEN: Zero Locked**: `--mode safe` + zero locked -> REFUSE banner. NO learned values. exit 1.

### REQ: Explicit Bootstrap Paths
Aggressive/force modes allow pre-lock estimates.
- **SCEN: Zero Locked**: `--mode aggressive` -> WARN + LOW confidence learned values.

### REQ: Precision Weighted Recs
Recs use precision-weighted qualifying set (N / Var). Trim tails.
- **SCEN: Multiple Buckets**: trim 5/95 tails -> weight buckets by `n/sigma²`. NO fixed safety K.

### REQ: BP-Derived Sigma
Derive `buf_variance_blend_ref_mm` from BP samples, NOT BL field.
- **SCEN: Telemetry Ready**: compute per-bucket BP stdev -> take p95. Clamp MM range.

### REQ: Contributors Visibility
Patch MUST include contributor evidence block.
- **SCEN: Recommendation Emitted**: `[nosf_contributors]` lists N, samples, weights, and top buckets.

## Historical Rationale and Constants

### Stability
Precision weighting (`n/sigma²`) + 5/95 trimming replaced "dominant pick" to stop oscillation.

### Constants
- **Qualifying Set**: Safe mode uses LOCKED buckets ONLY.
- **Sigma Clamp**: `[0.1, 5.0]` mm physical range.
- **Weight Cap**: `5 * median(weights)` to prevent single-bucket dominance.
- **Safe fallback**: safe mode = re-tunes. aggressive = bootstrap.
- **Legacy**: `SAFETY_K` is deprecated stub.
