# Acceptance Gate Semantics Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.14 acceptance-gate semantics
and follow-up analyzer behavior. The historical source note remains at
`openspec/design/sync-refactor/SYNC_REFACTOR_PHASE_2_14.md`.

## Requirements

### Requirement: Acceptance gate shall separate rejection from advisories

The analyzer SHALL fail only on conditions that make the recommendation
unreliable, while reporting stale config and incomplete soak signals as warnings.

#### Scenario: Recommendations are stable but current config is stale

- **WHEN** consistency passes and contributor evidence is adequate
- **AND** the current config differs from the learned recommendation
- **THEN** the gate may warn about stale current config
- **AND** it still emits an applicable patch when no reliability failure exists

### Requirement: Contributor mass shall use a floored denominator

Contributor-mass coverage SHALL avoid penalizing the operator for many tiny,
irrelevant, or immature buckets by applying the Phase 2.14 denominator floor
semantics.

#### Scenario: Many low-evidence buckets exist in state

- **WHEN** the state file includes many buckets with little usable evidence
- **THEN** contributor mass is computed from the mature evidence denominator
  defined by analyzer semantics
- **AND** the gate does not fail solely because immature buckets inflate the raw
  bucket universe

### Requirement: Contributor mass gray band shall warn before hard failure

Contributor mass below the ideal threshold but above the hard minimum SHALL
produce a warning instead of a rejection.

#### Scenario: Contributor mass is between pass and warn thresholds

- **WHEN** contributor mass is above the hard pass floor but below the preferred
  coverage target
- **THEN** the patch records a contributor-mass warning
- **AND** the acceptance gate can still pass if other hard criteria pass

### Requirement: Hardware sigma ceiling shall not block corrective patches

The acceptance gate SHALL treat buffer sigma that exceeds the current configured
reference as a stale-config warning when the emitted patch can correct the
reference.

#### Scenario: BP sigma p95 is higher than current reference

- **WHEN** the analyzer computes a supported higher buffer reference value
- **THEN** the patch can recommend the corrected value
- **AND** the gate records the stale reference as a warning rather than a hard
  failure

### Requirement: Soak maturity shall be reported without hiding recommendations

Run-count and duration maturity checks SHALL help the operator judge confidence
without hiding otherwise stable recommendations.

#### Scenario: Only two calibration runs are provided

- **WHEN** per-run consistency is stable but run count is below the soak target
- **THEN** the patch records a soak-immature warning
- **AND** the recommendation remains visible with its computed confidence

### Requirement: Analyzer input shall support shell-expanded CSV groups

The analyzer SHALL accept multiple CSV paths passed through `--in`, including
paths produced by shell glob expansion.

#### Scenario: Operator passes a wildcard-expanded run set

- **WHEN** the shell expands `--in ~/nosf-runs/phase212-run*.csv` into multiple
  paths
- **THEN** the analyzer reads all provided files in order
- **AND** source/run diagnostics identify the files used
