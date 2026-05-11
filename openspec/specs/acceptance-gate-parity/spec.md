# Acceptance Gate Parity Specification

## Purpose
Gate parity and mature-run consistency behavioral contracts and requirements.

## Requirements

### Requirement: Shared Recommendation Path
The gate SHALL compare per-run recommendations from the same state-aware path as the patch.

#### Scenario: Stable Recommendation
- **WHEN** raw bucket medians vary across runs
- **AND** shared recommendation path remains consistent
- **THEN** acceptance gate passes consistency check

### Requirement: Backward-Compatibility
`compute_recommendations` SHALL retain dictionary shape and semantics for existing callers.

#### Scenario: Test Import
- **WHEN** existing tests import and call `compute_recommendations`
- **THEN** returned keys, tuples, and labels remain compatible with Phase 2.12

### Requirement: Run Classification
The system SHALL classify runs (comparable or skipped) before checking consistency deltas.

#### Scenario: Low Rows
- **WHEN** run has fewer rows than threshold
- **THEN** run is skipped from consistency reduction
- **AND** skip reason is reported in patch diagnostics

### Requirement: Diagnostic Visibility
The generated patch MUST include per-run estimates regardless of gate outcome.

#### Scenario: Coverage Failure
- **WHEN** gate rejects patch due to insufficient coverage
- **THEN** patch still includes per-run estimates and skip reasons

### Requirement: Contributor Mass Hard Gate
The acceptance gate SHALL FAIL only on contributor mass, and WARN on raw row coverage.

#### Scenario: Low Raw Row Coverage
- **WHEN** contributor mass >= pass floor
- **AND** raw row coverage < warning threshold
- **THEN** gate records warning but does not FAIL

### Requirement: Placeholder Telemetry
The analyzer MUST mark telemetry counters as pending until real log parsing exists.

#### Scenario: Zero Counters
- **WHEN** acceptance diagnostics include telemetry counters
- **THEN** patch states that telemetry is not currently parsed
- **AND** zero counters are NOT presented as proof of no events

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
