# Motion Safety Specification

## Purpose
Durable contract for NOSF motor, filament, and task safety limits, extracted from `firmware/src/motion.c` and `BEHAVIOR.md`.

## Requirements

### Requirement: Dry Spin Protection
The system SHALL halt any spinning motor if no filament is detected at the intake and the buffer is not pulling.

#### Scenario: Filament Lost Mid-Task
- **WHEN** `TASK_FEED` or `TASK_LOAD_FULL` is active
- **AND** the `IN` sensor clears
- **AND** the buffer is not in `BUF_ADVANCE` (pulling a tail)
- **AND** this state persists for > 8 seconds
- **THEN** the motor stops and `FAULT:DRY_SPIN` is emitted
- **AND** automatic background restarts (sync or reload) are blocked until cleared by manual command or new filament insertion

### Requirement: Task Travel Limits
Automated tasks SHALL NOT spin indefinitely without hitting a physical checkpoint.

#### Scenario: Missing Sensor
- **WHEN** `TASK_LOAD_FULL` is running
- **AND** `LOAD_MAX_MM` travel distance is reached without triggering the completion state
- **THEN** the lane stops
- **AND** `LOAD_TIMEOUT` is emitted

### Requirement: Safe Autopreload
Autopreload SHALL only engage for freshly inserted filament and MUST leave the path clear for the other lane.

#### Scenario: Fresh Insertion
- **WHEN** the lane is `IDLE` and its `OUT` sensor is clear
- **AND** `AUTO_PRELOAD` is enabled
- **AND** the `IN` sensor rises
- **THEN** `TASK_AUTOLOAD` starts, drives until `OUT` triggers, then retracts by `RETRACT_MM` to park the tip
