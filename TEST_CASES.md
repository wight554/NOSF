# NOSF Test Cases

Practical bring-up and regression checklist for real hardware.

Use this document for repeatable validation after firmware changes, new wiring,
or tuning updates. It is intentionally operator-facing: every test has a clear
setup, command sequence, and expected result.

---

## Safety

- Start with reduced speeds for first motion on new hardware or after major firmware changes.
- Keep filament clear of the toolhead path unless the test explicitly requires a full load.
- Be ready to send `ST:` immediately if direction, sensor polarity, or lane selection looks wrong.
- Do not run RELOAD tests unattended.

Recommended temporary low-speed setup for first validation:

```bash
python3 scripts/nosf_cmd.py \
  "SET:FEED_RATE:600" \
  "SET:REV_RATE:600" \
  "SET:AUTO_RATE:400" \
  "SET:JOIN_RATE:400" \
  "SET:PRESS_RATE:300"
```

---

## Preconditions

- Firmware builds successfully.
- `config.ini` matches the target hardware.
- Board flashes and enumerates over USB CDC.
- `python3 scripts/nosf_cmd.py "VR:" "?:"` returns valid replies.
- IN / OUT sensors, optional Y sensor, and optional toolhead reporting path are wired as expected.

If this is a new setup, run the build and flash flow in `BUILD_FLASH.md` first.

---

## Code Analysis Regression Gate

Run this section before hardware validation when a change touches firmware,
scripts, config generation, persistence, or the serial protocol. These checks
do not prove runtime behavior, but they catch the most common integration
breaks before you flash a board.

Quick path:

```bash
bash scripts/validate_regression.sh
```

This runs the default static gate in one command. Use the detailed cases below
when you need to understand which layer failed.

### A. Generated Config Sync

#### Goal

Confirm config generation still works and the generated header matches the
current source inputs.

#### Steps

```bash
python3 scripts/gen_config.py
git diff --check
```

#### Expected Result

- `gen_config.py` completes without errors.
- No malformed generated content is introduced.
- If the change intentionally modifies compile-time defaults, the resulting
  `tune.h` update is expected and explainable.

#### Use When

- `config.ini`
- `config.ini.example`
- `scripts/gen_config.py`
- `firmware/include/tune.h` consumers

### B. Firmware Compile Regression

#### Goal

Confirm the firmware still compiles against the real Pico SDK target.

#### Steps

```bash
ninja -C build_local
```

#### Expected Result

- Build completes successfully.
- No new warnings or link failures appear in the touched area.
- Extracted modules still resolve all shared symbols and headers correctly.

#### Use When

- Any file under `firmware/`
- Any generated compile-time config change

### C. Python Helper Syntax Regression

#### Goal

Catch syntax errors in host-side helper scripts before runtime testing.

#### Steps

```bash
python3 -m py_compile scripts/*.py
```

#### Expected Result

- Command exits cleanly with no syntax errors.

#### Use When

- Any file under `scripts/`

### D. Diff Hygiene Regression

#### Goal

Catch whitespace errors, malformed patches, and broken formatting in tracked
changes.

#### Steps

```bash
git diff --check
```

#### Expected Result

- No output.

#### Use When

- Every non-trivial change before commit

### E. Settings Schema And Persistence Safety Review

#### Goal

Make sure settings layout and persistence behavior stay internally consistent.

#### Steps

1. Review the diff in `firmware/src/settings_store.c`.
2. If `settings_t` changed, confirm `SETTINGS_VERSION` was bumped.
3. If a tunable was added or removed, confirm the full path is present:
  `config.ini.example` -> `scripts/gen_config.py` -> owning runtime variable -> `settings_store.c` -> `protocol.c` -> docs.
4. If persistence commands or busy guards changed, confirm `SV:`, `LD:`, and `RS:` semantics still match `MANUAL.md`.

#### Expected Result

- `SETTINGS_VERSION` changes whenever persisted layout changes.
- No new runtime tunable exists only in one layer.
- Persistence behavior stays aligned with the documented protocol.

#### Use When

- `firmware/src/settings_store.c`
- `firmware/src/protocol.c`
- `config.ini.example`
- `scripts/gen_config.py`

### F. Protocol And Documentation Surface Review

#### Goal

Catch command, event, or parameter drift between code and the operator docs.

#### Steps

1. Review changed `SET:` / `GET:` / command handlers in `firmware/src/protocol.c`.
2. Confirm any new or removed command, event, or parameter is reflected in:
  - `MANUAL.md`
  - `BEHAVIOR.md` if behavior changed
  - `README.md` or `KLIPPER.md` if operator workflow changed
3. If the change modifies status output fields, confirm the examples in `TEST_CASES.md` still make sense.

#### Expected Result

- No command exists only in code or only in docs.
- Parameter names and units stay consistent.
- Status snapshots remain representative of the real protocol surface.

#### Use When

- `firmware/src/protocol.c`
- Any operator-facing documentation update tied to runtime behavior

### Suggested Minimum Static Gate By Change Type

| Change Type | Minimum Checks |
|-------------|----------------|
| Firmware logic only | B, D, F |
| Settings or tunables | A, B, D, E, F |
| Python scripts only | C, D |
| Config generation only | A, B, D, E |
| Docs-only protocol cleanup | D, F |

Do not skip the hardware tests later for motion, sync, or RELOAD changes. This
gate is meant to fail fast on integration mistakes, not replace real-hardware
validation.

---

## Acceptance Checklist By Lane

Use this table to record whether each lane passes the minimum motion path before
you move on to sync, toolchange, or RELOAD tests.

| Check | Lane 1 | Lane 2 | Notes |
|------|--------|--------|-------|
| `T:n` selects the correct lane | ☐ | ☐ | `?:` shows `LN:1` or `LN:2` as expected |
| IN sensor changes only the matching `I*` field | ☐ | ☐ | Verify no cross-talk |
| OUT sensor changes only the matching `O*` field | ☐ | ☐ | Verify no cross-talk |
| `LO:` reaches OUT and parks cleanly | ☐ | ☐ | No timeout, no wrong direction |
| `UL:` clears OUT cleanly | ☐ | ☐ | No lingering downstream filament |
| `UM:` clears IN cleanly | ☐ | ☐ | Lane becomes physically empty |
| `FL:` reaches toolhead path correctly | ☐ | ☐ | Requires toolhead presence reporting |
| No unexpected dry-spin or timeout fault in nominal path | ☐ | ☐ | Investigate before higher-level tests |

Do not treat toolchange or RELOAD failures as meaningful until both lane columns
are green for the basic motion path.

---

## 1. Build And Serial Smoke Test

### Goal

Confirm the host tools, firmware image, USB CDC interface, and base protocol are working.

### Steps

```bash
python3 scripts/gen_config.py
cmake --build build_local
python3 scripts/nosf_cmd.py "VR:" "?:"
python3 scripts/nosf_cmd.py --dump --raw
```

### Expected Result

- `gen_config.py` regenerates `firmware/include/tune.h` without errors.
- Build completes successfully.
- `VR:` returns the current firmware version.
- `?:` returns a complete status line with lane, toolchange, sensor, sync, and RELOAD fields.
- `--dump --raw` returns the current runtime parameter surface without missing keys or stale names.

---

## 2. Sensor Polarity And Idle State

### Goal

Confirm each discrete sensor reports correctly before motion tests.

### Steps

1. With no filament inserted, run `python3 scripts/nosf_cmd.py "?:"`.
2. Manually trigger each lane IN sensor and verify the corresponding `I1` or `I2` state changes.
3. Manually trigger each lane OUT sensor and verify `O1` or `O2`.
4. If fitted, trigger the Y-splitter sensor and verify `YS`.
5. If the host reports toolhead state, send `python3 scripts/nosf_cmd.py "TS:1" "?:"` and then `python3 scripts/nosf_cmd.py "TS:0" "?:"`.

### Expected Result

- Each sensor toggles the matching status field and no unrelated field changes.
- The controller remains idle during all manual sensor checks.
- Toolhead presence only changes `TH`.

---

## 3. Active Lane Selection

### Goal

Verify manual lane selection and active-lane reporting.

### Steps

```bash
python3 scripts/nosf_cmd.py "T:1" "?:"
python3 scripts/nosf_cmd.py "T:2" "?:"
```

### Expected Result

- `T:1` sets `LN:1`.
- `T:2` sets `LN:2`.
- `EV:ACTIVE` may be emitted if event output is enabled and the host is connected.

---

## 4. Preload And Unload Per Lane

### Goal

Validate the basic lane motion primitives without involving the toolhead.

### Steps

Run this sequence once for lane 1 and once for lane 2.

```bash
python3 scripts/nosf_cmd.py "T:1" "LO:" "?:"
python3 scripts/nosf_cmd.py "UL:" "?:"
python3 scripts/nosf_cmd.py "UM:" "?:"
```

Repeat with `T:2`.

### Expected Result

- `LO:` drives filament to the lane OUT sensor and parks just before it.
- After preload, the selected lane is active.
- `UL:` retracts out of the downstream path and clears the OUT sensor.
- `UM:` retracts fully until the IN sensor clears.
- If a distance limit is hit, the controller emits a timeout event instead of running forever.

---

## 5. Full Load To Toolhead

### Goal

Confirm the full-load path, including toolhead sensor handoff.

### Steps

1. Insert filament into the selected lane.
2. Start a full load:

```bash
python3 scripts/nosf_cmd.py "T:1" "FL:"
```

3. When filament reaches the extruder entry, have the host or test operator report toolhead presence:

```bash
python3 scripts/nosf_cmd.py "TS:1" "?:"
```

4. Clear the simulated toolhead state afterward:

```bash
python3 scripts/nosf_cmd.py "TS:0"
```

### Expected Result

- `FL:` runs until toolhead presence is reported.
- The firmware emits `EV:LOADED:<lane>` on success.
- If no toolhead presence arrives, motion stops at `LOAD_MAX` rather than running indefinitely.

---

## 6. Toolchange

### Goal

Verify unload, lane swap, and load sequencing in MMU mode.

### Steps

1. Preload or load both lanes so either lane can become active.
2. Disable autonomous RELOAD for this test:

```bash
python3 scripts/nosf_cmd.py "SET:RELOAD_MODE:0"
```

3. Trigger a toolchange:

```bash
python3 scripts/nosf_cmd.py "T:1" "TC:2"
python3 scripts/nosf_cmd.py "?:"
```

### Expected Result

- The controller emits `TC:` progress events through unload, swap, and load phases.
- The previous lane retracts cleanly.
- The target lane becomes active at the end of the sequence.
- Toolchange finishes in `TC:IDLE` or reports a clear `TC:ERROR` fault state.

---

## 7. Sync Auto-Start And Auto-Stop

### Goal

Confirm the buffer-driven sync controller starts, follows demand, and stops correctly.

### Steps

1. Enable automatic flow and sync:

```bash
python3 scripts/nosf_cmd.py "SET:AUTO_MODE:1" "SM:1"
```

2. Put the active lane in a loaded state where the downstream path can pull filament.
3. Pull the buffer into `ADVANCE` and monitor with repeated `?:` calls.
4. Let the system settle, then hold the buffer in `TRAILING` long enough to exceed `SYNC_AUTO_STOP`.

### Expected Result

- `BUF_ADVANCE` can trigger `EV:SYNC:AUTO_START`.
- Status shows sync active and `SPS` / `BL` / `BS` fields updating.
- Sustained `TRAILING` eventually triggers `EV:SYNC:AUTO_STOP`.
- Sync does not run during toolchange or RELOAD phases.

---

## 8. RELOAD Runout Recovery

### Goal

Validate autonomous lane switching on runout using the buffer-driven RELOAD path.

### Steps

1. Prepare two lanes: one active lane near depletion, one standby lane preloaded.
2. Enable RELOAD:

```bash
python3 scripts/nosf_cmd.py "SET:RELOAD_MODE:1" "SET:AUTO_MODE:1"
```

3. Cause a runout on the active lane.
4. Observe status and events during `RELOAD_WAIT_Y`, `RELOAD_APPROACH`, and `RELOAD_FOLLOW`.

### Expected Result

- The controller emits `EV:RUNOUT:<lane>` followed by `RELOAD:` progress events.
- The old lane stops once the swap path transfers ownership.
- The new lane approaches until buffer contact, then follows with bounded under-feed.
- RELOAD exits on successful pickup or fails with a visible timeout / fault instead of running forever.

---

## 9. Persistence Guarding

### Goal

Confirm flash-backed settings commands are blocked during unsafe activity and allowed when idle.

### Steps

1. Start any motion, for example `FD:`.
2. While motion is active, send:

```bash
python3 scripts/nosf_cmd.py "SV:" "LD:" "RS:"
```

3. Stop motion with `ST:`.
4. Repeat the same commands while idle.

### Expected Result

- During motion, each persistence command returns `ER:PERSIST_BUSY`.
- While idle, the commands succeed.
- Saved values survive a reboot when that behavior is being explicitly tested.

---

## 10. Flash And Post-Flash Smoke

### Goal

Confirm the board can re-enter BOOTSEL from firmware and return to a working serial state after reflashing.

### Steps

```bash
python3 scripts/nosf_cmd.py "BOOT:"
bash scripts/flash_nosf.sh
python3 scripts/nosf_cmd.py "VR:" "?:"
```

### Expected Result

- `BOOT:` reboots the board into RP2040 boot mode.
- The flash script rebuilds, programs, and verifies the board.
- After reboot, the controller answers normal serial commands again.

---

## Expected Status Snapshots

These are reference patterns, not byte-for-byte golden outputs. Exact rates,
buffer position, and some event timing fields will vary by setup, but the named
state fields should match the phase you are testing.

### Idle, lane 1 selected, no motion

```text
OK:LN:1,TC:IDLE,L1T:IDLE,L2T:IDLE,...,SM:0,CU:0,RELOAD:0
```

What to check:

- `LN:1`
- `TC:IDLE`
- `L1T:IDLE` and `L2T:IDLE`
- `SM:0` unless sync was intentionally enabled
- `CU:0`

### Lane 1 preloaded at OUT

```text
OK:LN:1,TC:IDLE,L1T:IDLE,L2T:IDLE,I1:1,O1:1,I2:0,O2:0,...
```

What to check:

- Active lane is the lane you preloaded
- The matching `O*` field is asserted
- The other lane does not show a false OUT trigger

### Unload in progress on lane 1

```text
OK:LN:1,TC:IDLE,L1T:UNLOAD,L2T:IDLE,...
```

What to check:

- Only the active lane has an unload task
- `TC` remains `IDLE` for plain `UL:` or `UM:` operations

### Toolchange from lane 1 to lane 2

```text
OK:LN:1,TC:LOAD_WAIT_TH,L1T:IDLE,L2T:LOAD_FULL,...
```

What to check:

- `TC` is not `IDLE` during the swap sequence
- The old lane is no longer feeding once ownership moves to lane 2
- The target lane shows a load-related task while toolchange is active

### Sync running normally

```text
OK:LN:1,TC:IDLE,L1T:FEED,L2T:IDLE,...,SM:1,BUF:ADVANCE,SPS:...,BL:...,BP:...
```

What to check:

- `TC:IDLE`
- Active lane task is `FEED`
- `SM:1`
- `BUF`, `SPS`, `BL`, and `BP` fields are changing sensibly as the buffer moves

### RELOAD follow active

```text
OK:LN:2,TC:RELOAD_FOLLOW,L1T:IDLE,L2T:FEED,...,SM:1,RELOAD:1
```

What to check:

- The new lane is now active
- The old lane is idle
- `TC:RELOAD_FOLLOW`
- The new lane remains in `FEED` rather than falling back to `IDLE`
- `RELOAD:1` remains set until the sequence finishes or faults out

### Persistence blocked during motion

```text
ER:PERSIST_BUSY
```

What to check:

- `SV:`, `LD:`, and `RS:` are rejected while any motion or toolchange state is active
- The same commands succeed again once the controller returns to idle

---

## Suggested Regression Minimum

For small firmware changes, run at least this subset:

1. Build And Serial Smoke Test
2. Sensor Polarity And Idle State
3. Preload And Unload Per Lane on the affected lane
4. Toolchange or RELOAD test if the change touches motion ownership, sync, or recovery
5. Persistence Guarding if the change touches settings or protocol admission rules

For major refactors, run the full list.

---

## Record Keeping

When a test fails, capture:

- firmware commit SHA
- exact commands sent
- `?:` output before and after the failure
- emitted `EV:` lines around the failure
- whether the issue reproduces on both lanes or only one lane

That information is usually enough to correlate the failure with motion,
toolchange, sync, or protocol ownership.