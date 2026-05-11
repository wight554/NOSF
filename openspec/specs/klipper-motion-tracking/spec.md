# Klipper Motion Tracking Specification

## Purpose

Capture the OpenSpec-native contract for Phase 2.10 Klipper sidecar and UDS
motion tracking. Old planning prose is available through git history when
needed.

## Requirements

### Requirement: Marker context shall come from slicer sidecar metadata

Host tooling SHALL support marker context generated from slicer sidecar metadata
rather than relying only on manually inserted G-code markers.

#### Scenario: A sidecar file is provided

- **WHEN** the tuner starts with a valid sidecar path
- **THEN** feature and velocity marker events are synthesized from the sidecar
- **AND** those markers drive the same tuner bucket assignment surface as M118
  marker strings

### Requirement: Klipper UDS tracking shall preserve on_m118 ingress

The Klipper Unix-domain-socket flow SHALL feed synthesized marker strings into
the existing `on_m118` contract without changing that contract.

#### Scenario: SegmentMatcher emits a feature transition

- **WHEN** motion tracking identifies a new feature/velocity segment
- **THEN** the synthesized string enters the tuner through `on_m118`
- **AND** existing `NT:START`, `NT:LAYER`, `NOSF_TUNE`, and finish dispatch
  semantics remain unchanged

### Requirement: SegmentMatcher event surface shall remain stable

The motion tracker SHALL keep the SegmentMatcher event surface compatible with
existing tests and tuner consumers.

#### Scenario: A segment boundary is crossed

- **WHEN** Klipper position events advance past a sidecar segment boundary
- **THEN** SegmentMatcher emits the expected marker event once
- **AND** downstream tuner logic does not need to know whether the marker came
  from G-code or sidecar tracking

### Requirement: Motion tracking shall be host-only

Phase 2.10 marker replacement SHALL NOT require firmware changes.

#### Scenario: The sidecar tracker is enabled

- **WHEN** the host follows Klipper motion over UDS
- **THEN** firmware protocol and RP2040 runtime behavior remain unchanged
- **AND** calibration still observes firmware telemetry over the existing serial
  interface

### Requirement: Fallback marker paths shall remain available

The workflow SHALL retain the ability to use direct marker lines when sidecar or
UDS tracking is unavailable.

#### Scenario: UDS connection is unavailable

- **WHEN** the operator cannot use the Klipper sidecar flow
- **THEN** existing marker ingestion paths remain usable
- **AND** tuner bucket logic receives the same logical marker state
