# Acceptance Gate Parity Specification

## Purpose
Gate parity + mature run consistency.

## Requirements

### REQ: Shared Recommendation Path
Gate MUST compare per-run recs from the same state-aware path as patch.
- **SCEN: Stable Rec**: raw bucket medians vary, but shared path consistent -> PASS.

### REQ: Back-Compat
`compute_recommendations` MUST retain dict shape/semantics.
- **SCEN: Test Import**: callers get same keys/tuples/labels.

### REQ: Classification
Classify runs (comparable/skipped) before delta check.
- **SCEN: Low Rows**: run < thresholds -> SKIP consistency. Report reason in patch.

### REQ: Diagnostic Visibility
Patch MUST include per-run estimates regardless of gate outcome.
- **SCEN: Coverage Fail**: gate fails -> still include estimates, skip reasons, placeholders.

### REQ: Contributor Mass Hard Gate
FAIL only on mass. WARN on raw rows.
- **SCEN: Low Raw / OK Mass**: mass >= pass floor -> PASS + WARN raw coverage.

### REQ: Placeholder Telemetry
Mark telemetry counters as pending until log parsing exists.
- **SCEN: zero Counters**: patch states "not parsed". No false "no-event" proof.

## Historical Rationale and Constants

### Stability
Shared `recommend_for_subset` path ensures gate measures real stability, not raw bucket noise.

### Thresholds
- **`MIN_COMPARABLE_BUCKETS = 3`**.
- **`MIN_RUN_BUCKET_ROWS = 50`**.
- **`CONTRIBUTOR_MASS_PASS = 0.50`** (FAIL floor).
- **`CONTRIBUTOR_MASS_WARN = 0.65`** (Target).
- **`RAW_COVERAGE_WARN = 0.80`** (WARN floor).

### Risk
- **Wrapper**: `compute_recommendations` is thin wrapper for test stability.
- **Classification**: Minimum 2 comparable runs req.
