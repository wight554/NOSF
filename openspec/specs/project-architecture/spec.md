# Project Architecture Specification

## Purpose

Capture the durable firmware architecture and workflow contracts that were
previously only summarized in `CONTEXT.md`. `CONTEXT.md` remains a compact
navigation guide; this spec is the OpenSpec-native contract agents should read
when changing firmware structure, runtime parameters, protocol behavior, or
persistence.

## Requirements

### Requirement: Firmware shall remain cooperative and non-blocking

NOSF firmware SHALL run as cooperative RP2040 firmware without an RTOS, with the
main loop calling non-blocking module ticks.

#### Scenario: A module adds runtime work

- **WHEN** firmware adds new behavior in `main.c`, `motion.c`, `sync.c`,
  `toolchange.c`, `protocol.c`, or settings code
- **THEN** the behavior is implemented as bounded cooperative work
- **AND** it does not introduce blocking loops that prevent other module ticks
  from running

### Requirement: Module ownership shall stay explicit

Each firmware module SHALL keep ownership aligned with the documented
architecture boundaries.

#### Scenario: A change affects toolchange behavior

- **WHEN** a change modifies cutter, toolchange, or RELOAD state transitions
- **THEN** primary logic belongs in `firmware/src/toolchange.c`
- **AND** shared declarations belong in module headers or
  `firmware/include/controller_shared.h`
- **AND** unrelated modules are touched only for required integration points

### Requirement: Runtime tunables shall follow the full parameter path

Persistent runtime tunables SHALL be represented consistently across config
files, generated firmware headers, runtime storage, serial protocol, and docs.

#### Scenario: A new persistent tunable is added

- **WHEN** a new persistent runtime parameter is introduced
- **THEN** `config.ini.example` and `config.ini` include the key
- **AND** `scripts/gen_config.py` emits the generated default/macro
- **AND** owning runtime variables and settings persistence are wired
- **AND** matching `SET:` and `GET:` protocol handlers exist
- **AND** operator documentation is updated
- **AND** `SETTINGS_VERSION` is bumped when `settings_t` layout changes

### Requirement: Serial protocol changes shall preserve reply semantics

USB serial commands SHALL continue using `CMD:params\n` input and `OK:` / `ER:`
reply semantics, with best-effort `EV:` events where applicable.

#### Scenario: A new serial command is added

- **WHEN** `firmware/src/protocol.c` handles a new command
- **THEN** successful outcomes reply with `OK`
- **AND** failures reply with `ER`
- **AND** command behavior is documented in `MANUAL.md`

### Requirement: Persistence shall remain activity-gated

Flash persistence commands SHALL be rejected while motion, toolchange, cutter
activity, or boot stabilization could make persistence unsafe.

#### Scenario: Operator sends `SV:` while motion is active

- **WHEN** persistence is requested during an unsafe activity window
- **THEN** firmware rejects the request with the busy persistence error
- **AND** settings flash is not modified

### Requirement: Sync shall not run during toolchange or RELOAD

Normal sync control SHALL remain guarded so it runs only when the toolchange
context is idle.

#### Scenario: Firmware enters RELOAD follow

- **WHEN** the toolchange context is not idle
- **THEN** normal sync tick behavior does not attempt to rescue or override the
  RELOAD/toolchange state machine
- **AND** RELOAD-specific buffer-driven logic owns that behavior

### Requirement: Load and unload safety shall remain distance-based

Load, unload, autoload, and related lane tasks SHALL use distance limits and
sensor state rather than legacy names that imply time-only limits.

#### Scenario: A load task reaches its travel limit

- **WHEN** the configured distance limit is reached before the expected sensor
  transition
- **THEN** the lane task stops and reports the appropriate fault/phase outcome
- **AND** toolchange phases react to the lane task result

### Requirement: Shared speed conversion helpers shall remain consistent

Speed conversion SHALL use shared helper functions rather than duplicate
conversions between slicer units, firmware steps-per-second, and protocol
values.

#### Scenario: Protocol reports or accepts a speed value

- **WHEN** a command converts between mm/min and steps-per-second
- **THEN** it uses the shared conversion helpers declared in
  `controller_shared.h`
- **AND** protocol and settings code agree on the conversion

### Requirement: Board pin assumptions shall live in config headers

Board-level pin assignments and hardware constants SHALL remain centralized in
`firmware/include/config.h` and generated tune headers where applicable.

#### Scenario: Hardware pin mapping changes

- **WHEN** a board pin assignment changes
- **THEN** the source of truth is updated in `config.h`
- **AND** `HARDWARE.md` is updated to match
