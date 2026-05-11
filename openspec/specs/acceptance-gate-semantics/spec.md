# Acceptance Gate Semantics Specification

## Purpose
Phase 2.14 gate semantics and reliability decision logic behavioral requirements.

## Requirements

### Requirement: Separate Rejection from Advisory
The gate SHALL FAIL ONLY on reliability issues; stale config and incomplete soak are warnings.

#### Scenario: Stable Recommendation / Stale Config
- **WHEN** consistency check passes
- **AND** current config is stale
- **THEN** gate warns but does not FAIL

### Requirement: Floored Denominator
The system SHALL avoid penalizing the operator for many immature buckets in mass calculation.

#### Scenario: Many Low-Evidence Buckets
- **WHEN** mass calculation is performed
- **THEN** mature evidence denominator floor is applied

### Requirement: Mass Gray Band
The gate SHALL issue a WARNING when mass is between PASS and FAIL thresholds.

#### Scenario: Gray Mass
- **WHEN** mass is above failure floor but below target
- **THEN** gate passes with mass warning

### Requirement: Sigma Ceiling
The analyzer SHALL warn and recommend correction when BP sigma is between reference and 5.0 mm.

#### Scenario: High BP Sigma
- **WHEN** BP sigma is between reference and 5.0 mm
- **THEN** gate warns and recommends correction

### Requirement: Soak Maturity
The system MUST report run-count and duration without hiding stable recommendations.

#### Scenario: 2 Consistent Runs
- **WHEN** per-run consistency is stable
- **THEN** gate passes with soak-immature warning
- **AND** recommendations remain visible

### Requirement: Glob Input
The analyzer SHALL support shell-expanded CSV groups using the `--in` flag.

#### Scenario: Multiple CSVs via Wildcard
- **WHEN** `--in *.csv` is used
- **THEN** analyzer processes all matching files

## Standard Constants and Thresholds

- **Mass Floor (`DENOMINATOR_MIN_BUCKET_N`)**: 50 samples.
- **Hardware Ceiling (`SIGMA_HARDWARE_CEILING_MM`)**: 5.0 mm.
- **Soak Minimum (`DURATION_WARN_MIN_S`)**: 1800 s (30 minutes).
- **Minimum Comparable Runs**: 2.
- **Contributor Mass Band**:
    - **FAIL**: < 40%
    - **WARN**: 40% to 65%
    - **PASS**: > 65%
