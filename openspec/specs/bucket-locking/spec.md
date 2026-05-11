# Bucket Locking Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.11 smarter bucket lock/unlock
behavior. Old planning prose is available through git history when needed.

## Requirements

### Requirement: Bucket schema 4 shall preserve residual statistics

Bucket state schema 4 SHALL include scalar residual-statistics fields used by
the lock/unlock algorithm without storing per-sample histories.

#### Scenario: A bucket receives a Kalman update

- **WHEN** the tuner updates the bucket estimate
- **THEN** residual EWMA, residual variance EWMA, outlier streak, locked sample
  count, last unlock reason, last unlock time, and locked-since run sequence are
  maintained as scalar fields

### Requirement: Schema 3 state shall migrate to schema 4 without data loss

The schema 3 to 4 migration SHALL preserve all existing bucket and `_meta`
content and keep LOCKED buckets locked.

#### Scenario: An operator loads an existing schema 3 database

- **WHEN** the Phase 2.11 or later tuner loads the database
- **THEN** schema 4 fields are added with safe defaults
- **AND** learned estimates, evidence counts, state, and metadata remain intact

### Requirement: Unlocking shall use three residual channels

LOCKED buckets SHALL unlock only through the catastrophic, streak, or drift
channels defined by residual-aware logic.

#### Scenario: A locked bucket sees a single ordinary noisy sample

- **WHEN** the residual does not satisfy catastrophic, streak, or drift criteria
- **THEN** the bucket remains LOCKED
- **AND** the sample is credited to locked dwell/evidence accounting

### Requirement: Locking shall be noise gated and dwell guarded

A bucket SHALL NOT enter or re-enter LOCKED state until required sample evidence,
noise criteria, and minimum locked dwell behavior are satisfied.

#### Scenario: A bucket has enough samples but high relative residual noise

- **WHEN** the bucket otherwise meets cumulative locking requirements
- **AND** residual noise exceeds the configured gate
- **THEN** the bucket remains STABLE
- **AND** `state-info` reports a noise-related wait reason

### Requirement: Verbose state-info shall expose lock diagnostics

Verbose tuner state output SHALL include residual and unlock diagnostics needed
to understand chatter, dwell, and lock decisions.

#### Scenario: The operator runs verbose state-info

- **WHEN** `--state-info --verbose` is invoked
- **THEN** output includes residual variance, outlier streak, dwell, and last
  unlock reason
- **AND** default state-info columns remain stable for non-verbose use
