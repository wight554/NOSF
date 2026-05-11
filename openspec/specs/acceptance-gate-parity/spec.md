# Acceptance Gate Parity Specification

## Purpose
Gate parity and mature-run consistency.

## Requirements

### Requirement: Shared Recommendation Path
Gate MUST compare per-run recommendations from the same state-aware path as the patch.
- **Scenario: Stable Recommendation**: Raw bucket medians vary, but shared path is consistent -> PASS.

### Requirement: Backward-Compatibility
`compute_recommendations` MUST retain dictionary shape and semantics.
- **Scenario: Test Import**: Existing callers receive the same keys, tuples, and labels.

### Requirement: Classification
Classify runs (comparable or skipped) before checking consistency deltas.
- **Scenario: Low Rows**: Run below thresholds -> SKIP consistency check. Report reason in patch.

### Requirement: Diagnostic Visibility
Patch MUST include per-run estimates regardless of gate outcome.
- **Scenario: Coverage Failure**: Gate fails -> still include estimates, skip reasons, and placeholders.

### Requirement: Contributor Mass Hard Gate
FAIL only on contributor mass. WARN on raw row coverage.
- **Scenario: Low Raw Row Coverage**: Mass >= pass floor -> PASS with raw-coverage warning.

### Requirement: Placeholder Telemetry
Mark telemetry counters as pending until real log parsing exists.
- **Scenario: Zero Counters**: Patch explicitly states "not currently parsed". No false "no-event" proof.

## Historical Rationale and Constants

### Stability
The shared `recommend_for_subset` path ensures the gate measures real recommendation stability, not raw per-bucket noise.

### Thresholds and Constants
- **`MIN_COMPARABLE_BUCKETS = 3`**: Minimum qualifying buckets per run.
- **`MIN_RUN_BUCKET_ROWS = 50`**: Minimum rows per bucket in a run.
- **`CONTRIBUTOR_MASS_PASS = 0.50`**: Hard failure floor.
- **`CONTRIBUTOR_MASS_WARN = 0.65`**: Preferred coverage target.
- **`RAW_COVERAGE_WARN = 0.80`**: Warning floor for raw MID row coverage.

### Risks and Constraints
- **Wrapper**: `compute_recommendations` is a thin wrapper to maintain test stability.
- **Classification**: A minimum of 2 comparable runs is required for consistency reduction.
