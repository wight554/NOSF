# Klipper Motion Tracking Specification

## Purpose
Klipper sidecar and UDS motion tracking contract.

## Requirements

### Requirement: Sidecar Metadata Markers
Synthesize markers from slicer sidecar JSON, not G-code strings.
- **Scenario: Sidecar Path**: Synthesize feature and velocity events from sidecar metadata.

### Requirement: UDS Ingress Parity
UDS flow feeds the existing `on_m118` ingress contract.
- **Scenario: Segment Transition**: Identify transition -> synthesize marker string -> feed `on_m118`. Dispatch logic stable.

### Requirement: Stable Matcher Surface
`SegmentMatcher` MUST remain compatible with tuner and tests.
- **Scenario: Segment Crossing**: Cross boundary -> emit marker ONCE. Tuner is agnostic to source.

### Requirement: Host-Only
Motion tracking MUST NOT require firmware changes.

### Requirement: Fallback Paths
Retain manual G-code marker support when UDS or sidecar is unavailable.

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
