# Task: SYNC_REFACTOR_PLAN — Phased Implementation

Implementing the approved sync refactor plan from SYNC_REFACTOR_PLAN.md.
Maintainer decisions D1–D7 are hard constraints (see SYNC_REFACTOR_PLAN.md §0).

## Phase 0 — Instrumentation & Observability (no behavior change)

### Goal
Add six new read-only status tail fields so the next phases are measurable.
Zero functional change to the control loop.

### Files to modify

#### firmware/src/sync.c
- Add `static uint32_t sync_advance_pin_since_ms = 0` — timer for advance dwell
- In `sync_on_transition()`: set timer when entering BUF_ADVANCE, clear when leaving
- In `sync_disable()`: clear the new timer
- Add public wrappers: `sync_reserve_target_mm()`, `sync_reserve_deadband_mm()`,
  `sync_advance_dwell_ms(uint32_t now_ms)`, `sync_est_age_ms(uint32_t now_ms)`

#### firmware/include/sync.h
- Declare: `sync_reserve_target_mm()`, `sync_reserve_deadband_mm()`,
  `sync_advance_dwell_ms(uint32_t)`, `sync_est_age_ms(uint32_t)`

#### firmware/src/protocol.c
- Increase `char b[350]` to `char b[512]` in `status_dump()`
- Increase `char line[384]` to `char line[512]` in `cmd_write_line()`
- Append tail fields after existing snprintf:
  `,RT:%.2f,RD:%.2f,AD:%u,TD:%u,TW:%u,EA:%u`
  Where:
  - RT = sync_reserve_target_mm() (signed mm, 2 decimal)
  - RD = sync_reserve_deadband_mm() (mm, 2 decimal)
  - AD = sync_advance_dwell_ms(now_ms) (ms, advance pin dwell)
  - TD = g_buf.state == BUF_TRAILING ? (now_ms - g_buf.entered_ms) : 0
  - TW = min(sync_trailing_wall_time_ms(A), 99999) as uint
  - EA = sync_est_age_ms(now_ms) (ms since last estimator update)

#### MANUAL.md
- Add new "Additional Diagnostic Status Fields" table after existing status section
- Document RT, RD, AD, TD, TW, EA

#### BEHAVIOR.md
- Add paragraph to "Velocity estimator" section referencing EA
- Add note to "Zone bias" section referencing RT, RD, AD, TD, TW

### Risks
- Status string fits in 512 bytes (estimated ~405 bytes with new fields)
- `cmd_write_line` line buffer must be large enough (same 512 upgrade)
- No settings changes, no version bump

### Validation
- `ninja -C build_local`
- STATUS returns all 6 new fields
- All existing fields still present in same positions

## Phase 0 Status: DONE — commit 7dda888

---

## Phase 1 — Control Logic Hardening

### Goal
1. Advance-dwell guard: auto-stop after sustained empty-side stall (6s default)
2. Refill assist: unconditional ramp toward SYNC_MAX after 400ms in advance
3. Stateless damp: remove read-side mutation from sync_is_positive_relaunch_damped()
4. Overshoot-MID extension: feature flag (default OFF per D6)

### New tunables
- SYNC_ADVANCE_DWELL_STOP_MS (default 6000ms, 0=disable)
- SYNC_ADVANCE_RAMP_DELAY_MS (default 400ms)
- SYNC_OVERSHOOT_MID_EXTEND (default 0)

### Files to modify
1. config.ini — add 3 new keys under # ─── Buffer Sync ─
2. config.ini.example — add 3 commented entries
3. scripts/gen_config.py — defaults + CONF_ macros
4. Run gen_config.py to regenerate tune.h
5. firmware/include/controller_shared.h — 3 extern decls
6. firmware/src/main.c — 3 global definitions
7. firmware/src/settings_store.c — bump 43u→44u, add fields, save/load/defaults
8. firmware/src/sync.c:
   - Remove sync_positive_relaunch_pending (static var, sync_disable clear)
   - Simplify sync_tick relaunch: just update sync_recent_negative_until_ms when RE < -deadband
   - Remove else-if clear path (timer expires naturally)
   - Add advance-dwell guard (hard stop + refill assist) after target_sps computation
   - Add overshoot_mid_extend gate around trailing overshoot_trim
   - Rewrite sync_is_positive_relaunch_damped() as stateless (no mutation)
9. firmware/src/protocol.c — SET/GET for SYNC_ADV_STOP_MS, SYNC_ADV_RAMP_MS, SYNC_OVERSHOOT_MID_EXT
10. MANUAL.md — document new tunables
11. BEHAVIOR.md — document advance-dwell guard + stateless damp

### Risks
- Settings version bump wipes user settings on first boot (expected, documented in commit)
- Stateless damp: 300ms early-release guard prevents flicker at window boundary
- Advance assist sets target_sps = SYNC_MAX_SPS when delayed; still gated by auto_start_allowed

## Phase 1 Status: DONE — commit b46c73d

---

## Phase 2 — Virtual Analog Buffer Abstraction

### Goal
Internal refactor: introduce buf_signal_t / buf_source_t so sync_tick() is
sensor-agnostic. Zero observable behavior change with BUF_SENSOR_TYPE=0.

### New files
- firmware/include/buf_signal.h — buf_signal_t, buf_source_t, buf_source_kind_t
- firmware/src/buf_source_endstop.c — wraps existing endstop logic
- firmware/src/buf_source_analog.c — wraps existing analog logic

### Changes
- sync.c: replace direct reads of BUF_SENSOR_TYPE/g_buf_pos/g_buf.state in
  control loop with buf_signal_t from active source's read(). Keep g_buf_pos
  and g_buf.state as exported shims populated each tick.
- protocol.c: append SK: and CF: tail fields
- main.c: source registry, select at boot
- firmware/CMakeLists.txt: add new .c files
- MANUAL.md, BEHAVIOR.md: document SK/CF and adapter architecture

### Risks
- sync.c is the most complex file; must validate bit-for-bit endstop parity
- toolchange.c must continue reading g_buf.state unmodified

## Phase 2 Status: DONE

---

## Phase 2.5 — Trailing-Side Centering Hardening + Confidence-Aware Mid Estimator

### Goal

Two coupled improvements, both default-OFF so endstop-mode parity (§4.5) is preserved:
1. Low-gain integral reserve centering with anti-windup (SYNC_RESERVE_INTEGRAL_GAIN = 0.0 default)
2. Physics-based sigma confidence model replacing Phase 2's wall-clock decay

### Findings (read from code)

- SETTINGS_VERSION is 44u. Phase 2.5 adds 4 persisted struct fields → bump to 45u.
- Phase 2 confidence: wall-clock linear decay in buf_sensor_tick(). Replace with physics-based sigma.
- sync_tick() uses buf_target_reserve_mm() → replace everywhere with effective_target.
- sync_apply_scaling() reads buf_target_reserve_mm() directly → pass effective_target as parameter.
- Status buffer is 512 bytes; current tail ~80 chars, Phase 2.5 adds ~35 more → fits.
- Sigma accumulation: track fabsf(net_delta) per tick in buf_virtual_position_tick() when in BUF_MID.
- sigma = sqrt(accum) * ENDSTOP_PER_UNIT_SIGMA_MM (0.025f).
- confidence = clamp(1.0 - sigma / EST_SIGMA_HARD_CAP_MM, 0, 1).
- Integral gate: s == BUF_MID && confidence >= 0.7 && gain > 0. Frozen otherwise (holds value).
- On zone transition (buf_update): sigma accum reset, fallback flag cleared.
- On sync_disable: integral and all sigma state reset.

### New tunables

| config.ini key                  | Default | Persistent? | SET/GET key      |
|---------------------------------|---------|-------------|------------------|
| sync_reserve_integral_gain      | 0.0     | YES         | SYNC_INT_GAIN    |
| sync_reserve_integral_clamp_mm  | 0.6     | YES         | SYNC_INT_CLAMP   |
| sync_reserve_integral_decay_ms  | 0       | YES         | SYNC_INT_DECAY_MS|
| est_sigma_hard_cap_mm           | 1.5     | YES         | EST_SIGMA_CAP    |
| est_low_cf_warn_threshold       | 0.5     | runtime-only | EST_LOW_CF_THR  |
| est_fallback_cf_threshold       | 0.2     | runtime-only | EST_FALLBACK_THR |

### New status fields: RI:, RC:, ES:, EC: (all additive tail per D7)
### New events: SYNC,ADV_DWELL_WARN / BUF,EST_LOW_CF / BUF,EST_FALLBACK

### Files to modify


1. [x] config.ini
2. [x] config.ini.example
3. [x] scripts/gen_config.py + regenerate tune.h
4. [x] firmware/include/controller_shared.h
5. [x] firmware/src/main.c
6. [x] firmware/src/settings_store.c (bump 44u→45u, 4 fields)
7. [x] firmware/include/sync.h (2 new declarations)
8. [x] firmware/src/sync.c (core changes)
9. [x] firmware/src/protocol.c (SET/GET + status tail)
10. [x] MANUAL.md
11. [x] BEHAVIOR.md

## Phase 2.5 Status: DONE

---

## Phase 2.6 — Drift Observer & ADVANCE-Risk Telemetry

### Goal
Residual drift observer: measure `g_buf_pos − switch_pos_mm` at `MID→ADVANCE/TRAILING`
transitions, accumulate EWMA (`BPD`), optionally apply as `bp_eff = g_buf_pos − clamp(BPD, ±BUF_DRIFT_CLAMP_MM)`.
Rolling ADVANCE-pin density window for ADV_RISK_HIGH warn-only event.
All behavior default-OFF (`BUF_DRIFT_THR_MM=0.0`). Settings version 45u → 46u.

### Files modified
1. [x] config.ini
2. [x] config.ini.example
3. [x] scripts/gen_config.py + regenerate tune.h
4. [x] firmware/include/controller_shared.h
5. [x] firmware/src/main.c
6. [x] firmware/src/settings_store.c (bump 45u→46u, 5 fields)
7. [x] firmware/include/sync.h (5 new accessor declarations)
8. [x] firmware/src/sync.c (drift observer, bp_eff correction, ADV_RISK block, resets)
9. [x] firmware/src/protocol.c (SET/GET + status tail BPR/BPD/BPN/APX/RDC)
10. [x] MANUAL.md
11. [x] BEHAVIOR.md
12. [x] SYNC_REFACTOR_PLAN.md

## Phase 2.6 Status: DONE

## Completed Steps
- Phase 0: commit 7dda888 — observability tail fields (RT/RD/AD/TD/TW/EA)
- Phase 1: commit b46c73d — advance-dwell guard, stateless damp, overshoot flag
- Phase 2: commit c39814e — buf_signal_t abstraction, SK/CF status fields, BUF_SENSOR idle guard
- Phase 2.6: commit 56ffde8 — drift observer, ADV_RISK telemetry, settings v46

---

## Active Investigation — Phase 2.6 drift correction still not relieving advance bias

### Findings
- User log shows `BUF_DRIFT_THR_MM:2.0` and intended `BUF_DRIFT_CLAMP:6.0`, but status has `RDC:0` for the whole capture. Correction never engaged.
- `BPN` only reaches 1 on the first ADVANCE hit and 2 on the second. Current code requires `BPN >= BUF_DRIFT_MIN_SMP` before any correction, and the supplied command sets `BUF_DRIFT_MIN_SMP:3`, so the controller asks the hardware to survive three bad ADVANCE excursions before reacting.
- `BPD` is already strongly non-zero after the first sample (`-7.80`) and grows to `-10.06` after the second ADVANCE sample. This is a severe enough residual that waiting for the full sample gate is causing the observed near-advance running/stall risk.
- `BUF_DRIFT_CLAMP` is silently clamped to `5.0` in both protocol SET and settings load, despite the operator command using `6.0` and the observed residual needing more than the original `2.0` clamp.
- `RE:` in status is still raw `g_buf_pos - RT`; it does not show the corrected controller-side reserve error when drift correction is active. `RDC` is the only current visible correction activity field.
- A default-off behavior invariant remains intact if changes only affect the path where `BUF_DRIFT_THR_MM > 0`.

### Plan

#### firmware/include/controller_shared.h
- Add a shared `BUF_DRIFT_CLAMP_LIMIT_MM` constant so protocol and settings use the same accepted maximum.
- Risk: header is broad; keep the macro local to drift tunables and avoid changing settings layout.

#### firmware/src/protocol.c + firmware/src/settings_store.c
- Raise the accepted `BUF_DRIFT_CLAMP` upper limit from `5.0` to `BUF_DRIFT_CLAMP_LIMIT_MM` so `SET:BUF_DRIFT_CLAMP:6.0` is honored.
- Risk: persistent values above the old max load after flashing; bounded by the new shared limit and still default-off unless `BUF_DRIFT_THR_MM > 0`.

#### firmware/src/sync.c
- Change drift apply from an all-or-nothing sample gate to a ramp-in gate: once correction is explicitly enabled and at least one residual sample exists, apply a sample-confidence fraction up to full strength at `BUF_DRIFT_MIN_SMP`.
- Keep correction clamped at endstop thresholds as in commit `487ed3c`.
- Risk: correction starts earlier on explicit enable; default-off parity is preserved, and the fraction limits early action when the sample count is below the configured full-confidence gate.

#### MANUAL.md + BEHAVIOR.md + SYNC_REFACTOR_PLAN.md
- Document that `BUF_DRIFT_MIN_SMP` is now the full-strength sample count and that correction ramps in before that when explicitly enabled.
- Document the wider clamp range and the operator-facing implication of `RDC`.

### Completed
- Implemented ramp-in drift apply: explicit drift correction now starts after the first ADVANCE residual sample and reaches full strength at `BUF_DRIFT_MIN_SMP`.
- Raised `BUF_DRIFT_CLAMP` runtime/settings load limit to 8.0 mm so the operator's 6.0 mm clamp is honored.
- Updated `MANUAL.md`, `BEHAVIOR.md`, `SYNC_REFACTOR_PLAN.md`, `SYNC_REFACTOR_PHASE_2_6.md`, `config.ini`, and `config.ini.example` to match the new correction semantics.
- Validation: `ninja -C build_local` passed.

---

## Active Investigation — Post d51ec64 continuous-feed log

### Findings
- Ramp-in behavior is working as designed: user log shows `RDC:33` at `BPN:1`, then `RDC:66` at `BPN:2`.
- ADVANCE dwell improved: second ADVANCE event stayed around `AD:517` before returning to MID, instead of sitting near advance indefinitely.
- Remaining failure shifted to the trailing side. With `BPD:-8.93`, `BUF_DRIFT_CLAMP:6`, and `BPN:2/3`, applied correction is about `-4 mm`, so the control law sees `bp_eff = raw BP + 4 mm`.
- When raw `BP` is physically at the trailing boundary (`-7.80`), correction makes the controller see about `-3.8`, near the reserve target. This masks the trailing wall, keeps sync speed high, and eventually triggers trailing `AUTO_STOP`.
- Existing endstop clamp only prevents correction from pushing `bp_eff` past a boundary; it does not prevent correction from hiding the opposite boundary.

### Plan

#### firmware/src/sync.c
- Add a sign-aware wall taper after drift correction is computed:
  - Negative drift correction shifts effective position toward ADVANCE; fade it to zero as raw `BP` approaches the TRAILING endstop.
  - Positive drift correction shifts effective position toward TRAILING; fade it to zero as raw `BP` approaches the ADVANCE endstop.
- Use the existing reserve deadband as the taper width so correction remains active through the useful mid-zone but cannot mask a physical wall.
- Risk: correction becomes less aggressive at the exact opposite wall; this is intended because physical switch state must dominate there.

#### BEHAVIOR.md + MANUAL.md
- Document that correction is tapered near the opposite endstop and `RDC` may drop below the sample ramp value when wall protection is active.

### Completed
- Implemented sign-aware wall taper in `firmware/src/sync.c`: negative drift correction fades out near the trailing wall; positive drift correction fades out near the advance wall.
- Updated `MANUAL.md` and `BEHAVIOR.md` so `RDC` is documented as the final applied correction scalar after sample ramp, clamp, confidence gate, and wall taper.
- Validation: `ninja -C build_local` passed.

---

## Active Task — Provisional Real-Print Sync Defaults

### Findings
- Latest real-print logs show no skipping, with sync still close to ADVANCE on refill but usable. The tested stable shape keeps `SYNC_RESERVE_PCT=40` and `SYNC_INT_GAIN=0.0`.
- Current repository defaults already match those two values in `config.ini`, `config.ini.example`, `scripts/gen_config.py`, and generated `tune.h`.
- Drift correction remains default-off in active config/docs (`BUF_DRIFT_THR_MM=0.0`, `BUF_DRIFT_CLAMP=2.0`), while the print tests used enabled drift correction. Runtime clamp support is already raised to 8.0 mm.
- No settings schema change is needed because the existing tunables already persist; this task only changes defaults/docs/generated header.

### Plan

#### config.ini + config.ini.example
- Make drift correction a provisional enabled default with `buf_drift_apply_thr_mm: 2.0`.
- Use a conservative `buf_drift_clamp_mm: 3.0` default to pull the controller away from ADVANCE compared with the tested 4.0 mm clamp.
- Risk: existing controllers with persisted flash settings will keep their saved values until reset or explicit `SET`/`SAVE`.

#### firmware/include/tune.h
- Regenerate from `scripts/gen_config.py` so firmware defaults match `config.ini`.

#### MANUAL.md + BEHAVIOR.md
- Update the runtime parameter table and behavior reference to describe the provisional enabled drift default and the `0.0` off switch.
- Keep the original Phase 2.6 plan docs as historical design notes unless they claim current defaults.

### Validation
- Run `ninja -C build_local` before commit.

### Completed
- Updated `config.ini`, `config.ini.example`, `scripts/gen_config.py`, and generated `firmware/include/tune.h` so provisional defaults are `BUF_DRIFT_THR_MM=2.0` and `BUF_DRIFT_CLAMP=3.0`.
- Updated `MANUAL.md`, `BEHAVIOR.md`, and `SYNC_REFACTOR_PLAN.md` to describe the enabled-but-thresholded drift correction default and the `0.0` disable path.
- Validation: `python3 -m py_compile scripts/*.py` passed.
- Validation: `ninja -C build_local` passed.

---

## Active Task — Align Defaults To Live Settings Dump

### Findings
- User supplied a live `scripts/nosf_cmd.py --dump` from the working printer.
- Major differences from repo defaults: motor current 0.98 A, gear ratio about 2.941, SpreadCycle/StealthChop threshold 500, chopper `toff/hstrt/hend = 4/5/3`, feed/rev/auto 3000, global/sync max 4000, `pre_ramp_rate=90`, `buf_half_travel_mm=7.8`, `buf_size_mm=22`, `sync_reserve_pct=35`, `reload_mode=1`, and `reload_join_delay_ms=10000`.
- The dump does not include the new Phase 2.6 drift tunables, so keep the just-committed provisional drift defaults (`BUF_DRIFT_THR_MM=2.0`, `BUF_DRIFT_CLAMP=3.0`) unless a later live dump includes those fields.
- `autoload_retract_mm` and `enable_cutter` are live runtime settings but still hardcoded startup defaults (`10` and `false`) rather than `gen_config.py` defaults; those already match the dump.

### Plan

#### config.ini + config.ini.example
- Align tracked defaults and example comments with the live dump for all supported compile-time config keys.
- Add `reload_join_delay_ms: 10000` to tracked `config.ini` so generated `tune.h` does not fall back to the old 500 ms default.

#### scripts/gen_config.py + firmware/include/tune.h
- Update fallback defaults to match the live dump where the key is supported by the generator.
- Regenerate `firmware/include/tune.h`.

#### MANUAL.md
- Update documented defaults for physical model, speed ceilings, reserve percentage, RELOAD mode, join delay, and StealthChop threshold.

### Validation
- Run `python3 -m py_compile scripts/*.py`.
- Run `ninja -C build_local`.

### Completed
- Restored blank `config.ini.example` placeholders for mandatory `microsteps`, `rotation_distance`, and `run_current`.
- Left the remaining live-dump defaults unchanged.
- Validation: `python3 -m py_compile scripts/*.py` passed.
- Validation: `ninja -C build_local` passed.

### Completed
- Updated `config.ini`, `config.ini.example`, and `scripts/gen_config.py` defaults to match the supplied live settings dump where generator-supported.
- Regenerated ignored `firmware/include/tune.h` locally from the aligned `config.ini`.
- Updated `MANUAL.md` documented defaults for buffer geometry, speeds, reserve percentage, RELOAD mode/join delay, and StealthChop threshold.
- Kept Phase 2.6 drift defaults at the existing provisional values because the live dump does not include those newer fields.
- Validation: `python3 -m py_compile scripts/*.py` passed.
- Validation: `ninja -C build_local` passed.

---

## Active Task — Keep Mandatory Motor Calibration Placeholders

### Findings
- User clarified `microsteps`, `rotation_distance`, and `run_current` should remain untouched.
- These are mandatory config inputs in `scripts/gen_config.py`; before the live-dump defaults update, `config.ini.example` intentionally left them blank.
- `scripts/gen_config.py` fallback behavior for these keys was not changed by the live-dump commit.

### Plan

#### config.ini.example
- Restore blank placeholders for `microsteps`, `rotation_distance`, and `run_current`.
- Leave all non-mandatory live-dump defaults intact.

### Validation
- Run `python3 -m py_compile scripts/*.py`.
- Run `ninja -C build_local`.

## Phase 2.6.x — Stability Refinements (Slow Speed & Drift)

### Goal
Address estimator circular stalls and improve tracking bias during open-loop drift.

### Changes
1. **Estimator Stall Escape:** Use raw model position (ignoring drift correction) to detect stalls. Bleed EST toward MMU rate + margin when model is pinned but physical is in MID.
2. **Confidence Bias (Ramping Bias):** Shift effective position toward ADVANCE side when confidence < 1.0. Creates gentle feed pressure during uncertainty.
3. **Uncertainty Speed Probe:** Direct speed boost to target_sps proportional to uncertainty (~150mm/min max).
4. **Adaptive Bootstrap Floor:** Use baseline_sps / 2 as the floor when hitting ADVANCE switch.
5. **Drift Safety Guard:** Clamp bp_eff strictly by physical switch state.

## Phase 2.6.x Status: DONE
