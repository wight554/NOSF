# Acceptance Gate Parity Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.13 acceptance-gate parity and
mature-run consistency. Old planning prose is available through git history when
needed.

## Requirements

### Requirement: Gate consistency shall reuse recommendation semantics

The acceptance gate SHALL compare per-run recommendations computed by the same
state-aware recommendation path used to emit the final patch.

#### Scenario: Per-bucket row medians vary but recommendation is stable

- **WHEN** raw per-bucket per-run medians swing across runs
- **AND** state-aware recommendations for the same runs remain stable
- **THEN** the acceptance gate passes the consistency check
- **AND** it reports the comparable per-run recommendation estimates

### Requirement: compute_recommendations shall remain backward-compatible

Existing callers of `compute_recommendations` SHALL receive the same dict shape
and recommendation semantics after the shared recommendation refactor.

#### Scenario: Existing analyzer tests import compute_recommendations

- **WHEN** tests call `compute_recommendations` with existing inputs
- **THEN** returned keys, tuple shape, values, and confidence labels remain
  compatible with Phase 2.12 expectations

### Requirement: Runs shall be classified before consistency reduction

The acceptance gate SHALL classify each input run as comparable or skipped
before computing consistency deltas.

#### Scenario: A run lacks enough qualifying rows

- **WHEN** a run has fewer than the comparable-run thresholds
- **THEN** the run is skipped from consistency reduction
- **AND** the patch diagnostics include the skip reason

### Requirement: Patch diagnostics shall always include per-run estimates

Acceptance-gate patch output SHALL include the per-run diagnostics block whether
the gate passes, fails, or skips consistency.

#### Scenario: Acceptance gate fails for coverage

- **WHEN** the gate rejects a patch because coverage is insufficient
- **THEN** the emitted patch still includes per-run estimates, comparable-run
  counts, skipped-run reasons, telemetry placeholder notes, warnings, and failure
  reasons

### Requirement: Coverage gate shall use contributor mass as the hard check

The acceptance gate SHALL distinguish contributor mass from raw row coverage and
use contributor mass as the hard coverage criterion.

#### Scenario: Raw MID coverage is below target but contributor mass passes

- **WHEN** contributor mass is at or above the pass threshold
- **AND** raw MID row coverage is below the warning threshold
- **THEN** the gate records a raw-coverage warning
- **AND** it does not fail solely because of raw MID row coverage

### Requirement: Telemetry counters shall be marked as placeholders

Until log parsing exists, analyzer telemetry counters SHALL be reported as
pending placeholder values rather than clean operational signals.

#### Scenario: The patch reports ADV or fallback telemetry counters

- **WHEN** acceptance diagnostics include telemetry counters
- **THEN** the patch states that telemetry is not currently parsed from logs
- **AND** zero counters are not presented as proof that no events occurred

## Historical Design Rationale and Constants

### Consistent Recommendation Path Rationale
Prior to Phase 2.13, the acceptance gate compared raw per-bucket per-run medians, which were brittle to noise. By refactoring the logic to use a shared `recommend_for_subset` path, the gate now compares the same high-level recommendations that will actually be emitted in the patch, providing a true measure of stability.

### Run Classification Thresholds
- **`MIN_COMPARABLE_BUCKETS = 3`**: A run must have at least 3 qualifying buckets to be considered comparable for consistency reduction.
- **`MIN_RUN_BUCKET_ROWS = 50`**: A bucket within a run must have at least 50 rows to contribute to that run's recommendation.

### Coverage Thresholds (Phase 2.13 Baseline)
- **`CONTRIBUTOR_MASS_PASS = 0.50`**: Minimum mass floor for a hard pass.
- **`CONTRIBUTOR_MASS_WARN = 0.65`**: Preferred mass coverage target.
- **`RAW_COVERAGE_WARN = 0.80`**: Warning threshold for raw MID row coverage.

### Risk Mitigation
- **Conservative Classification**: If `MIN_COMPARABLE_BUCKETS = 3` is too aggressive for an operator's data, it can be lowered to 2, but not below.
- **Wrapper Pattern**: `compute_recommendations` was preserved as a thin wrapper around `recommend_for_subset` to ensure that existing Phase 2.12 and 2.11 regression tests remain valid without modification.
- **Telemetry Placeholder**: Reporting zero telemetry without a disclaimer was identified as a risk for false confidence; the placeholder comment block is a mandatory requirement.
