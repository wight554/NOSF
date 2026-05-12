# Klipper Integration Specification

## Purpose
Durable contract for NOSF Klipper integration (`nosf_cmd.py`), extracted from `KLIPPER.md` and script sources.

## Requirements

### Requirement: Host Serial Control
The Klipper host MUST interact with NOSF via single-command CDC serial transactions.

#### Scenario: Script Invocation
- **WHEN** a Klipper macro calls `nosf_cmd.py`
- **THEN** the script opens the serial port, sends the formatted command
- **AND** blocks until an `OK:` or `ER:` response is received
- **AND** returns the result to Klipper via stdout

### Requirement: Motion Tracking Sidecar
The sidecar (`--uds`) SHALL track Klipper's print state and forward speed events to NOSF.

#### Scenario: UDS Stream
- **WHEN** Klipper's Unix Domain Socket emits toolhead or print_stats changes
- **THEN** the sidecar translates them into `NOSF_TUNE` events and `BASELINE_SPS` updates
- **AND** sends them over serial without blocking normal macro commands

### Requirement: Macro Orchestration
Toolchange macros (`_NOSF_TC`) SHALL coordinate the extruder, MMU, and toolhead state.

#### Scenario: Full TC Macro
- **WHEN** a toolchange is triggered
- **THEN** Klipper shapes the tip, calls `nosf_cmd.py TC:lane`, waits for `DONE`, and loads the new filament into the extruder
