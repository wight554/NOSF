# Persistence Contract Specification

## Purpose
Durable contract for NOSF flash-backed runtime parameters and config generation, extracted from `firmware/src/settings_store.c` and `AGENTS.md`.

## Requirements

### Requirement: Settings Version Bump
The runtime settings layout MUST be protected by a strict schema version.

#### Scenario: Struct Modification
- **WHEN** any field is added, removed, or resized in `settings_t`
- **THEN** `SETTINGS_VERSION` MUST be incremented
- **AND** on boot, if flash version mismatches, settings are wiped to default

### Requirement: Flash Loading and Defaults
Missing or corrupt flash SHALL NOT prevent safe boot.

#### Scenario: Fresh Board
- **WHEN** `settings_load()` reads invalid magic or bad CRC
- **THEN** settings are initialized to compiled C defaults
- **AND** written back to flash immediately

### Requirement: Runtime Tunables Flow
Any durable tunable SHALL live in `config.ini` and flow through `gen_config.py`.

#### Scenario: New Parameter
- **WHEN** a new runtime parameter is added
- **THEN** it MUST be represented in `config.ini` and `config.ini.example`
- **AND** consumed in firmware via generated `CONF_*` macros
