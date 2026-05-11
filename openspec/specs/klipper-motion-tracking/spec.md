# Klipper Motion Tracking Specification

## Purpose
Klipper sidecar + UDS motion tracking contract.

## Requirements

### REQ: Sidecar Metadata Markers
Synthesize markers from slicer sidecar JSON, not G-code strings.
- **SCEN: Sidecar Path**: synth feature/velocity events from sidecar metadata.

### REQ: UDS Ingress Parity
UDS flow feeds existing `on_m118` contract.
- **SCEN: Segment Transition**: Identifty transition -> synth marker string -> feed `on_m118`. Dispatch stable.

### REQ: Stable Matcher Surface
`SegmentMatcher` MUST remain compatible with tuner/tests.
- **SCEN: Segment Crossing**: Cross boundary -> emit marker ONCE. Tuner agnostic to source.

### REQ: Host-Only
Tracking MUST NOT require firmware changes.

### REQ: Fallback Paths
Retain manual marker support when UDS/sidecar unavailable.

## Historical Rationale and Constraints

### Stutter Removal
Sidecar + UDS avoids `RUN_SHELL_COMMAND` latency (stutter). Passive measurement.

### UDS Contract
- **Paths**: `/tmp/klippy_uds`, `/tmp/klippy.sock`.
- **Subs**: `print_stats`, `virtual_sdcard`, `motion_report`, `extruder`, `display_status`.
- **Z-Sanity**: uses Z + cumulative E to handle `file_position` jumps.

### Frozen
- **Sidecar SHA**: mismatch -> REFUSE. Re-run `gcode_marker.py`.
- **Boundaries**: Throttled deltas. ONCE per crossing.
- **Retract Guard**: `live_extruder_velocity` jitter filtered.
- **Canceled Objects**: `EXCLUDE_OBJECT` zones tagged "skip" in sidecar.
