# Klipper Motion Tracking Specification

## Purpose
Klipper sidecar and UDS motion tracking behavioral contracts and requirements.

## Requirements

### Requirement: Sidecar Metadata Markers
The system SHALL synthesize markers from slicer sidecar JSON rather than G-code strings.

#### Scenario: Sidecar Path
- **WHEN** tuner starts with valid sidecar path
- **THEN** feature and velocity markers synthesized from metadata
- **AND** markers drive the existing tuner bucket surface

### Requirement: UDS Ingress Parity
The Klipper UDS flow SHALL feed the existing `on_m118` ingress contract.

#### Scenario: Segment Transition
- **WHEN** motion tracking identifies segment crossing
- **THEN** synthesized marker string enters `on_m118`
- **AND** existing dispatch semantics remain unchanged

### Requirement: Stable Matcher Surface
The `SegmentMatcher` SHALL remain compatible with existing tuner and test suites.

#### Scenario: Segment Crossing
- **WHEN** Klipper position advances past boundary
- **THEN** matcher emits expected marker event exactly ONCE
- **AND** tuner remains agnostic to marker source

### Requirement: Host-Only Integration
Motion tracking SHALL NOT require firmware changes to operate.

#### Scenario: Sidecar Tracker Active
- **WHEN** host follows Klipper motion over UDS
- **THEN** firmware protocol and runtime behavior remain unchanged

### Requirement: Fallback Paths
The workflow MUST retain manual G-code marker support when UDS or sidecar is unavailable.

#### Scenario: UDS Connection Unavailable
- **WHEN** Klipper UDS flow cannot be established
- **THEN** existing G-code marker ingestion remains usable
- **AND** tuner logic receives same logical marker state

## Historical Rationale and Constraints

### Stutter Removal
Sidecar + UDS avoids `RUN_SHELL_COMMAND` latency (stutter). Calibration becomes a passive measurement.

### UDS Contract
- **Default Paths**: `/tmp/klippy_uds`, `/tmp/klippy.sock`.
- **Subscriptions**: `print_stats`, `virtual_sdcard`, `motion_report`, `extruder`, `display_status`.
- **Z-Sanity**: Uses Z-axis + cumulative E-axis to handle non-monotonic `file_position` jumps.

### Frozen Constraints
- **Sidecar SHA**: Mismatch -> REFUSE to load. Operator MUST re-run `gcode_marker.py`.
- **Boundaries**: Throttled deltas. Emitted exactly ONCE per crossing.
- **Retract Guard**: `live_extruder_velocity` jitter filtered.
- **Canceled Objects**: `EXCLUDE_OBJECT` zones tagged as "skip" in the sidecar.
