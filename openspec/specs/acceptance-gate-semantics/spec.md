# Acceptance Gate Semantics Specification

## Purpose
Phase 2.14 gate semantics and reliability decision logic.

## Requirements

### Requirement: Separate Rejection from Advisory
FAIL ONLY on reliability issues. Stale config and incomplete soak signals are reported as WARNINGS.
- **Scenario: Stable Recommendation / Stale Config**: Consistency PASS -> WARN about stale config. Emit patch.

### Requirement: Floored Denominator
Avoid penalizing the operator for many immature buckets in mass calculation.
- **Scenario: Many Low-Evidence Buckets**: Use the mature evidence denominator floor.

### Requirement: Mass Gray Band
WARNING between PASS and FAIL thresholds.
- **Scenario: Gray Mass**: Above hard floor but below target -> PASS with mass warning.

### Requirement: Sigma Ceiling
BP sigma > current reference but < ceiling -> WARN about stale reference. Emit patch.
- **Scenario: High BP Sigma**: Above current reference but below 5.0 mm -> WARN. Recommend correction.

### Requirement: Soak Maturity
Report run-count and duration without hiding stable recommendations.
- **Scenario: 2 Consistent Runs**: Per-run consistency stable -> PASS with soak-immature warning. Recommendations remain visible.

### Requirement: Glob Input
Support shell-expanded CSV groups using the `--in` flag (e.g., `--in *.csv`).

## Standard Constants and Thresholds

- **Mass Floor (`DENOMINATOR_MIN_BUCKET_N`)**: 50 samples.
- **Hardware Ceiling (`SIGMA_HARDWARE_CEILING_MM`)**: 5.0 mm.
- **Soak Minimum (`DURATION_WARN_MIN_S`)**: 1800 s (30 minutes).
- **Minimum Comparable Runs**: 2.
- **Contributor Mass Band**:
    - **FAIL**: < 40%
    - **WARN**: 40% to 65%
    - **PASS**: > 65%
