# Acceptance Gate Semantics Specification

## Purpose
Phase 2.14 gate semantics and decision logic.

## Requirements

### REQ: Separate Rejection from Advisory
FAIL ONLY on reliability issues. Stale config / incomplete soak = WARN.
- **SCEN: Stable Rec / Stale Config**: consistency PASS -> WARN stale. Emit patch.

### REQ: Floored Denominator
Avoid penalty for immature buckets in mass calc.
- **SCEN: Many Low-Evidence Buckets**: use mature evidence denominator floor.

### REQ: Mass Gray Band
WARN between PASS and FAIL thresholds.
- **SCEN: Gray Mass**: above hard floor / below target -> PASS + WARN.

### REQ: Sigma Ceiling
BP sigma > ref but < ceiling -> WARN stale ref. Emit patch.
- **SCEN: High BP Sigma**: above current but below 5.0mm -> WARN. Recommend correction.

### REQ: Soak Maturity
Report run-count/duration without hiding recs.
- **SCEN: 2 Runs**: consistent -> PASS + soak-immature WARN. Rec visible.

### REQ: Glob Input
Support shell-expanded CSV groups (`--in *.csv`).

## Standard Constants and Thresholds

- **Mass Floor (`DENOMINATOR_MIN_BUCKET_N`)**: 50 samples.
- **Hardware Ceiling (`SIGMA_HARDWARE_CEILING_MM`)**: 5.0 mm.
- **Soak Min (`DURATION_WARN_MIN_S`)**: 1800 s (30 min).
- **Min Comparable Runs**: 2.
- **Mass Band**: FAIL < 40%; WARN 40-65%; PASS > 65%.
