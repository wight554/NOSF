# Sync Refactor Specification

## Purpose

Define the current behavioral contract for NOSF sync calibration, live-tuner bucket learning, Klipper marker tracking, analyzer recommendations, and acceptance-gate semantics. This spec is the normalized current contract agents should read first; migrated historical phase prose is available through git history when needed.

## Requirements

### Requirement: Firmware sync behavior remains standalone after calibration
NOSF SHALL run normal sync, toolchange, and RELOAD behavior without a host-side tuning process attached after reviewed calibration defaults are flashed.

#### Scenario: Host is detached after calibration
- **WHEN** calibration has produced reviewed values in `config.ini` and firmware has been regenerated/flashed
- **THEN** NOSF SHALL operate from firmware/runtime settings only
- **AND** live tuner host processes SHALL NOT be required during normal printing

#### Scenario: Live writes are used only by explicit debug mode
- **WHEN** `scripts/nosf_live_tuner.py` is started without live-write flags
- **THEN** it SHALL observe and persist bucket state without sending `SET:` writes or `SV:`

### Requirement: Calibration is observe-only by default
The host calibration workflow SHALL collect marker-correlated MID-zone samples, learn bucket state, and emit review-only patches without mutating firmware defaults unless an explicit flash/commit path is requested.

#### Scenario: Default tuner invocation observes only
- **WHEN** the tuner receives status samples and feature markers with no `--allow-*writes` flags
- **THEN** it SHALL update state and CSV output only
- **AND** it SHALL NOT send baseline, bias, or save commands to firmware

#### Scenario: Analyzer patch is review-only
- **WHEN** `scripts/nosf_analyze.py` emits a patch
- **THEN** the patch SHALL be commented review text under `[nosf_review]`
- **AND** the operator SHALL manually copy accepted values into `config.ini`

### Requirement: Klipper marker tracking uses sidecar and UDS motion events
Calibration marker synthesis SHALL prefer slicer sidecar JSON plus Klipper UDS `objects/subscribe` motion tracking instead of per-feature shell commands in G-code.

#### Scenario: Sidecar path is available
- **WHEN** a sidecar file and Klipper UDS are provided
- **THEN** the tuner SHALL synthesize the same `NT:START`, `NT:LAYER`, `NOSF_TUNE`, and `NOSF_TUNE:FINISH` events expected by `on_m118`
- **AND** the `on_m118` ingress contract SHALL remain stable

#### Scenario: Shell marker fallback is needed
- **WHEN** sidecar or UDS tracking is unavailable and fallback mode is enabled
- **THEN** legacy marker-file or shell-marker flows MAY be used without changing the bucket-learning semantics

### Requirement: Bucket state is durable and migratable
The live tuner SHALL persist bucket learning data in a schema-versioned state file and migrate older supported schemas through a chained migration registry.

#### Scenario: Production schema 3 state is loaded
- **WHEN** schema 3 state is loaded by the current tuner
- **THEN** it SHALL migrate to schema 4 without losing bucket estimates, lock state, or `_meta` fields

#### Scenario: Future schema is loaded
- **WHEN** a state file has a schema version newer than the current tuner supports
- **THEN** the tuner SHALL refuse it rather than silently mutating data

### Requirement: Bucket lock and unlock decisions resist chatter
A bucket SHALL only become LOCKED when cumulative evidence and noise gates pass, and a LOCKED bucket SHALL unlock only on strong evidence of mismatch.

#### Scenario: New bucket has few samples
- **WHEN** a bucket has insufficient samples, runs, layers, or MID time
- **THEN** it SHALL remain TRACKING or STABLE rather than LOCKED

#### Scenario: LOCKED bucket sees a single moderate outlier
- **WHEN** a locked bucket receives one moderate residual outlier
- **THEN** it SHALL remain LOCKED

#### Scenario: LOCKED bucket sees catastrophic, streak, or drift evidence
- **WHEN** catastrophic residual, sustained outlier streak, or sustained EWMA drift thresholds are met
- **THEN** the bucket MAY unlock and return to TRACKING

### Requirement: Tuner lock noise gate is relative to learned rate
The tuner SHALL use residual noise ratio (`sigma/x`) rather than an absolute variance threshold to decide whether a stable bucket is quiet enough to lock.

#### Scenario: High-flow bucket has acceptable relative noise
- **WHEN** residual sigma divided by learned rate is within the configured ratio threshold
- **THEN** the noise gate SHALL allow normal lock criteria to proceed

#### Scenario: Low-flow bucket has high relative noise
- **WHEN** residual sigma divided by learned rate exceeds the configured ratio threshold after warmup
- **THEN** the bucket SHALL remain STABLE with a noise wait reason

### Requirement: Analyzer recommendations use qualifying state-aware contributors
The analyzer SHALL compute baseline, bias, and variance recommendations from qualifying bucket contributors using state-aware weighting, not raw dominant CSV row clusters.

#### Scenario: LOCKED buckets exist in state
- **WHEN** one or more LOCKED buckets exist
- **THEN** analyzer baseline and bias recommendations SHALL use LOCKED buckets as the qualifying set

#### Scenario: Zero LOCKED buckets in safe mode
- **WHEN** safe mode sees no LOCKED buckets and `--force` is not set
- **THEN** the analyzer SHALL write a refused patch with current values and exit non-zero

#### Scenario: Contributors have different precision
- **WHEN** qualifying buckets have different sample counts and residual variance
- **THEN** baseline and bias recommendations SHALL use precision-weighted aggregation over qualifying buckets

### Requirement: Analyzer acceptance gate compares recommendation paths
The acceptance gate SHALL judge consistency using the same recommendation path used for patch emission, filtered to comparable runs.

#### Scenario: Raw per-bucket medians disagree but recommendations agree
- **WHEN** raw CSV bucket medians vary across runs but state-aware recommendations are consistent
- **THEN** the acceptance gate SHALL pass consistency

#### Scenario: A run is immature
- **WHEN** a run lacks enough rows for contributing buckets or has insufficient recommendation confidence
- **THEN** it SHALL be skipped from consistency reduction and reported in patch diagnostics

### Requirement: Acceptance gate distinguishes FAIL from WARN
The analyzer acceptance gate SHALL fail only when recommendations are unreliable or hardware scatter is pathological; stale current config and immature-but-consistent soak signals SHALL be warnings.

#### Scenario: Current variance reference is stale
- **WHEN** observed BP sigma p95 exceeds current `buf_variance_blend_ref_mm` but remains below the hardware ceiling
- **THEN** the gate SHALL warn and emit the corrective recommendation rather than failing

#### Scenario: Contributor mass is in the gray band
- **WHEN** contributor mass is below the warning threshold but above the hard failure floor
- **THEN** the gate SHALL pass with a contributor-mass warning

#### Scenario: Hardware scatter is pathological
- **WHEN** observed BP sigma p95 exceeds the hardware ceiling
- **THEN** the gate SHALL fail and report a hardware-level scatter reason
