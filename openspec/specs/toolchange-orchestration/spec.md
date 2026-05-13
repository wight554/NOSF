# Toolchange Orchestration Specification

## Purpose
Durable contract for NOSF toolchange (TC) and RELOAD orchestration, defining phase boundaries, timeouts, and state expectations.

## Requirements

### Requirement: Full Automated Toolchange
The system SHALL orchestrate an automated sequence to swap active lanes without host intervention.

#### Scenario: Normal Toolchange
- **WHEN** `TC:<lane>` is commanded
- **THEN** the system executes `UNLOAD_CUT` (if `TC_AUTO_CUT` is enabled), `UNLOAD_REVERSE`, waits for `OUT` to clear, waits for Y-splitter clear, updates `active_lane`, and starts `LOAD_FULL`
- **AND** emits phase events (`TC:CUTTING`, `TC:UNLOADING`, `TC:SWAPPING`, `TC:LOADING`, `TC:DONE`) at boundaries

### Requirement: Manual Cutter Execution
The host SHALL be able to trigger the exact cutter sequence independently of a full toolchange.

#### Scenario: Manual Cut with Feed
- **WHEN** `CU:` is commanded
- **THEN** the system executes the full cutter state machine (Open -> Feed -> Close -> Open -> Repeat -> Block)
- **AND** emits `EV:CUT:DONE` upon successful parking
- **AND** emits `EV:CUT:ERROR` upon failure or timeout

#### Scenario: Manual Cut without Feed (Bare)
- **WHEN** `CX:` is commanded
- **THEN** the system executes the cutter state machine but skips the filament feed logic (Open -> Close -> Open -> Repeat -> Block)
- **AND** emits `EV:CUT:DONE` upon successful parking
- **AND** emits `EV:CUT:ERROR` upon failure or timeout

### Requirement: RELOAD Buffer-Driven Contact
During runout RELOAD, the new lane SHALL approach until physical buffer contact is detected.

#### Scenario: RELOAD Approach
- **WHEN** the old lane clears the Y-splitter and `RELOAD_JOIN_MS` elapses
- **THEN** the new lane starts `TASK_FEED` at `JOIN_SPS`
- **AND** waits for the buffer to hit `BUF_TRAILING`
- **AND** aborts if the configured travel limit or physical timeout is reached before contact

### Requirement: RELOAD Bang-Bang Pressure Cycle
During the RELOAD follow phase, the new lane SHALL over-feed to close the gap and maintain pressure on the old tail.

#### Scenario: Follow Phase
- **WHEN** physical contact is established (`BUF_TRAILING`)
- **THEN** the motor target becomes `extruder_est_sps * RELOAD_LEAN_FACTOR` (over-feeding)
- **AND** drops to `TRAILING_RATE` if the physical arm hits the `TRAILING` wall
- **AND** repeats this cycle until `LOADED` (toolhead sensor triggered or `BUF_ADVANCE` sustained)
