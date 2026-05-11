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
- 2.9.9 done: implemented schema 3 and watermark; committed and pushed 7ca94a0.
- 2.9.10 done: implemented recommend-recheck mode; committed and pushed 3b2e6d8.
- 2.9.11 done: implemented stale bucket handling and prune utility; committed and pushed b881c6b.
- 2.9.12 done: implemented observe-daemon mode; committed and pushed eb30a04.
- 2.9.13 done: updated documentation and deprecated nosf_logger.py; committed and pushed b387fab.
- bugfix: restored missing --include-stale flag; committed and pushed d6b089a.

---

## Phase 2.10 — Klipper Motion Tracking

### Findings
- Read `AGENTS.md`: session banner posted; commit/push required after each milestone; script changes require Python validation; shell git must be used for add/commit/push for this task; do not commit `.agents/`, `.claude/`, or `skills-lock.json`.
- Read `TASK.md`: Phase 2.9 base milestones and continuation are DONE through 2.9.13 plus the `--include-stale` bugfix; Phase 2.10.0 plan exists in `SYNC_REFACTOR_PHASE_2_10.md` and was committed as `4fc11c9`.
- Read `SYNC_REFACTOR_PHASE_2_10.md` sections 0-16: Phase 2.10 is host-only, replaces `RUN_SHELL_COMMAND` markers with a sidecar plus direct Klipper UDS `objects/subscribe`, and must feed byte-identical strings through existing `tuner.on_m118`.
- Read `SYNC_REFACTOR_PHASE_2_9.md` sections 15-17: preserve observe-only default, schema 1->2->3 chained migration, Path A/Path B lock criteria including `total_print_mid_s`, per-tunable watermark `_meta`, read-only `--recommend-recheck`, operator-only `--prune-stale`, and daemon reminder-only behavior.
- Read `scripts/gcode_marker.py`: current default is `--emit m118`, layer markers are already on by default with `--no-layer-markers`; `--emit file` still creates `RUN_SHELL_COMMAND CMD=nosf_marker`; in-place postprocess uses temp file + `os.replace`.
- Read `scripts/nosf_live_tuner.py`: state schema is 3, `on_m118` is the only marker ingress, `run_loop` currently pumps serial, optional Klipper log, and optional marker file; marker file is truncated by default unless `--keep-marker-file`; default observe mode sends no `SET:` or `SV:`.
- Read `scripts/test_nosf_live_tuner.py`: pytest-free stdlib tests cover observe-only, schema migration, Path B locks, layer credit, CSV output, daemon behavior, stale pruning, and no-SV patch emission. These must keep passing after every relevant milestone.
- Read `scripts/nosf_analyze.py`: analyzer is channel-agnostic, consumes CSV/state, writes review-only patches, and currently loads state schemas 1/2 only in `load_state`; Phase 2.10 should not modify analyzer unless a doc/test issue forces it.
- Read `scripts/nosf_marker.py`: marker-file bridge appends timestamped tags; kept for fallback until 2.10.6, then warned deprecated.
- Read `scripts/nosf_logger.py`: already prints deprecation warning; Phase 2.10 must not touch it because logger deprecation landed separately in Phase 2.9.
- Read `MANUAL.md`, `KLIPPER.md`, `README.md`, and `CONTEXT.md`: docs still present shell-marker calibration as the primary path; 2.10.5 must flip primary docs to sidecar + UDS while keeping shell-marker fallback/legacy notes.
- Existing dirty local files are unrelated: `.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md`. Do not stage them.
- Tests to keep green: `python3 scripts/test_nosf_live_tuner.py` across tuner-facing work; sidecar work adds `scripts/test_gcode_marker.py`; UDS/matcher work adds `scripts/test_klipper_motion_tracker.py`; parity work adds `scripts/test_phase_2_10_parity.py`.

### Plan
- **2.10.1 — Sidecar generator:** update `scripts/gcode_marker.py` with `build_sidecar(input_path, sidecar_path, dia)`, `--emit sidecar`, `--sidecar PATH`, source SHA-256, M82/M83 E-mode tracking, Orca/standard layer detection, feature/width/height/feedrate/v_fil_bin segmentation, skip metadata for `EXCLUDE_OBJECT_START/END`, and no `RUN_SHELL_COMMAND` output in sidecar mode. Add `scripts/test_gcode_marker.py` and `tests/fixtures/orca_sample.gcode`. Validate with the 2.10.1 gate.
- **2.10.2 — KlipperApiClient:** add `scripts/klipper_motion_tracker.py` with stdlib `socket.AF_UNIX` JSON-RPC client, ETX framing, partial-chunk reassembly, nonblocking `poll(timeout_s)`, `objects/list`, and exact `objects/subscribe` request shape. Add socketpair tests in `scripts/test_klipper_motion_tracker.py`. Validate with the 2.10.2 gate.
- **2.10.3 — SegmentMatcher:** extend `scripts/klipper_motion_tracker.py` with sidecar loading, SHA validation, byte-position binary search, Z/retract/stale guards, START/LAYER/FINISH/feature event synthesis, and speed/extrude factor correction. Extend tracker tests and add `scripts/test_phase_2_10_parity.py` using the Orca fixture. Validate with the 2.10.3 gate.
- **2.10.4 — Tuner integration:** before committing, document Pi experiments Q-2.10-A through Q-2.10-E in this TASK section. Then wire `--klipper-uds`, `--klipper-mode {auto,on,off}`, and `--sidecar` into `scripts/nosf_live_tuner.py`; pump Klipper deltas through `SegmentMatcher.update`; suppress marker-file pump with an explicit flag when sidecar is attached; keep marker fallback for `auto` failures and enforce nonzero exit for `on` failures. Validate with the 2.10.4 gate.
- **2.10.5 — Docs:** update `MANUAL.md`, `KLIPPER.md`, `README.md`, and `CONTEXT.md` so sidecar + UDS is the primary calibration flow, shell markers are fallback/debug, UDS path discovery is documented, and Phase 2.10 appears in history. Run all preceding tests.
- **2.10.6 — Deprecation flip:** after hardware soak criteria are satisfied or maintainer explicitly allows, flip `gcode_marker.py` default `--emit` to `sidecar`, warn on shell-marker emit modes, warn when `scripts/nosf_marker.py` is invoked, and move shell-marker docs under Legacy. Smoke-test `--emit file` fallback on a sample G-code.

### Completed Steps
- Phase 2.10 preflight read done; implementation plan appended. Committed and pushed `9e9afca`.
- 2.10.1 done: added `--emit sidecar`, sidecar JSON generation, Orca fixture, and stdlib `test_gcode_marker.py`; validation passed (`python3 -m py_compile scripts/gcode_marker.py`, `python3 -m py_compile scripts/test_gcode_marker.py`, `python3 scripts/test_gcode_marker.py`, `python3 -m py_compile scripts/*.py`, `python3 scripts/test_nosf_live_tuner.py`). Committed and pushed `4b59571`.
- 2.10.2 done: added stdlib Klipper UDS client with ETX framing, partial-chunk reassembly, subscribe request shape, and socketpair tests. Committed and pushed `803a327`.
- 2.10.3 done: added `SegmentMatcher`, sidecar SHA validation, byte-position lookup, START/LAYER/FINISH/feature synthesis, Z/retract/stale guards, matcher tests, and offline parity test. Validation passed (`python3 -m py_compile scripts/klipper_motion_tracker.py`, `python3 -m py_compile scripts/test_klipper_motion_tracker.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 -m py_compile scripts/test_phase_2_10_parity.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 -m py_compile scripts/*.py`, `python3 scripts/test_nosf_live_tuner.py`). Committed and pushed `09ea8dd`.
- Pi experiment notes for Q-2.10-A through Q-2.10-D were recorded. Committed and pushed `b12c3c3`.

### Pi Experiment Results Before 2.10.4
- Q-2.10-A actual UDS path: `ps -ef | grep '[k]lippy.py'` shows Klipper launched with `-a /home/pi/printer_data/comms/klippy.sock`; `/tmp/klippy_uds` does not exist. Default can stay `/tmp/klippy_uds`, but docs and examples must show `--klipper-uds /home/pi/printer_data/comms/klippy.sock` for this printer.
- Q-2.10-B subscribe initial snapshot: `objects/subscribe` returns an immediate snapshot while idle/complete. Snapshot included `motion_report`, `gcode_move`, `print_stats.state=complete`, `print_stats.current_layer=null`, `virtual_sdcard.file_position=file_size`, and `webhooks.state=ready`.
- Q-2.10-C motion stream through manual commands: fixed probe confirmed async deltas arrive during manual `G91/G1/M400/M105/G1/M400/G90`. Initial response is under `result.status`; later async messages are under `params.status`. Deltas showed `motion_report.live_position` changing and `live_velocity` 50.0 then 0.0; subscription survived `M400` and `M105`.
- Q-2.10-D `virtual_sdcard.file_position`: active-print probe showed `state=printing`, filename `Voron_Design_Cube_v7_Skywire_PETG_OSEQ_26m27s.gcode`, `fp=14543`, and reading bytes around offset 14543 from `/home/pi/printer_data/gcodes/<filename>` produced valid G-code move text. Treat `file_position` as byte-indexed.
- Q-2.10-E `print_stats.state` during `EXCLUDE_OBJECT`: long subscribe probe saw `print_stats.state=printing`, `virtual_sdcard.is_active=True`, and `webhooks.state=ready` while Klipper console logged `// Excluding object VORON_DESIGN_CUBE_V7.DRC_ID_0_COPY_0`; no status transition was emitted. Matcher must not rely on `print_stats.state` for object exclusion and should rely on sidecar skip/byte-position guards.
- 2.10.4 done: integrated Klipper UDS motion tracking into `nosf_live_tuner.py` behind `--klipper-uds`, `--klipper-mode {auto,on,off}`, and `--sidecar`; added initial/delta status merge helpers; pumped synthesized START/LAYER/feature/FINISH strings through `tuner.on_m118`; suppressed marker-file input with an explicit UDS sidecar guard; preserved auto fallback and observe-only behavior. Validation passed (`python3 -m py_compile scripts/nosf_live_tuner.py`, `python3 -m py_compile scripts/klipper_motion_tracker.py scripts/test_klipper_motion_tracker.py scripts/test_nosf_live_tuner.py scripts/test_phase_2_10_parity.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `5cf786e`.
- 2.10.5 done: updated `MANUAL.md`, `KLIPPER.md`, `README.md`, and `CONTEXT.md` so sidecar + UDS is the primary calibration flow, UDS path discovery is documented, shell markers are the fallback path, and Phase 2.10 appears in phase history. Validation passed (`python3 -m py_compile scripts/gcode_marker.py scripts/test_gcode_marker.py scripts/klipper_motion_tracker.py scripts/test_klipper_motion_tracker.py scripts/nosf_live_tuner.py scripts/test_nosf_live_tuner.py scripts/test_phase_2_10_parity.py`, `python3 scripts/test_gcode_marker.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `06304cb`.
- 2.10.6 done: flipped `gcode_marker.py` default to `--emit sidecar`, added deprecation warnings for `--emit file|mark|both`, added `nosf_marker.py` invocation warning, moved shell-marker setup under `Legacy Shell-Marker Fallback` in `KLIPPER.md`, and preserved `--emit file` fallback behavior. Validation passed (`python3 -m py_compile scripts/gcode_marker.py scripts/test_gcode_marker.py scripts/nosf_marker.py`, `python3 scripts/test_gcode_marker.py`, manual smoke `python3 scripts/gcode_marker.py tests/fixtures/orca_sample.gcode --output <tmp>/orca_legacy.gcode --emit file` with deprecation warning and `RUN_SHELL_COMMAND CMD=nosf_marker`, manual smoke `python3 scripts/nosf_marker.py --file <tmp>/markers.log NT:START` with deprecation warning, `python3 -m py_compile scripts/*.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 scripts/test_phase_2_10_parity.py`). Committed and pushed `65e0f88`.
- 2.10.7 hotfix: live Pi probe showed every matched segment had `skip=True` because static `EXCLUDE_OBJECT_START/END` object regions were treated as excluded by default. Corrected sidecar generation to keep object metadata while leaving `skip=False`; runtime extrusion velocity and byte-position guards handle actual excluded-object skips. Validation passed (`python3 -m py_compile scripts/gcode_marker.py scripts/test_gcode_marker.py scripts/klipper_motion_tracker.py scripts/test_klipper_motion_tracker.py`, `python3 scripts/test_gcode_marker.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 -m py_compile scripts/*.py`). Commit pending.

---

## Phase 2.11 — Smarter Bucket Lock/Unlock

### Findings
- Phase 2.10 sidecar+UDS marker stream is dense and accurate; it exposed a long-standing bug in `scripts/nosf_live_tuner.py:591-602` where a single residual outlier unlocks a healthy bucket because the threshold `radius = sqrt(4*(P + R_BASE))` collapses once `P` settles (≈ 23 sps at `P=35`).
- Field evidence (May 2026 print): `Outer wall_v1375` cycled LOCKED→TRACKING→LOCKED at least 3 times; `x` walked across `567→419→524→354`. Total result was 1 LOCKED out of 115 buckets after 1475 s MID.
- Phase 2.9 §17.1.6 chained `_MIGRATIONS` registry pattern must be reused for schema 3→4; non-destructive defaults required.
- `on_m118` ingress contract and `klipper_motion_tracker.py` are out of scope for Phase 2.11; only the tuner-internal lock/unlock algorithm changes.
- Existing Phase 2.9 dual-path lock criteria (Path A multi-run, Path B single high-confidence print) remain the lock gates; Phase 2.11 adds a noise gate and rewrites only the unlock branch.

### Plan
- Durable plan committed in `SYNC_REFACTOR_PHASE_2_11.md` (this commit, milestone 2.11.0).
- Milestones:
  - 2.11.1 reproduction fixture + failing chatter test (`tests/fixtures/phase_2_11_chatter.json`, expected-fail assertion).
  - 2.11.2 residual statistics (`resid_ewma`, `resid_var_ewma`, `outlier_streak`, `locked_sample_count`, `last_unlock_reason`) + schema 3→4 migration via `_migrate_3_to_4`.
  - 2.11.3 three-channel unlock (catastrophic / streak / drift) + noise-gated lock + `MIN_LOCK_DWELL` + `P_UNLOCK_RESET=400`; flip chatter test to hard assertion.
  - 2.11.4 `--state-info --verbose` columns, new `wait` reasons (`noise σ²=..`, `dwell N/M`), MANUAL/KLIPPER/README/CONTEXT updates.
  - 2.11.5 Pi soak: 3 back-to-back calibration prints; success = same bucket stays LOCKED across runs 2 and 3, ≤1 unlock line per bucket per print, schema-4 state file loads.
- Risk: constants may need tuning on Pi (R-1); `nosf_analyze.py` `load_state` may need a `migrate_state_data` call (R-7).

### Completed Steps
- Phase 2.11 preflight read done; implementation plan committed in `SYNC_REFACTOR_PHASE_2_11.md`. Commit SHA recorded after push.
- docs: commonized caveman/cavemem protocol in AGENTS.md; committed and pushed ad8eeb7.
- docs: simplified caveman rule in AGENTS.md; committed and pushed 93fea40.
- 2.11.1 done: added `tests/fixtures/phase_2_11_chatter.json` and a soft expected-fail chatter repro test; validation passed (`python3 -m py_compile scripts/nosf_live_tuner.py`, `python3 -m py_compile scripts/test_nosf_live_tuner.py`, `python3 scripts/test_nosf_live_tuner.py`). Committed and pushed `1675c8d`.
- 2.11.2 done: added residual EWMA bucket fields, schema 3→4 migration, schema-chain tests, and analyzer schema-4 load compatibility while leaving lock/unlock behavior unchanged; validation passed (`python3 -m py_compile scripts/nosf_live_tuner.py`, `python3 -m py_compile scripts/test_nosf_live_tuner.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `4aaabc6`.
- 2.11.3 done: replaced single-sample unlock with catastrophic/streak/drift channels, added noise-gated locking and lock dwell, flipped chatter repro to a hard assertion, and added algorithm tests; validation passed (`python3 -m py_compile scripts/nosf_live_tuner.py`, `python3 -m py_compile scripts/test_nosf_live_tuner.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `1b22fc0`.
- 2.11.4 done: added `--state-info --verbose` residual columns, verbose CSV fields, new wait reasons, and docs for interpreting noisy buckets; validation passed (`python3 -m py_compile scripts/nosf_live_tuner.py`, `python3 scripts/test_nosf_live_tuner.py`). Commit SHA reported after push.

### Phase 2.11 Pi Validation (May 11 2026)
- Verified lock dwell counter incrementing (1/20 -> 20/20).
- Verified noise gating: buckets with high sigma2 kept in STABLE/wait=noise.
- Verified drift channel: Outer wall_v1375 correctly unlocked on sustained 300+ sps deviation.
- Observed zero chatter: LOCKED buckets stayed locked through scatter that previously caused unlocks.
- Logic behaves as intended: patient with noise, decisive on drift.

### Phase 2.11 Pi Validation (May 11 2026)
- Verified lock dwell counter incrementing (0/20 -> 42).
- Verified noise gating: buckets with massive sigma2 (10k+) held in STABLE/wait=noise.
- Verified drift channel: Outer wall_v1375 correctly unlocked mid-print on sustained error.
- Observed zero chatter: No relock-unlock cycling seen on noisy high-flow segments.
- Logic confirmed: Patient with scatter, aggressive with drift.
- bugfix: added CsvEmitter header mapping to nosf_analyze.py; committed and pushed 3805e55.
- bugfix: mapped zone to BUF instead of TC in nosf_analyze.py; committed and pushed ab1830e.
- feat: added RL: manual reload trigger command; committed and pushed deda75d.
- bugfix: enforced PRESS_SPS floor in RELOAD_FOLLOW; committed and pushed 6be1a84.
- bugfix: replaced RELOAD_LEAN under-feed with 1.15x over-feed to prevent gap at high speeds; committed and pushed f08a46a.
- feat: restored tunable RELOAD_LEAN_FACTOR with 1.15 over-feed default; committed and pushed 11bc9a2.

---

## Phase 2.12 — Analyzer Rigor and Noise Gate

### Findings
- Read `AGENTS.md`: caveman/cavemem startup, TASK-first workflow, script validation before commits, commit/push every milestone, no local AI config.
- Read `TASK.md`: Phase 2.11 implementation and Pi validation are present; Phase 2.12.0 exists as committed plan `SYNC_REFACTOR_PHASE_2_12.md` at `20f7b45`.
- Read `SYNC_REFACTOR_PHASE_2_12.md`: source of truth. Phase 2.12 is host-only, keeps state schema 4, replaces the tuner's absolute `V_NOISE_LOCK_THR` lock gate with a relative `sigma/x` gate, then hardens `nosf_analyze.py`.
- Read `SYNC_REFACTOR_PHASE_2_11.md`: preserve residual EWMA fields and catastrophic/streak/drift unlock detector; only lock-side noise decision changes in 2.12.1.
- Read `SYNC_REFACTOR_PHASE_2_10.md` sections 6-9: sidecar+UDS still feeds byte-identical synthetic marker strings through `tuner.on_m118`; no new ingress.
- Read `SYNC_REFACTOR_PHASE_2_9.md` sections 15-17: observe-only default, dual-path lock criteria, and chained `_MIGRATIONS` pattern remain constraints.
- Read current scripts/docs: `nosf_live_tuner.py` still uses absolute `V_NOISE_LOCK_THR=400`; `test_nosf_live_tuner.py` has Phase 2.11 noise tests that must be updated to ratio semantics; `nosf_analyze.py` still has dominant-bucket baseline, `SAFETY_K`, BL-as-sigma, and row-count confidence bugs for later milestones.

### Plan
- 2.12.1: update `scripts/nosf_live_tuner.py` constants/helpers, `_maybe_lock`, and `bucket_wait_reason` to use `sigma/x <= NOISE_RATIO_THR`; add five tuner tests.
- 2.12.2: rewrite analyzer LOCKED floor, mode semantics, `--force`, confidence helper, and initial analyzer tests.
- 2.12.3: replace dominant-bucket baseline/bias with precision-weighted qualifying-bucket aggregation plus field oscillation fixtures.
- 2.12.4: derive `buf_variance_blend_ref_mm` from BP scatter, clamp to `[0.1, 5.0]`, and keep `mid_creep_timeout_ms` at current/default confidence.
- 2.12.5: append `[nosf_contributors]` block and update MANUAL/KLIPPER/README/CONTEXT.
- 2.12.6: run Pi soak or record maintainer-provided results and tune constants only if evidence requires.

### Completed Steps
- Phase 2.12 preflight read done; implementation begins at 2.12.1.
- 2.12.1 done: replaced absolute tuner noise gate with relative `sigma/x` gate, added five tuner tests, and preserved Phase 2.10/2.11 regressions. Validation passed (`python3 -m py_compile scripts/nosf_live_tuner.py`, `python3 -m py_compile scripts/test_nosf_live_tuner.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `5cfbada`.
- 2.12.2 done: added analyzer LOCKED-bucket floor, safe/aggressive/force mode semantics, removed `SAFETY_K` subtraction from baseline, rewrote confidence to bucket-evidence labels, and added analyzer tests. Validation passed (`python3 -m py_compile scripts/nosf_analyze.py`, `python3 -m py_compile scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `813e343`.
- 2.12.3 done: replaced dominant-bucket baseline and all-row bias with trimmed precision-weighted qualifying-bucket aggregation; added field oscillation fixtures and tests. Validation passed (`python3 -m py_compile scripts/nosf_analyze.py`, `python3 -m py_compile scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `de54be2`.
- 2.12.4 done: removed BL-as-sigma parsing, derived variance reference from qualifying BP scatter with `[0.1, 5.0]` clamp, and deferred `mid_creep_timeout_ms` learning to DEFAULT/current. Validation passed (`python3 -m py_compile scripts/nosf_analyze.py`, `python3 -m py_compile scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `b0c2152`.
- 2.12.5 done: added `[nosf_contributors]` patch diagnostics and documented Phase 2.12 analyzer modes, `--force`, relative noise wait reason, and contributor interpretation. Validation passed (`python3 scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 -m py_compile scripts/*.py`). Committed and pushed `920e7b0`.
- 2.12.6 local gate done: full local suite passed (`python3 scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 scripts/test_gcode_marker.py`, `python3 -m py_compile scripts/*.py`). Pi soak cannot run from this workspace; maintainer must run the sequence below and provide captured state-info/patch files for review.

### Phase 2.12 Pi Validation
- Status: **pending maintainer Pi run**. Local code/test gate passed on May 11 2026.
- Required sequence: back up `~/nosf-state/buckets-myprinter.json`; run three back-to-back calibration prints with `nosf_live_tuner.py --observe-daemon --debug --progress-interval 5 --csv-out ~/nosf-runs/phase212-runN.csv`; capture `--state-info --verbose` after each run; run `nosf_analyze.py --mode safe --state ~/nosf-state/buckets-myprinter.json --out ~/nosf-runs/phase212-runN.patch.ini` after each run.
- Record after maintainer run: LOCKED bucket count progression run-1/run-2/run-3, `baseline_rate` and `sync_trailing_bias_frac` recommendation progression, final `buf_variance_blend_ref_mm`, top five high-traffic `sigma/x` ratios, and any `NOISE_RATIO_THR` adjustment.
- Success criteria: at least 5 LOCKED buckets by run 3; baseline drift after run 2 under 50 sps; variance reference within `[0.1, 5.0]` mm; `mid_creep_timeout_ms` remains DEFAULT/current unless a future signal exists.

---

## Phase 2.13 — Acceptance Gate Parity

### Findings
- Read `AGENTS.md`: session banner posted; keep TASK-first workflow, no local AI config, one milestone per commit/push, Python validation for script edits, and `Generated-By: GPT-5.5 (High)` footer.
- Read `TASK.md`: Phase 2.12 implementation is complete through 2.12.6 local gate; Pi validation exposed an analyzer acceptance-gate mismatch rather than a tuner learning failure.
- Read `SYNC_REFACTOR_PHASE_2_13.md`: source of truth. Phase 2.13 is host-only, keeps schema 4, preserves Phase 2.12 recommendation semantics, and fixes the acceptance gate to compare the same state-aware recommendation path used by normal analyzer output.
- Read `SYNC_REFACTOR_PHASE_2_12.md`: preserve LOCKED-bucket floor, safe/aggressive/force behavior, precision-weighted baseline/bias, BP-derived variance reference, and `[nosf_contributors]` output.
- Read `scripts/nosf_analyze.py`: `compute_recommendations()` is state-aware, but `consistency_by_run()` uses raw per-bucket per-run medians from all MID rows. `acceptance_gate()` fails on raw LOCKED row coverage, raw per-bucket deltas, CSV `sigma_mm`, and hard-coded telemetry zeros.
- Read `scripts/test_nosf_analyze.py`: existing tests cover Phase 2.12 recommendation behavior and patch output, but not acceptance-gate parity with standalone recommendations.
- Read `scripts/nosf_live_tuner.py` for schema only: schema remains 4; no tuner learning-loop changes are needed.
- Read docs: MANUAL/KLIPPER/README/CONTEXT document Phase 2.12 analyzer modes; 2.13 docs must explain per-run estimates, skipped immature runs, contributor-mass coverage, and telemetry-placeholder limits.

### Plan
- **2.13.1 — Repro fixture:** add synthetic state and three CSV fixtures under `tests/fixtures/`; add a soft expected-fail test showing raw gate deltas are bogus while standalone recommendations are close.
- **2.13.2 — Shared recommendation path:** add `recommend_for_subset(...)`, make `compute_recommendations()` a wrapper, and make gate consistency use per-run recommendations instead of raw per-bucket medians; flip repro to hard assertion.
- **2.13.3 — Mature-run diagnostics:** add comparable/immature run classification, skip immature runs from consistency, and emit per-run estimate diagnostics in patches.
- **2.13.4 — Coverage refinement:** add contributor-mass coverage gate, downgrade raw 80% row coverage to warning, derive gate sigma from BP rows, and label telemetry counters as pending placeholders.
- **2.13.5 — Docs and Pi validation:** update MANUAL/KLIPPER/README/CONTEXT and record maintainer Pi validation against existing phase212 CSVs/state without destructive DB edits.

### Completed Steps
- Phase 2.13 preflight read done; implementation begins at 2.13.1.
- 2.13.1 done: added synthetic three-run acceptance-gate parity fixture and soft expected-fail analyzer test; validation passed (full Phase 2.13 gate). Committed and pushed `7951502`.
- 2.13.2 done: factored `recommend_for_subset()`, made `compute_recommendations()` a wrapper, changed acceptance-gate consistency to use per-run recommendations, and flipped the parity fixture to a hard assertion. Validation passed (full Phase 2.13 gate). Committed and pushed `e24aef8`.
- 2.13.3 done: added comparable/immature run classification, skipped immature runs from consistency reduction, emitted per-run estimate diagnostics in patches, and covered skipped/true-disagreement cases. Validation passed (full Phase 2.13 gate). Committed and pushed `19589c0`.
- 2.13.4 done: added contributor-mass coverage gate, downgraded raw MID coverage to patch warning, switched acceptance sigma to qualifying BP scatter, and documented telemetry counters as placeholders. Validation passed (full Phase 2.13 gate). Committed and pushed `2d97263`.
- 2.13.5 docs done: documented acceptance-gate parity, skipped-run reasons, contributor-mass coverage, raw-coverage warnings, and telemetry-placeholder limits in MANUAL/KLIPPER/README/CONTEXT. Validation passed (full Phase 2.13 gate). Commit SHA to be recorded after push.

### Phase 2.13 Pi Validation
- Status: **pending maintainer Pi rerun**. The operator's `/home/pi/nosf-runs/phase212-run2.csv`, `phase212-run3.csv`, `phase212-run4.csv`, and `/home/pi/nosf-state/buckets-myprinter.json` are not present in this macOS workspace, so the real `/tmp/nosf-gate.ini` cannot be generated here without fabricating data.
- Required command on the Pi:
  ```bash
  python3 scripts/nosf_analyze.py \
      --in ~/nosf-runs/phase212-run2.csv ~/nosf-runs/phase212-run3.csv ~/nosf-runs/phase212-run4.csv \
      --state ~/nosf-state/buckets-myprinter.json \
      --machine-id myprinter \
      --acceptance-gate \
      --mode safe \
      --out /tmp/nosf-gate.ini
  ```
- Capture from `/tmp/nosf-gate.ini`: the `Acceptance gate` line, `Coverage: contributor mass ..., raw MID coverage ...`, `Consistency: max baseline delta ..., max bias delta ...`, and the full `Per-run estimates used in consistency check` block.
- Expected on the operator evidence from Phase 2.12: consistency should pass with baseline delta near 33 sps and bias delta near 0.003. Raw MID coverage may warn around 74-75%, but should not fail. If contributor mass is below 50%, record it as a LOCKED-bucket coverage issue requiring more calibration data, not a Phase 2.13 gate-parity failure.

## Phase 2.14 — Gate Question Semantics [DONE]

Audit acceptance-gate logic to differentiate between FAIL (logic invalidity/hardware) and WARN (stale config). Implement contributor mass floor and hardware noise ceilings.

### Findings
- Contributor mass denominator needs `n >= 50` floor to exclude noise-prone sparse buckets.
- Acceptance gate currently treats all issues as FAIL; needs a WARN tier for fixable/stale config (e.g. sigma > current_ref).
- Hard ceilings like `SIGMA_HARDWARE_CEILING_MM = 5.0` are needed to surface mechanical failure clearly.
- Consistency logic (comparable runs) is more robust than simple run-count/duration checks; demote the latter to WARN.

### Plan
- [x] 2.14.1: Add repro fixtures for diluted mass, high sigma, and 2-run skip.
- [x] 2.14.2: Implement `DENOMINATOR_MIN_BUCKET_N = 50` floor in `contributor_mass()`.
- [x] 2.14.3: Implement `SIGMA_HARDWARE_CEILING_MM = 5.0` (FAIL) and σ_p95 WARN logic.
- [x] 2.14.4: Refactor run-count, duration, and locked-count to WARN; defer FAIL to comparable-runs.
- [x] 2.14.5: Update docs and perform final validation.

### Unit tests (Phase 2.14)
- [x] `2.14-mass`: diluted mass passes after floor implementation
- [x] `2.14-sigma`: high sigma warns and passes after split implementation
- [x] `2.14-runs`: two runs pass with warning after demotion
- [x] `gate-bp-sigma`: updated to expect WARN
- [x] `gate-parity`: updated to expect FAIL (due to immature field data)

## Phase 2.14 Follow-up — Contributor Mass Gray Band

### Findings
- Maintainer Pi validation after Phase 2.14 still rejects otherwise stable recommendations solely on `contributor mass 47.8% < 50.0%`.
- Patch diagnostics show the hard recommendation-quality signals are healthy: 15 contributors, HIGH confidence, four comparable runs, baseline delta 0 sps, and bias delta 0.022.
- Therefore the 50% contributor-mass edge is acting as a policy cliff. Below ~40% still means locked contributors are a minority of mature state evidence and should fail, but 40-65% should be a visible coverage warning when consistency and contributor count are already strong.

### Plan
- Lower the hard contributor-mass floor from 50% to 40%, keeping the 65% warning tier.
- Add a regression test for the 40-50% gray band so the operator's 47.8% case passes with a warning.
- Update MANUAL.md and KLIPPER.md wording from `<50%` hard failure to `<40%` hard failure / `<65%` warning.
- Validate analyzer and script suites, then commit and push.

### Completed Steps
- Implemented gray-band contributor-mass behavior: <40% remains FAIL, 40-65% is WARN. Validation passed (`python3 scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_live_tuner.py`, `python3 scripts/test_phase_2_10_parity.py`, `python3 scripts/test_klipper_motion_tracker.py`, `python3 scripts/test_gcode_marker.py`, `python3 -m py_compile scripts/*.py`). Commit pending.

## Analyzer Input Glob Support

### Findings
- Maintainer wants `scripts/nosf_analyze.py --in ~/nosf-runs/phase212-run1*csv` to work without manually listing files.
- Shells usually expand unquoted globs, but analyzer-side expansion is still useful for quoted patterns, scripts, and clearer no-match errors.
- `read_csv_runs(paths)` is the narrow input boundary used by CLI and tests, so expanding there preserves existing `--in file1 file2` behavior.

### Plan
- Add stdlib glob/user-home expansion for `--in` path arguments, preserving normal explicit paths and sorting matches per pattern.
- Raise a normal file-not-found error when a glob pattern has no matches.
- Add analyzer regression coverage for glob expansion.
- Validate analyzer tests and script py_compile, then commit and push.

### Completed Steps
- Implemented `--in` glob expansion in `read_csv_runs()` with `~` support and sorted matches. Added analyzer regression coverage for `phase212-run*csv`. Validation passed (`python3 -m py_compile scripts/nosf_analyze.py scripts/test_nosf_analyze.py`, `python3 scripts/test_nosf_analyze.py`, `python3 -m py_compile scripts/*.py`). Commit pending.

## OpenSpec Global Skills Cleanup

### Findings
- Project-local OpenSpec/OpsX skill bundles were present under `.agent/`, `.claude/`, `.codex/`, `.gemini/`, `.github/skills/`, and `.github/prompts/`.
- AGENTS/AI policy is global-first AI config: repo should keep project OpenSpec data, not personal tool-local skill folders.
- `openspec/config.yaml` is project-specific and should be committed as the source of truth for spec-driven context.

### Plan
- Move/copy OpenSpec/OpsX skills and commands to matching home-directory global config locations.
- Remove project-local AI config bundles from the repo worktree.
- Ignore local AI config paths in `.gitignore`.
- Document OpenSpec project initialization in `AI.md`, and fill `openspec/config.yaml` with NOSF context.

### Completed Steps
- Global OpenSpec/OpsX skills installed under `~/.agents`, `~/.claude`, `~/.codex`, `~/.gemini`, and `~/.github`; project-local copies removed. Ignore/docs/OpenSpec config updates pending commit.

## OpenSpec Design Migration

### Findings
- The root `SYNC_REFACTOR*.md` files contain durable implementation notes, phase plans, validation thinking, and design rationale rather than day-to-day scratch work.
- These files are better suited to `openspec/design/` so future agents have one place to look for design history.
- Root docs should stay either operator-facing (`README.md`, `MANUAL.md`, `KLIPPER.md`) or agent-facing (`AGENTS.md`, `AI.md`); detailed phase design belongs under OpenSpec.

### Plan
- Move `SYNC_REFACTOR_PLAN.md` and `SYNC_REFACTOR_PHASE_2_8.md` through `SYNC_REFACTOR_PHASE_2_14.md` into `openspec/design/sync-refactor/`.
- Add OpenSpec README/index files summarizing layout, current design baseline, and future tracking suggestions.
- Update `CONTEXT.md` and `AI.md` to point agents at `openspec/design/`.
- Validate links/status with `git status` and commit.

### Completed Steps
- Moved sync-refactor phase notes into `openspec/design/sync-refactor/` and added OpenSpec indexes. Added proposed `changes/`, `design/adr/`, and `design/validation/` tracking scaffolds. Validation passed (`git diff --check`). Commit pending.

## OpenSpec Spec Alignment

### Findings
- Moving historical notes as-is preserves audit history, but it does not provide an OpenSpec-native current contract.
- `openspec list --specs` reported no specs, confirming agents had design archives but no normalized spec entry point.
- `AGENTS.md` mentioned AI setup but did not tell agents that NOSF now uses OpenSpec for durable design/spec tracking.

### Plan
- Add `openspec/specs/sync-refactor/spec.md` using OpenSpec requirement/scenario format for the current sync/calibration/tuner/analyzer contract.
- Add `openspec/design/sync-refactor/tasks.md` as a completed task ledger mapped from the old phases.
- Add `openspec/design/sync-refactor/spec-traceability.md` to map normalized requirements back to old phase notes.
- Update `AGENTS.md`, `AI.md`, and OpenSpec index docs so agents know to use OpenSpec first for durable behavior contracts.
- Validate with `openspec list --specs`, `openspec spec validate sync-refactor`, and markdown diff checks.

### Completed Steps
- Added `openspec/specs/sync-refactor/spec.md` with 9 validated requirements, plus `tasks.md` and `spec-traceability.md` under `openspec/design/sync-refactor/`. Updated `AGENTS.md`, `AI.md`, and OpenSpec indexes to state that NOSF uses OpenSpec. Validation passed (`openspec list --specs`, `openspec validate --specs sync-refactor`, `git diff --check`). Commit pending.

## OpenSpec Phase Spec Conversion

### Findings
- A single current-state `sync-refactor` spec is not enough for readability/integrity; it hides phase-level contracts that old agents used to find in `SYNC_REFACTOR_PHASE_*.md`.
- Historical files should remain available as provenance, but each old phase area should have an OpenSpec-native `Purpose` + `Requirements` spec.
- `TASK.md` workflow is itself a project contract and should also be represented as an OpenSpec spec rather than only prose in `AGENTS.md`.

### Plan
- Add OpenSpec specs for the historical sync-refactor phase areas: foundation, live tuning, calibration workflow, Klipper motion tracking, bucket locking, analyzer rigor, acceptance-gate parity, and acceptance-gate semantics.
- Add an OpenSpec spec for `TASK.md` workflow and project tracking.
- Update OpenSpec indexes and traceability so agents know which spec replaces which historical file.
- Validate every spec with `openspec validate --specs`.

### Completed Steps
- Added OpenSpec-native phase specs for the historical sync-refactor foundation,
  live tuner, calibration workflow, Klipper motion tracking, bucket locking,
  analyzer rigor, acceptance-gate parity, and acceptance-gate semantics.
- Added `task-workflow` spec so `TASK.md`/AGENTS workflow is represented as a
  durable OpenSpec contract.
- Updated OpenSpec README, sync-refactor history index, traceability map, and
  AGENTS guidance so agents start from specs and use phase notes as provenance.

## OpenSpec Task and Context Split

### Findings
- `TASK.md` is 586 lines and mostly historical sync-refactor findings/plans;
  that content is useful, but it makes the active handoff file noisy.
- `openspec/design/sync-refactor/tasks.md` has a concise ledger, but there is no
  OpenSpec-owned verbatim archive of the old `TASK.md` narrative.
- `CONTEXT.md` is only 186 lines and still useful as a quick architecture guide,
  but its durable architecture/gotcha rules should also exist as an OpenSpec
  spec so agents can validate and discover them with `openspec list --specs`.

### Plan
- Archive the current full `TASK.md` content under `openspec/design/` before
  trimming it.
- Add a validated `project-architecture` OpenSpec spec based on `CONTEXT.md`
  module ownership, runtime parameter pattern, persistence/protocol rules, and
  critical gotchas.
- Replace `TASK.md` with a lean current-task handoff that points to the
  OpenSpec task history and specs.
- Update `AGENTS.md`, `CONTEXT.md`, and OpenSpec indexes to reference the new
  locations.
- Validate with `openspec validate --specs`, `openspec list --specs`, and
  `git diff --check`.

### Completed Steps
- Pending.
