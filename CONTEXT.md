# NOSF — Project Context

Deep reference for firmware work. This file is the architectural source of truth
for agents; `TASK.md` is a task log and may contain historical notes that no
longer describe the current tree.

---

## Firmware Architecture

NOSF is a cooperative firmware for RP2040 with no RTOS. The main loop calls a
small set of non-blocking module ticks every iteration.

### Module ownership

- `firmware/src/main.c`
  - hardware bring-up
  - shared runtime globals
  - autopreload edge detection
  - LED state
  - main-loop ordering
- `firmware/src/motion.c`
  - debounced IN / OUT sensors
  - stepper PWM helpers
  - per-lane task execution (`TASK_AUTOLOAD`, `TASK_UNLOAD`, `TASK_LOAD_FULL`, `TASK_MOVE`, `TASK_FEED`)
- `firmware/src/sync.c`
  - buffer sensing
  - estimator-driven sync controller
  - auto-start / auto-stop sync behavior
  - boot-time buffer stabilization
- `firmware/src/toolchange.c`
  - cutter state machine
  - toolchange state machine
  - RELOAD approach / follow logic
- `firmware/src/protocol.c`
  - USB CDC parser
  - `OK:` / `ER:` replies and best-effort `EV:` events
  - `?:`, `SET:`, `GET:`, and advanced TMC access commands
- `firmware/src/settings_store.c`
  - flash-backed settings schema
  - defaults/save/load/reset
  - TMC re-apply after settings changes

Shared types, globals, and cross-module helpers live in `firmware/include/controller_shared.h`.

---

## Key Runtime Structures

### `lane_t`

Per-lane motion and sensor state.

Important fields:

- `in_sw`, `out_sw`: debounced IN / OUT inputs
- `m`: step/dir/enable motor handle
- `task`: current lane task
- `task_limit_mm`, `task_dist_mm`: distance-based task limits and progress
- `target_sps`, `current_sps`: ramp target and current command speed
- `fault`: `FAULT_NONE`, `FAULT_TIMEOUT`, `FAULT_SENSOR`, `FAULT_BUF`, `FAULT_CUT`, `FAULT_DRY_SPIN`
- `unload_to_in`, `unload_buf_recover_done`: unload-path bookkeeping

### `tc_ctx_t`

Toolchange / RELOAD state.

Important fields:

- `state`: `TC_IDLE` through `TC_ERROR`
- `target_lane`, `from_lane`
- `phase_start_ms`: current phase timing origin
- `reload_tick_ms`, `reload_current_sps`, `last_trailing_ms`: RELOAD follow control state

### `buf_tracker_t`

Buffer-zone transition history used by the estimator.

Important fields:

- `state`: `BUF_MID`, `BUF_ADVANCE`, `BUF_TRAILING`, `BUF_FAULT`
- `entered_ms`, `dwell_ms`
- `arm_vel_mm_s`
- `mmu_sps_at_entry`, `mmu_sps_dwell_sum`, `mmu_sps_dwell_samples`

---

## Runtime Parameter Pattern

Persistent tunables follow this path:

1. Add or update the key in `config.ini.example` and `config.ini`.
2. Add the default and generated `CONF_*` macro in `scripts/gen_config.py`.
3. Regenerate `firmware/include/tune.h` with `python3 scripts/gen_config.py`.
4. Add or update the owning runtime variable in the appropriate module (`main.c`, `motion.c`, `sync.c`, `toolchange.c`, or another owner; shared externs belong in `controller_shared.h`).
5. Add the field to `settings_t` in `firmware/src/settings_store.c` if the value must persist.
6. Wire defaults/save/load/reset in `settings_store.c`.
7. If the value affects hardware registers, update the TMC apply path in `settings_store.c`.
8. Add `SET:` / `GET:` handling in `firmware/src/protocol.c`.
9. Update the relevant docs (`MANUAL.md`, `BEHAVIOR.md`, `README.md`, etc.).
10. Bump `SETTINGS_VERSION` in `firmware/src/settings_store.c` whenever `settings_t` layout changes.

Current `SETTINGS_VERSION`: `38` in `firmware/src/settings_store.c`.

---

## Critical Gotchas

### 1. Sync only runs in `TC_IDLE`

`sync_tick()` is guarded so normal sync never runs during toolchange or RELOAD.
If you change RELOAD behavior, do not expect the normal sync tick to rescue it.

### 2. RELOAD is buffer-driven now

- `TC_RELOAD_APPROACH` waits for `BUF_TRAILING` contact.
- `TC_RELOAD_FOLLOW` reuses the estimator with `RELOAD_LEAN_FACTOR`.
- There is no driver-load or DIAG-based stall handling in the current firmware flow.

### 3. Load / unload safety is distance-based

- `AUTOLOAD_MAX`, `LOAD_MAX`, and `UNLOAD_MAX` are travel limits.
- Toolchange phases such as `TC_LOAD_WAIT_TH` or `TC_UNLOAD_WAIT_OUT` observe the underlying lane task and react when it stops.
- Older names like `TC_LOAD_MS` / `TC_UNLOAD_MS` are legacy protocol aliases, not real time-based limits.

### 4. Persistence is activity-gated

`SV:`, `LD:`, and `RS:` are rejected with `ER:PERSIST_BUSY` while motion,
toolchange, cutter activity, or boot stabilization is active.

### 5. Speed conversion helpers are shared

`mm_per_min_to_sps*()` and `sps_to_mm_per_min*()` are implemented in `main.c`
and declared in `controller_shared.h`. Protocol and settings code rely on them.

### 6. Board pins live in `config.h`

`firmware/include/config.h` is the source of truth for pin assignment and other
board constants. DIAG pins remain defined for board completeness, but current
firmware does not attach DIAG IRQ handling.

---

## Common Operations

### Add a new `SET:` / `GET:` parameter

- Add config key and generated macro.
- Update the runtime variable and persistence path if needed.
- Add `SET` / `GET` branches in `firmware/src/protocol.c`.
- Regenerate `tune.h` and update the docs.

### Add a new serial command

- Implement the command in `cmd_execute()` in `firmware/src/protocol.c`.
- Use module APIs from `motion.h`, `sync.h`, `toolchange.h`, and `settings_store.h`.
- Document it in `MANUAL.md`.

### Run the static regression gate

- `bash scripts/validate_regression.sh`
- Use this before hardware testing to catch config, build, script, and diff-integrity regressions quickly.

### Emit a reply or event

- `cmd_reply("OK", data)` / `cmd_reply("ER", reason)`
- `cmd_event("TYPE", data)`

Remember that `EV:` output is best-effort and rate-limited.

---

## Navigation Guide

| Task | Read |
|------|------|
| Add or modify a runtime parameter | This file + `protocol.c` + `settings_store.c` + `config.ini.example` |
| Change motion, load, unload, or runout behavior | `motion.c` + `BEHAVIOR.md` |
| Change sync / buffer behavior | `sync.c` + `BEHAVIOR.md` |
| Change RELOAD or toolchange flow | `toolchange.c` + `BEHAVIOR.md` |
| Change serial protocol behavior | `protocol.c` + `MANUAL.md` |
| Change board pins or hardware assumptions | `config.h` + `HARDWARE.md` |
| Run or extend bring-up / regression validation | `TEST_CASES.md` + `BUILD_FLASH.md` |
| Change agent workflow / repo rules | `AGENTS.md` + `WORKFLOW.md` |