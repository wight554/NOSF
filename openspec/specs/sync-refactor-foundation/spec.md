# Sync Refactor Foundation Specification

## Purpose

Capture the OpenSpec-native contract for the original sync refactor foundation
from `openspec/design/sync-refactor/SYNC_REFACTOR_PLAN.md`. This spec preserves
the durable behavior expected from Phases 0 through 2.7 without requiring agents
to start from the historical phase plan.

## Requirements

### Requirement: Firmware sync shall remain standalone after calibration

NOSF firmware SHALL run sync behavior from compiled configuration and runtime
settings without requiring a Klipper plugin or host daemon during normal
printing.

#### Scenario: Host tooling is absent during a print

- **WHEN** calibration has already produced reviewed settings
- **AND** those settings have been generated into firmware configuration
- **THEN** the RP2040 firmware continues controlling sync from local sensors,
  estimator state, and runtime tunables
- **AND** no host process is required for nominal sync operation

### Requirement: Sync hardening shall be additive and default-compatible

Instrumentation, estimator confidence, and buffer-behavior changes SHALL be
introduced so existing default behavior remains recognizable unless the operator
opts into new calibration-derived settings.

#### Scenario: Firmware boots with default configuration

- **WHEN** no reviewed calibration patch has been applied
- **THEN** sync defaults remain conservative and compatible with the previous
  behavior profile
- **AND** added diagnostics do not alter motion by themselves

### Requirement: Runtime tunables shall flow through config generation

Any durable sync tunable SHALL live in `config.ini` and `config.ini.example`,
flow through `scripts/gen_config.py`, and be consumed from generated
`firmware/include/tune.h` or the matching runtime settings path.

#### Scenario: A new sync parameter is added

- **WHEN** an implementation adds a sync-related runtime default
- **THEN** the value is represented in the config files
- **AND** generated firmware headers expose the value
- **AND** operator documentation describes the setting

### Requirement: Telemetry shall support offline calibration analysis

Firmware and host tooling SHALL expose enough sync, buffer, and estimator
signals for offline calibration to infer stable operating parameters.

#### Scenario: A calibration print is recorded

- **WHEN** the operator captures tune/analyzer telemetry
- **THEN** buffer position, estimated rate, confidence, and relevant marker
  context are available to host analysis
- **AND** the analysis can recommend settings without changing firmware behavior
  during capture

### Requirement: Regression impact shall be reviewed across firmware flows

Changes to sync behavior SHALL consider preload, load, unload, toolchange, sync,
RELOAD, persistence, protocol, and documentation effects before landing.

#### Scenario: A sync change touches firmware behavior

- **WHEN** a pull request or agent task modifies sync firmware behavior
- **THEN** the implementation notes identify affected flows
- **AND** validation covers the flows that could regress
