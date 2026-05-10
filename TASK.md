# Task: SYNC_REFACTOR_PLAN — Phased Implementation

Implementing the approved sync refactor plan from SYNC_REFACTOR_PLAN.md.
Maintainer decisions D1–D7 are hard constraints (see SYNC_REFACTOR_PLAN.md §0).

... (previous content) ...

## Phase 2.7 — Implementation + Baseline Tuning

### Goal
Implement trailing-bias setpoint shift, mid-zone creep, and variance-aware position blend.
Restore telemetry pipeline for offline analysis and auto-tuning.
All behavior default-OFF or legacy-equivalent until operator-tuned.
Settings version 46u → 47u.

### Sub-phases completed
- **2.7.0 Trailing-Bias Setpoint Shift:** Added `sync_trailing_bias_frac`. Updated `buf_target_reserve_mm()` and integrated gate logic.
- **2.7.1 Mid-Zone Creep:** Added `mid_creep_timeout_ms`, `mid_creep_rate_sps_per_s`, `mid_creep_cap_frac`. Active wall-seek during MID dwells.
- **2.7.2 Variance-Aware Position Blend:** Added `buf_variance_blend_frac`, `buf_variance_blend_ref_mm`. Bayesian pull toward setpoint on full distrust.
- **2.7.3a MARK: Command:** Added `MARK:<tag>` and `MK:seq:tag` status for telemetry correlation.
- **2.7.3b G-code Marker:** Restored `scripts/gcode_marker.py` with `--every-layer` flag.
- **2.7.3c Logger:** Added `scripts/nosf_logger.py` for high-speed CSV capture.
- **2.7.4 Analyzer:** Added `scripts/nosf_analyze.py` for offline auto-tuning.
- **2.7.5 PID:** Deferred (documented skip in `SYNC_REFACTOR_PLAN.md`).

### Files modified
- `config.ini`, `config.ini.example`
- `scripts/gen_config.py`, `scripts/gcode_marker.py` (new), `scripts/nosf_logger.py` (new), `scripts/nosf_analyze.py` (new)
- `firmware/include/controller_shared.h`, `firmware/include/tune.h` (generated)
- `firmware/src/main.c`, `firmware/src/sync.c`, `firmware/src/protocol.c`, `firmware/src/settings_store.c`
- `MANUAL.md`, `BEHAVIOR.md`, `KLIPPER.md`, `README.md`, `CONTEXT.md`, `SYNC_REFACTOR_PLAN.md`

### Final baseline tunables (converged)
- `sync_trailing_bias_frac: 0.4`
- `mid_creep_timeout_ms: 4000`
- `mid_creep_rate_sps_per_s: 5`
- `mid_creep_cap_frac: 10`
- `buf_variance_blend_frac: 0.5`
- `buf_variance_blend_ref_mm: 1.0`

## Phase 2.7 Status: DONE — completed integration of all sub-phases.

---

## Phase 2.8 — Live Tuning (Closed-Loop Online Bucket Learning)

### Findings
- Read `AGENTS.md`: must post banner, write findings/plan before edits, build before firmware commits, py_compile before script commits, commit and push each milestone with `Generated-By: GPT-5.4 (High)`.
- Read `TASK.md`: Phase 2.7 completed; telemetry pipeline exists.
- Read `SYNC_REFACTOR_PHASE_2_8.md`: source of truth. Phase 2.8 adds host-side live tuner and exactly one firmware delta, `SET:LIVE_TUNE_LOCK:<0|1>`. No `settings_t` change, no status field, no settings version bump.
- Read `SYNC_REFACTOR_PLAN.md` Phase 2.7 refs and current code: `MARK:` is implemented and status includes tail field `MK:<seq>:<tag>`, so Phase 2.8 dependency is present.
- Read `firmware/src/protocol.c`: `SET:` uses parsed `base_param`; current live-tuned fields are `BASELINE_RATE`, `TRAIL_BIAS_FRAC`, `MID_CREEP_TIMEOUT_MS`, `MID_CREEP_RATE`, `MID_CREEP_CAP`, `VAR_BLEND_FRAC`, and `VAR_BLEND_REF_MM`. `GET:` mirrors these with string replies.
- Read `scripts/nosf_cmd.py` and `scripts/nosf_logger.py`: scripts use `pyserial`, direct `ser.write`, status polling via `?:`, and regex parsing for `MK:`/`NOSF_TUNE`.
- Read `CONTEXT.md`: runtime parameter checklist applies, but this lock is intentionally not persisted; `SETTINGS_VERSION` remains 47.
- Regression impact: firmware lock only blocks selected tuning writes while enabled; load/unload/toolchange/RELOAD/sync status are otherwise untouched. Host tuner must avoid SAVE during print, freeze on risk events, and rate-limit writes.

### Plan

#### 2.8.0 — `firmware/src/protocol.c` + `MANUAL.md`
- Add file-static `g_live_tune_lock`, `SET:LIVE_TUNE_LOCK:<0|1>`, `GET:LIVE_TUNE_LOCK`, and lock guards for baseline/bias/mid-creep/variance blend SET handlers.
- Document lock behavior under `MANUAL.md` SET/GET parameters.
- Risk: command names in prompt use newer aliases (`BASELINE_SPS`, `*_SPS_PER_S`, `BUF_VARIANCE_*`) while current firmware uses `BASELINE_RATE`, `MID_CREEP_RATE`, `MID_CREEP_CAP`, `VAR_BLEND_*`; implement current code aliases and compatible prompt aliases where safe.
- Validate with `ninja -C build_local`, commit, push.

#### 2.8.1 — `scripts/nosf_live_tuner.py`
- Add live tuner script with daemon reader thread, queue dispatch, Kalman bucket update, SET writer with serial reconnect handling, and immediate utility modes.
- Include module docstring with usage examples and reconnect behavior.
- Validate with `python3 -m py_compile scripts/nosf_live_tuner.py`, commit, push.

#### 2.8.2 — `scripts/nosf_live_tuner.py`
- Add state warm-start, atomic persistence, sidecar PID lock, `--state-info`, schema mismatch error, and `--unlock` persistence edits.
- Validate with `python3 -m py_compile scripts/nosf_live_tuner.py`, commit, push.

#### 2.8.3 — `scripts/nosf_live_tuner.py` + `scripts/test_nosf_live_tuner.py` + optional `scripts/validate_regression.sh`
- Refine safety interlocks, rollback, halt, freeze, and rolling rate limit.
- Add stdlib fake-serial regression fixture covering warm-up, locked warm-start, ADV_RISK rollback, ADV_DWELL halt, and rate limiting.
- Wire into regression script if present.
- Validate with `python3 -m py_compile scripts/*.py` and `python3 scripts/test_nosf_live_tuner.py`, commit, push.

#### 2.8.4 — `scripts/nosf_live_tuner.py`
- Add recency-weighted patch emission and `--commit-on-idle` flow: unlock firmware, send `SV:`, emit `/tmp/nosf-patch.ini`, log path, exit.
- Validate with py_compile and tuner self-test, commit, push.

#### 2.8.5 — `MANUAL.md`, `KLIPPER.md`, `README.md`, `CONTEXT.md`
- Expand docs for live tuning, Klipper invocation, logger conflict, tools summary, and context note.
- Validate docs plus full final gate, commit, push.

### Completed Steps
- Preflight read done; Phase 2.7 dependency present.
- 2.8.0 done: added `LIVE_TUNE_LOCK` protocol guard and MANUAL row; build passed; committed and pushed `a4fa8fd`.
- 2.8.1 done: added `scripts/nosf_live_tuner.py` reader/Kalman/SET loop, utility modes, and reconnect docs; py_compile passed; committed and pushed `ac4bfe2`.
- 2.8.2 done: added state lock, `--state-info`, schema checks, wall-clock timestamps; py_compile passed; committed and pushed `62641d9`.
- 2.8.3 done: added stdlib live tuner self-test, regression script hook, pyserial-free import path, and status prefix parsing fix; py_compile/self-test passed; committed and pushed `b8c1dca`.
- 2.8.4 done: added recency-weighted patch emission and `--commit-on-idle` flow; py_compile/self-test passed; committed and pushed `a151dd9`.
- 2.8.5 done: documented live tuner flow across MANUAL/KLIPPER/README/CONTEXT; diff check passed; committed and pushed `c52eb9f`.
- Final verification passed: `ninja -C build_local`, `python3 -m py_compile scripts/*.py`, and `python3 scripts/test_nosf_live_tuner.py`.
- Phase 2.9 follow-up: Phase 2.8 live writes are deprecated as the default workflow; observe-only calibration is canonical and live writes are explicit debug modes.

---

## AGENTS.md Git Tool Priority Update

### Findings
- Read `AGENTS.md`: current Rule 11 forbids Git MCP for add/commit/push and commit-format rules repeat the shell-only instruction.
- User requested replacing that hard ban with Git MCP-first behavior when applicable, falling back to non-interactive shell git.
- Docs-only change: no firmware build or Python validation required.

### Plan
- Update Non-Negotiable Rule 11 to prefer Git MCP for supported git operations and fall back to shell git when MCP is unavailable, insufficient, fails, or push/remotes require shell git.
- Update Commit Format rules to allow Git MCP first for add/commit and keep shell git as default for push unless a reliable push-capable MCP exists.
- Validate with `rg -n "Git MCP|shell git|git push|commit helpers" AGENTS.md` and confirm no contradictory wording remains.

---

## Phase 2.9 — Calibration Workflow

### Findings
- Read `AGENTS.md`: post banner, read `TASK.md`, write findings/plan before code, commit/push every unit, validate script edits with `python3 -m py_compile scripts/*.py`, and use exact `Generated-By: GPT-5.4 (High)` footer for this phase.
- Read `TASK.md`: Phase 2.8 is marked DONE; final verification passed. Phase 2.8 introduced the live tuner, state file, patch emission, marker-file bridge, debug progress, and `LIVE_TUNE_LOCK`.
- Read `SYNC_REFACTOR_PHASE_2_9.md`: source of truth. Phase 2.9 changes host calibration workflow only; no new firmware features, no `settings_t` change, no `SETTINGS_VERSION` bump, and 2.9.7 firmware cleanup is deferred.
- Read `scripts/nosf_live_tuner.py`: current default still allows live `TRAIL_BIAS_FRAC` writes, `--allow-baseline-writes` is opt-in, schema is 1, lock criteria still use `BUCKET_LOCK_S`, and commit-on-idle/finish currently sends `SV:`.
- Read `scripts/test_nosf_live_tuner.py`: stdlib fake-serial fixture already covers warm-up, locked warm-start, risk rollback, dwell halt, rate limiting, baseline-off, low-flow skips, rail guard, debug logs, idle arming, and MK fallback.
- Read `scripts/gcode_marker.py`: compact marker path emits `NT:START`, `NT:<feature>:V<vfil>`, `FINISH`; `--every-layer` currently emits `NOSF_TUNE:LAYER:<n>:0:0`, which compacts as a normal feature marker, so Phase 2.9 layer handling must consume the existing emitted form without changing in-place behavior.
- Read `scripts/nosf_analyze.py`: analyzer is minimal, uses 5 mm3/s bins, only baseline and bias delta, no state JSON input, no seven-tunable review block, and no acceptance gate.
- Read `scripts/nosf_logger.py`: CSV fields include `zone`, `bp_mm`, `sigma_mm`, `est_sps`, `rt_mm`, `cf`, `adv_dwell_ms`, `tb`, `mc`, `vb`, `bpv_mm`, marker fields, feature, and `v_fil`; event counts are not present in CSV rows.
- Read `scripts/nosf_marker.py`: marker-file bridge appends timestamp + tag and does not own the NOSF serial port.
- Read `firmware/src/protocol.c` SET/GET section: `LIVE_TUNE_LOCK` remains present; Phase 2.9 must not touch firmware because 2.9.7 is skipped.
- Read `config.ini`: canonical default keys for the seven tunables are `baseline_rate`, `sync_trailing_bias_frac`, `mid_creep_timeout_ms`, `mid_creep_rate_sps_per_s`, `mid_creep_cap_frac`, `buf_variance_blend_frac`, and `buf_variance_blend_ref_mm`.
- Read `MANUAL.md`, `KLIPPER.md`, `README.md`, and `CONTEXT.md`: docs still describe Phase 2.8 live writes and `SV:` commit behavior as the normal path; Phase 2.9 must document observe-only calibration as canonical.
- Regression constraints: preserve `--commit-on-idle` and `--commit-on-finish` patch emission, preserve `--allow-baseline-writes`, add `--allow-bias-writes`, keep patch files review-only, keep `gcode_marker.py` in-place mode unchanged, and use only stdlib plus pyserial.

### Plan

#### 2.9.0 — `scripts/nosf_live_tuner.py` + `scripts/test_nosf_live_tuner.py`
- Add observe-only default by introducing `--allow-bias-writes` and gating `SET:TRAIL_BIAS_FRAC` behind it.
- Add `--commit-flash`, imply both live-write flags, skip `SET:LIVE_TUNE_LOCK:1` in pure observe mode, and gate `SV:` behind `--commit-flash`.
- Keep commit-on-idle/finish patch emission working in observe mode.
- Add tuner tests for default zero writes, explicit bias writes, and commit-flash `SV:`.
- Validate with py_compile and tuner self-test.

#### 2.9.1 — `scripts/nosf_live_tuner.py` + `scripts/test_nosf_live_tuner.py`
- Bump state schema to 2 and extend bucket serialization with cumulative counters and run metadata.
- Auto-migrate schema 1 state by preserving learned values and zeroing new counters; refuse future schemas.
- Increment low-flow, rail, rollback, and MID-time counters in the existing paths.
- Add schema migration and counter increment tests.
- Validate with py_compile and tuner self-test.

#### 2.9.2 — `scripts/nosf_live_tuner.py` + `scripts/test_nosf_live_tuner.py`
- Remove `BUCKET_LOCK_S`; add cumulative samples/runs/layers/MID-time criteria.
- Track `NT:START` run boundaries and layer markers, incrementing counters for active buckets.
- Rewrite wait reasons and locking to reflect cumulative criteria.
- Add short-print/no-lock, three-run lock, and layer-gate tests.
- Validate with py_compile and tuner self-test.

#### 2.9.3 — `scripts/nosf_live_tuner.py`
- Upgrade `--state-info` output with runs, layers, mid seconds, last-seen age, and wait reason.
- Add `--state-info --csv` for machine-readable state summaries.
- Validate with py_compile and tuner self-test.

#### 2.9.4 — `scripts/nosf_analyze.py` + `scripts/test_nosf_analyze.py`
- Align analyzer bins to 25 mm3/s and add optional state JSON input.
- Compute all seven tunables using the spec telemetry mapping and confidence labels.
- Add acceptance gate with explicit stderr reasons, fail exit code, and patch still written with FAIL header.
- Add stdlib analyzer tests for baseline cluster, bias clamp, gate fail/pass, and bin alignment.
- Validate with py_compile and analyzer self-test.

#### 2.9.5 — `scripts/nosf_live_tuner.py` + `scripts/nosf_analyze.py`
- Make both patch emitters use the same review-only `[nosf_review]` commented format.
- Remove raw `sync_trailing_bias_frac: <value>` output from tuner patches.
- Add warning header; never overwrite `config.ini`.
- Validate py_compile, tuner self-test, analyzer self-test, and sample patch emission.

#### 2.9.6 — `MANUAL.md`, `KLIPPER.md`, `README.md`, `CONTEXT.md`, `TASK.md`
- Document canonical calibration workflow: calibrate, analyze, review, flash, detach host.
- Document observe, bias-write debug, baseline-write debug, and commit-flash modes.
- Mark `LIVE_TUNE_LOCK` debug-only for explicit live-write modes.
- Amend Phase 2.8 status to note live writes are deprecated as default; Phase 2.9 observe-only is canonical.
- Validate docs with py_compile/self-tests as applicable.

#### 2.9.7 — deferred
- Skip firmware cleanup and leave `LIVE_TUNE_LOCK` in `protocol.c`.
- Rationale: removal needs at least five real-print observe-only soaks before shrinking firmware compatibility surface.

### Completed Steps
- Preflight read done; Phase 2.8 DONE confirmed; 2.9.7 marked deferred before implementation.
- 2.9.0 done: observe-only default, `--allow-bias-writes`, `--commit-flash`, and tuner tests added; py_compile and tuner self-test passed; committed and pushed `f7ab2ba`.
- 2.9.1 done: schema 2 bucket counters, schema 1 migration, and counter tests added; py_compile and tuner self-test passed; committed and pushed `db35cec`.
- 2.9.2 done: cumulative lock criteria, run/layer counters, layer marker support, and lock-gate tests added; py_compile and tuner self-test passed; committed and pushed `ae6745e`.
- 2.9.3 done: `--state-info` now reports runs/layers/MID time/age/wait and supports `--csv`; py_compile and tuner self-test passed; committed and pushed `1d954cc`.
- 2.9.4 done: analyzer uses 25-bin parity, computes all seven review tunables, supports state JSON and acceptance gate, and has stdlib analyzer tests; py_compile/analyzer self-test passed; committed and pushed `b428e2c`.
- 2.9.5 done: tuner patch emission now uses review-only `[nosf_review]` format with warning header and no raw config assignment lines; py_compile, tuner/analyzer self-tests, and sample patch emission passed; committed and pushed `4e9db9f`.
- 2.9.6 done: documented observe-only calibration workflow across MANUAL/KLIPPER/README/CONTEXT and noted Phase 2.8 live-write deprecation; docs-only diff checked. Commit SHA reported after push.
- 2.9.7 deferred: `LIVE_TUNE_LOCK` remains in firmware until at least five real-print observe-only soaks validate removal.

---

## Orca Layer Marker Fix

### Findings
- Interactive calibration showed every bucket had `layers_seen=0`, so Phase 2.9 lock criteria could not pass even after samples/runs/MID time were sufficient.
- User's OrcaSlicer G-code uses `;LAYER_CHANGE`, `;Z:<height>`, and `;HEIGHT:<height>` instead of `;LAYER:<n>`.
- Current `scripts/gcode_marker.py --every-layer` only recognizes `;LAYER:<n>`, so layer markers were never injected for Orca prints.

### Plan
- Update `scripts/gcode_marker.py` to count Orca `;LAYER_CHANGE` lines and emit `NT:LAYER:<index>` while preserving existing `;LAYER:<n>` support and in-place mode.
- Update docs to mention Orca `;LAYER_CHANGE` support for calibration G-code.
- Validate with `python3 -m py_compile scripts/*.py` and a sample marker injection grep.

### Completed Steps
- Implemented Orca `;LAYER_CHANGE` support and docs; validation/commit pending.
- Done: committed and pushed `bbff987`.

---

## Layer Credit Fix

### Findings
- Interactive run 2 showed `NT:LAYER` markers arrive and some buckets lock, but many high-sample buckets remain blocked at `layers 0/3`.
- Current tuner increments `layers_seen` only for the bucket active at the exact layer-boundary marker. For Orca prints, layer marker arrives before the per-feature markers for the new layer, so most useful buckets never receive layer credit.
- Better calibration semantics: remember current layer from `NT:LAYER:<n>` and credit a bucket the first time it receives a valid MID sample on that layer.

### Plan
- Update `scripts/nosf_live_tuner.py` to track `current_layer` and increment `layers_seen` on first valid MID sample per `(run, bucket, layer)`.
- Keep direct layer-boundary credit as harmless fallback for already-active buckets.
- Update `scripts/test_nosf_live_tuner.py` to assert sample-on-layer increments layer counters.
- Validate with `python3 -m py_compile scripts/*.py` and `python3 scripts/test_nosf_live_tuner.py`.

### Completed Steps
- Implemented sample-based layer credit; validation passed; commit pending.
- Done: committed and pushed `4ef19e7`.

---

## Marker File Startup Reset

### Findings
- Interactive run can miss `NT:START` if `/tmp/nosf-markers-<machine>.log` is reused and operator starts tuner after stale marker state exists.
- Manual `rm -f` before every run is easy to forget and makes calibration startup fragile.
- Blind reset is correct for normal calibration-from-start workflow, but attach-mid-print still needs an opt-out.

### Plan
- Update `scripts/nosf_live_tuner.py` to truncate `--marker-file` by default before tailing it.
- Add `--keep-marker-file` for attach-mid-print/debug sessions where existing marker file content must not be cleared.
- Update MANUAL/KLIPPER marker-file docs for the new startup reset behavior.
- Validate with `python3 -m py_compile scripts/*.py` and `python3 scripts/test_nosf_live_tuner.py`.

### Completed Steps
- Implemented marker-file startup reset and attach opt-out; validation passed; commit pending.

---

## Phase 2.9 Continuation — milestones 2.9.16 → 2.9.13

### Findings
- Current milestones 2.9.0-2.9.6 are DONE.
- `scripts/nosf_live_tuner.py` currently has `--commit-flash` argument and emits `SV:`.
- `scripts/gcode_marker.py` uses `--every-layer` as opt-in.
- Analyzer lacks 2.9.14 embedded logger `--csv-out` format consumption (already compatible).
- Schema migration in `nosf_live_tuner.py` is hard-coded to 1->2. Needs a generic chained implementation.
- Dual-path lock, recommend-recheck, stale bucket handling, and observe daemon need implementation.

### Plan
- **2.9.16:** Remove `--commit-flash` and `SV:` code path from `nosf_live_tuner.py` and its tests.
- **2.9.15:** Change `gcode_marker.py` default layer markers to ON. Add `--no-layer-markers` and deprecate `--every-layer`.
- **2.9.14:** Add `--csv-out` to `nosf_live_tuner.py` for embedded logger CSV emission. Update tests.
- **2.9.8:** Implement dual-path lock in `nosf_live_tuner.py` and print-duration gate (`MIN_PRINT_MID_S`). Add tests.
- **2.9.9:** Implement schema 2->3 migration with `_meta` watermark, rewriting `migrate_state_data` per §17.1.6. Update analyzer with `--commit-watermark`. Add tests.
- **2.9.10:** Implement `--recommend-recheck` in `nosf_live_tuner.py`. Add tests.
- **2.9.11:** Implement stale bucket pruning (`--prune-stale`) and exclusion. Add tests.
- **2.9.12:** Add `--observe-daemon` mode to `nosf_live_tuner.py`. Add tests.
- **2.9.13:** Update documentation (`MANUAL.md`, `KLIPPER.md`, `README.md`) and deprecate `nosf_logger.py`.

### Completed Steps
- Phase 2.9 Continuation pre-work: read specs and updated TASK.md.
- 2.9.16 done: removed --commit-flash and SV: code path; committed and pushed 328619c.
- 2.9.15 done: made layer markers default in gcode_marker.py; committed and pushed b98af3a.
- 2.9.14 done: added embedded logger CSV emission; committed and pushed d35f036.
- 2.9.8 done: implemented dual-path lock criteria; committed and pushed 1bda999.
