# Phase 2.9 — Calibration Workflow (Observe-Only Tuner + Mature Analyzer)

> **Status:** PROPOSED. Companion to `SYNC_REFACTOR_PLAN.md` and
> `SYNC_REFACTOR_PHASE_2_8.md`. Builds on Phase 2.7 telemetry pipeline
> (`MARK:`, `gcode_marker.py`, `nosf_logger.py`) and Phase 2.8 live
> tuner (`nosf_live_tuner.py`). **No new firmware features.** Optional
> firmware cleanup deferred to 2.9.7.
>
> **Why a new phase.** Phase 2.8 implemented a closed-loop online
> tuner. Real-print experience (4 min calibration prints) shows the
> closed-loop write path is architecturally mismatched with firmware
> globals: per-(feature, v_fil_bin) buckets cannot drive global
> tunables without thrashing rate limits. Final firmware target is
> standalone (no host attached). Phase 2.9 reorients tuning toward
> **calibration prints → offline aggregation → reviewed config patch
> → flash → detach host**.

## 0. Decision Addendum

These are fixed by the maintainer for Phase 2.9. Where any section
conflicts, this list wins.

| # | Topic | Decision |
|---|---|---|
| M1 | No new firmware features | All work in `scripts/`, `*.md`. `LIVE_TUNE_LOCK` stays as-is for backward compatibility. |
| M2 | Observe-only default | Tuner default mode emits zero `SET:` writes. Live writes opt-in via explicit flags. |
| M3 | Patch never overwrites repo config | Output goes to `/tmp/nosf-patch.ini` or `config.patch.ini`. Operator manually merges. |
| M4 | No firmware-side persistence change | No `settings_t` change, no `SETTINGS_VERSION` bump. |
| M5 | Lean implementation | Pure stdlib + `pyserial` only. No `numpy`, `scipy`, `pandas`. |
| M6 | Cumulative criteria across runs | LOCKED criteria account for short prints by aggregating sample count, run count, layer count across calibration runs. |
| M7 | Acceptance gate gates commit | Analyzer refuses to emit a patch unless coverage, consistency, and telemetry quality thresholds pass. |
| M8 | Final operation is host-detached | Workflow ends with operator flashing reviewed defaults, then disconnecting USB host. Live tuner is **calibration-time only**. |

## 1. Findings (post-2.8)

1. **Phase 2.8 closed loop produces a single global average.** `emit_patch`
   (in `nosf_live_tuner.py`) computes recency-weighted mean across
   LOCKED buckets and writes a single `sync_trailing_bias_frac` plus
   commented `baseline_rate_sps_suggestion`. Net result is identical to
   what offline analysis would produce, minus the live-write
   instability and rate-limit thrashing.
2. **Firmware tunables are global, not per-feature.** `SYNC_TRAILING_BIAS_FRAC`,
   `MID_CREEP_*`, `BUF_VARIANCE_BLEND_*`, `g_baseline_target_sps` are
   single values applied to every feature. Per-bucket Kalman state on
   host cannot map to per-feature firmware behavior. Online SET writes
   to globals create transients on bucket transitions.
3. **EST is a derived quantity, not a target.** Firmware estimator
   produces `EST` from current baseline + buffer feedback. Writing
   `BASELINE_SPS := EST` closes a degenerate loop. `--allow-baseline-writes`
   is correctly disabled by default (commit `cb3f5b7`); same logic
   applies to bias writes.
4. **Short prints starve buckets.** 4-minute print at 10 Hz status
   polling = ~2400 samples. After bucket binning at 25 mm³/s,
   ~30-80 buckets typical. Average 30-80 samples/bucket, far below
   `N_MIN_SAMPLES = 200`. Multi-run accumulation needed; current
   tuner does not enforce it as a lock criterion.
5. **`nosf_analyze.py` is half-built.** Already aggregates
   `nosf_logger.py` CSVs and emits baseline + bias_delta. Gaps:
   5 mm³/s bin (same bug already fixed in tuner), only 2 of 7
   tunables, hardcoded `n>=30` minimum, no acceptance gate, no
   sigma/telemetry quality check.
6. **`gcode_marker.py` already feeds both pipelines.** Same `MK:`
   stream usable by CSV logger and live tuner. No new instrumentation
   needed.
7. **Final product target is standalone.** Once defaults are baked
   into `config.ini` and flashed, there is no host. All tuning
   activity must be confined to a calibration window.

## 2. Goals

- Tuner default = **observe-only**. Reads markers, parses status,
  updates buckets, persists JSON. No `SET:` writes.
- Live writes available behind explicit `--allow-bias-writes` and
  `--allow-baseline-writes` flags for research/debugging only.
- Lock criteria account for cumulative samples and run count, not
  just within-print 60 s wall clock.
- Analyzer covers all 7 tunables, computes a confidence-rated patch,
  refuses to emit unless acceptance gate passes.
- Patch always written to a review file. Repo `config.ini` only
  changes via operator merge.
- Documentation describes the four-step operator workflow:
  calibrate → analyze → review → flash.

## 3. Architecture

```
                  ┌─────────────────────────────────────┐
                  │ Calibration print (operator-driven) │
                  │  - sliced gcode through             │
                  │    scripts/gcode_marker.py          │
                  │  - run gcode on Klipper             │
                  └──────────────┬──────────────────────┘
                                 │
                ┌────────────────┴────────────────────┐
                ▼                                     ▼
   ┌─────────────────────────────┐      ┌────────────────────────────┐
   │ scripts/nosf_logger.py      │      │ scripts/nosf_live_tuner.py │
   │  CSV capture (status rows + │      │  observe-only by default   │
   │   feature/v_fil from MK:)   │      │  bucket Kalman + JSON      │
   │  out: runN.csv              │      │  state warm-start          │
   └──────────────┬──────────────┘      └──────────┬─────────────────┘
                  │                                 │
                  └─────────────┬───────────────────┘
                                ▼
                  ┌─────────────────────────────┐
                  │ ≥ 3 calibration runs done.  │
                  │ Bucket state + CSV corpus   │
                  │ ready for analysis.         │
                  └──────────────┬──────────────┘
                                 ▼
                  ┌─────────────────────────────┐
                  │ scripts/nosf_analyze.py     │
                  │  --in run1.csv run2.csv ... │
                  │  --state ~/nosf-state/...   │
                  │  --out config.patch.ini     │
                  │  --acceptance-gate          │
                  │                             │
                  │  emits all 7 tunables OR    │
                  │  exits non-zero with        │
                  │  acceptance failure reason  │
                  └──────────────┬──────────────┘
                                 ▼
                  ┌─────────────────────────────┐
                  │ Operator review:            │
                  │   diff config.ini patch.ini │
                  │   merge desired keys        │
                  │   commit + push             │
                  │   ninja -C build_local      │
                  │   flash firmware            │
                  └──────────────┬──────────────┘
                                 ▼
                       ┌──────────────────────┐
                       │ Detach USB host.     │
                       │ Standalone operation.│
                       └──────────────────────┘
```

## 4. Telemetry → tunable mapping

The analyzer computes each tunable from explicit telemetry. No
single-value heuristics; every recommendation carries n, σ, and a
confidence flag.

| Tunable | Source field | Aggregation | Confidence gate |
|---|---|---|---|
| `baseline_rate` | `EST` (MID zone, dominant-speed cluster) | `p50(EST) − k_safe · σ(EST)` | n ≥ 1000, σ < 0.15 · p50 |
| `sync_trailing_bias_frac` | `BP`, `RT` (MID) | `clamp(0.4 + mean(BP−RT)/threshold_mm, 0.05, 0.65)` | n ≥ 1000 |
| `mid_creep_timeout_ms` | MID dwell durations where flow > 0 | `p95(dwell_ms)` | ≥ 50 dwell intervals observed |
| `mid_creep_rate_sps_per_s` | EST recovery slope post-creep | `median(dEST/dt)` while creeping; default 5 if insufficient | ≥ 20 creep events |
| `mid_creep_cap_frac` | EST ramp during creep relative to base | `p90(EST_creep / EST_at_start) · 100` | ≥ 20 creep events |
| `buf_variance_blend_frac` | `BPV` (g_buf_sigma_mm) distribution under stable flow | 0.5 if σ stays low; reduce toward 0.3 if false-pulls visible | n ≥ 500 |
| `buf_variance_blend_ref_mm` | σ p95 during clean MID | round to 0.5 mm steps | n ≥ 500 |

`k_safe` constants:
- `safe` mode: 1.5 (default)
- `aggressive` mode: 1.0

## 5. Lock criteria for short prints

Replace within-print 60 s wall-clock dwell with cumulative criteria
across calibration runs.

A bucket is **STABLE** when:
- `P_b < P_STABLE_THR` (filter converged)
- bias is in safe range `[BIAS_SAFE_MIN, BIAS_SAFE_MAX]`

A bucket transitions **STABLE → LOCKED** when ALL hold:
- `n_b ≥ N_MIN_SAMPLES_CUMULATIVE` (default 200, cumulative across runs)
- `runs_seen_b ≥ N_MIN_RUNS` (default 2)
- `layers_seen_b ≥ N_MIN_LAYERS` (default 3)
- `cumulative_mid_s_b ≥ MIN_MID_TIME_S` (default 60)

`runs_seen_b` increments once per `NT:START` marker (first time the
bucket is touched in that run).
`layers_seen_b` increments on layer-boundary markers from
`gcode_marker.py --every-layer`.
`cumulative_mid_s_b` accumulates real-time spent in MID for that
bucket across runs.

The `BUCKET_LOCK_S` constant is removed. Its semantic role is
replaced by `MIN_MID_TIME_S` aggregated across runs.

## 6. Tuner mode matrix

| Mode | Reads markers | Reads status | Bucket KF | JSON persist | `SET:LIVE_TUNE_LOCK:1` | `SET:TRAIL_BIAS_FRAC` | `SET:BASELINE_SPS` | `SV:` |
|---|---|---|---|---|---|---|---|---|
| `--observe` (default) | yes | yes | yes | yes | no | no | no | no |
| `--allow-bias-writes` | yes | yes | yes | yes | yes | yes (rate-limited) | no | no |
| `--allow-baseline-writes` | yes | yes | yes | yes | yes | no | yes (rate-limited) | no |
| `--commit-flash` | yes | yes | yes | yes | yes | yes (final) | yes (final) | yes (one-shot at end) |

Combinations of `--allow-*` flags are additive. `--commit-flash`
implies `--allow-bias-writes` and `--allow-baseline-writes`.

The default `--observe` mode replaces what was previously called
"warm-up + closed-loop". Patch emission via `--commit-on-finish` /
`--commit-on-idle` works identically in observe mode: it just writes
`/tmp/nosf-patch.ini`, never `SV:`.

## 7. State file extensions

Schema bump: `_schema: 2`.

Per-bucket fields added:
- `runs_seen: int`
- `layers_seen: int`
- `cumulative_mid_s: float`
- `low_flow_skip_count: int`
- `rail_skip_count: int`
- `rollback_count: int`
- `first_seen_run: str` (ISO date of first run that touched bucket)

Loader behavior on schema mismatch:
- `_schema == 1`: auto-migrate by zeroing new counters; preserve
  existing `x`, `P`, `n`, `bias`, `bp_ewma`, `locked`. Bump file to
  schema 2 on first persist.
- `_schema > 2`: refuse to load; print error; exit non-zero.

## 8. Patch emission rules

`emit_patch` writes a complete `[nosf]` review block to
`/tmp/nosf-patch.ini`:

```
# nosf_analyze.py emitted patch
# Source: 3 runs, 18234 samples, 7 LOCKED buckets
# Acceptance gate: PASS
# Coverage: 87.4 %% of MID time in LOCKED buckets
# Consistency: max baseline delta 32 sps, max bias delta 0.04
# Telemetry: ADV_RISK_HIGH=2, EST_FALLBACK=0, ADV_DWELL_STOP=0

[nosf_review]
# Each line: current_value -> suggested_value (confidence)
# baseline_rate:                1600 -> 1582 (HIGH, n=12044, sigma=87)
# sync_trailing_bias_frac:      0.400 -> 0.342 (HIGH, n=12044)
# mid_creep_timeout_ms:         4000 -> 3800 (MEDIUM, 64 dwells)
# mid_creep_rate_sps_per_s:     5    -> 5    (DEFAULT, insufficient creep events)
# mid_creep_cap_frac:           10   -> 12   (LOW, 22 creep events)
# buf_variance_blend_frac:      0.500 -> 0.500 (HIGH, sigma p95 0.41)
# buf_variance_blend_ref_mm:    1.000 -> 0.500 (HIGH, sigma p95 0.41)

# To apply, copy values into config.ini, then run:
#   python3 scripts/gen_config.py
#   ninja -C build_local
#   bash scripts/flash_nosf.sh
```

The patch file is **commented out by default**. Operator
intentionally uncomments and copies into `config.ini`. Never
machine-applied. Never overwrites `config.ini` directly.

If acceptance gate fails, write the same file with header
`Acceptance gate: FAIL` and an explicit reason list, then exit 1.
Suggested values still printed for visibility but flagged with
`(REJECTED)`.

## 9. Acceptance gate

Patch emission requires ALL of:

| Check | Threshold |
|---|---|
| Coverage | ≥ 80% of MID time spent in buckets that reached LOCKED |
| Consistency: per-bucket baseline | max Δx across runs ≤ 50 sps |
| Consistency: per-bucket bias | max Δbias across runs ≤ 0.05 |
| Telemetry: `EV:SYNC,ADV_DWELL_STOP` | count 0 |
| Telemetry: `EV:SYNC,ADV_RISK_HIGH` | count ≤ 5 per run |
| Telemetry: `EV:BUF,EST_FALLBACK` | count 0 |
| Telemetry: σ p95 | < `buf_variance_blend_ref_mm` (current config) |
| Run count | ≥ 3 |
| Run duration | each ≥ 10 min OR cumulative ≥ 30 min |
| Locked bucket count | ≥ 3 |

Any failure → exit 1 with a reason list. Operator runs more
calibration prints or investigates the flagged regime.

## 10. Operator workflow

```
# 1. Slice gcode, postprocess to inject markers
python3 scripts/gcode_marker.py print.gcode

# 2. Start CSV logger and live tuner in parallel
#    (logger captures full status stream; tuner watches markers)
python3 scripts/nosf_logger.py --port /dev/ttyACM0 \
    --out ~/nosf-runs/runN.csv &
python3 scripts/nosf_live_tuner.py --port /dev/ttyACM0 \
    --machine-id myprinter \
    --marker-file /tmp/nosf-markers-myprinter.log \
    --commit-on-finish &

# 3. Run print on Klipper. After 3+ runs:
python3 scripts/nosf_analyze.py \
    --in ~/nosf-runs/run1.csv ~/nosf-runs/run2.csv ~/nosf-runs/run3.csv \
    --state ~/nosf-state/buckets-myprinter.json \
    --out config.patch.ini \
    --acceptance-gate

# 4. Review and merge
diff config.ini config.patch.ini
$EDITOR config.ini   # copy desired suggestions

# 5. Regenerate, build, flash
python3 scripts/gen_config.py
ninja -C build_local
bash scripts/flash_nosf.sh

# 6. Detach host. NOSF runs standalone.
```

Note: `nosf_logger.py` and `nosf_live_tuner.py` cannot share a TTY.
Either run logger only (analyzer reads CSVs), or run tuner only
(analyzer reads JSON state), or alternate runs. Recommended:
**logger for runs 1-2, tuner for runs 3+**, since tuner JSON state
is the durable source for bucket counters but logger CSVs feed the
analyzer's per-tunable computations.

## 11. Implementation milestones

```
[ ] 2.9.0  observe-only default + flag matrix
[ ] 2.9.1  bucket state extensions + schema 2
[ ] 2.9.2  cumulative lock criteria
[ ] 2.9.3  state-info diagnostic upgrade
[ ] 2.9.4  analyzer parity (all 7 tunables, 25-bin, acceptance gate)
[ ] 2.9.5  patch emission rules (review-only, never overwrite)
[ ] 2.9.6  documentation
[ ] 2.9.7  optional firmware cleanup (LIVE_TUNE_LOCK removal)
```

Each milestone is one commit + push per AGENTS.md rule #3. No
firmware build required for 2.9.0–2.9.6.

### 2.9.0 — Observe-only default + flag matrix

**Files:** `scripts/nosf_live_tuner.py`, `scripts/test_nosf_live_tuner.py`

Changes:
- Add `--allow-bias-writes` flag, default false. Mirror existing
  `--allow-baseline-writes`.
- Add `--commit-flash` flag, default false. Implies both `--allow-*`
  flags and enables `SV:` after `--commit-on-finish` /
  `--commit-on-idle` triggers.
- Restructure `_maybe_emit_set` to gate bias writes by
  `self.allow_bias_writes` (currently always allowed).
- Restructure `run_loop` to skip `_engage_lock` and `SET:LIVE_TUNE_LOCK:1`
  when in pure observe mode (no `--allow-*` flags).
- Strip the `SV:` call from the existing commit path; gate it behind
  `--commit-flash`.
- `--commit-on-finish` and `--commit-on-idle` always emit
  `/tmp/nosf-patch.ini`. The `SV:` step is now opt-in.
- Module docstring updated to describe four modes.

Tests:
- `test_observe_default_no_writes` — verify zero serial writes after
  20 status updates with `allow_bias_writes=False`.
- `test_allow_bias_writes_writes` — verify bias writes still happen
  when explicit flag set.
- `test_commit_flash_invokes_sv` — verify `SV:` only fires under
  `--commit-flash`.

Validation:
```
python3 -m py_compile scripts/nosf_live_tuner.py
python3 scripts/test_nosf_live_tuner.py
```

### 2.9.1 — Bucket state extensions + schema 2

**Files:** `scripts/nosf_live_tuner.py`, `scripts/test_nosf_live_tuner.py`

Changes:
- Bump `SCHEMA_VERSION = 2`.
- Extend `Bucket` dataclass with: `runs_seen`, `layers_seen`,
  `cumulative_mid_s`, `low_flow_skip_count`, `rail_skip_count`,
  `rollback_count`, `first_seen_run`.
- Update `_persist` to serialize all new fields.
- Update `_load_state` to:
  - schema 1: auto-migrate (zero new counters, preserve existing
    fields), bump file to schema 2 on next persist.
  - schema 2: load directly.
  - schema > 2: error and exit.
- Increment counters in the right paths:
  - `low_flow_skip_count` in the `est < MIN_LEARN_EST_SPS` branch.
  - `rail_skip_count` in `_maybe_emit_set` bias-rail-guard branch.
  - `rollback_count` in `_rollback_active`.
  - `cumulative_mid_s` in `on_status` when bucket is active and
    `BUF == MID`, accumulating `dt_s`.
- `runs_seen` and `layers_seen` are wired in 2.9.2 (need NT:START
  and layer-boundary marker support).

Tests:
- `test_schema1_migration` — write a v1 state file, load, verify
  zeroed counters, verify next persist emits schema 2.
- `test_counter_increments` — drive low-flow / rail / rollback
  paths; verify counter values.

### 2.9.2 — Cumulative lock criteria

**Files:** `scripts/nosf_live_tuner.py`, `scripts/test_nosf_live_tuner.py`,
`scripts/gcode_marker.py` (layer-marker emission already exists)

Changes:
- Remove `BUCKET_LOCK_S` constant. Add:
  - `N_MIN_SAMPLES_CUMULATIVE = 200`
  - `N_MIN_RUNS = 2`
  - `N_MIN_LAYERS = 3`
  - `MIN_MID_TIME_S = 60.0`
- `runs_seen` increment: in `on_m118` when `NT:START` is detected,
  set a per-bucket flag that next-time-bucket-becomes-active will
  increment `runs_seen`. Reset all bucket "seen this run" flags on
  START.
- `layers_seen` increment: handle layer-boundary markers from
  `gcode_marker.py --every-layer` (compact tag `NT:LAYER:N`).
  When a bucket is active and a layer marker arrives, increment
  the bucket's `layers_seen`.
- `_maybe_lock` rewritten:
  ```
  stable = (P < P_STABLE_THR) and bias in safe range
  if not stable: state = TRACKING
  elif state == STABLE and (
      n >= N_MIN_SAMPLES_CUMULATIVE
      and runs_seen >= N_MIN_RUNS
      and layers_seen >= N_MIN_LAYERS
      and cumulative_mid_s >= MIN_MID_TIME_S
  ): state = LOCKED
  else: state = STABLE
  ```
- `_bucket_wait_reason` updated to print which criterion is the
  blocker (`samples`, `runs`, `layers`, `mid_time`).

Tests:
- `test_short_print_no_lock` — single 60 s simulated run, verify
  no LOCKED.
- `test_three_run_lock` — three simulated runs of 30 s each touching
  same bucket; verify LOCKED after run 3.
- `test_layer_count_required` — verify layers_seen gates LOCKED
  even when n + runs + mid_time satisfied.

### 2.9.3 — State-info diagnostic upgrade

**Files:** `scripts/nosf_live_tuner.py`

Changes:
- `print_state_info` columns: add `runs`, `layers`, `mid_s`,
  `last_seen_age`, `wait` (wait reason).
- New `--state-info --csv` flag emits machine-readable rows for
  analyzer consumption.
- Wider header for legibility.

No new tests required (display function only).

### 2.9.4 — Analyzer parity

**Files:** `scripts/nosf_analyze.py`, new `scripts/test_nosf_analyze.py`

Changes:
- `bin_v_fil`: change `5.0` → `25.0`.
- Add per-tunable computation paths from §4 table.
- Read tuner JSON state in addition to CSVs:
  - `--state ~/nosf-state/buckets-<id>.json` (optional).
  - When present, use bucket counters (`runs_seen`, `layers_seen`,
    `cumulative_mid_s`) for confidence ratings and acceptance gate.
- `--acceptance-gate` flag: refuse to emit unless all checks in §9
  pass.
- Output sections:
  - `# Acceptance gate: PASS/FAIL` header
  - `[nosf_review]` block with current → suggested for all 7
    tunables + per-line confidence (HIGH / MEDIUM / LOW / DEFAULT /
    REJECTED)
  - `# To apply` footer with manual steps

Tests (new file):
- `test_baseline_from_dominant_cluster`
- `test_bias_clamped_to_safe_range`
- `test_acceptance_gate_fail_low_coverage`
- `test_acceptance_gate_pass_three_runs`
- `test_25_bin_alignment_with_tuner`

Validation:
```
python3 -m py_compile scripts/nosf_analyze.py scripts/test_nosf_analyze.py
python3 scripts/test_nosf_analyze.py
```

### 2.9.5 — Patch emission rules

**Files:** `scripts/nosf_live_tuner.py`, `scripts/nosf_analyze.py`

Changes:
- Remove the `sync_trailing_bias_frac: <value>` line from
  `nosf_live_tuner.py emit_patch`. Replace with the same
  `[nosf_review]` block format as `nosf_analyze.py` (commented
  suggestions only).
- Both scripts emit identical patch format. Tuner's patch is
  single-source (last run only); analyzer's patch is multi-run
  with acceptance gate.
- Update both to write `# WARNING: do not blindly apply` header.

No new tests; format change only. Verify by running each script
with a sample state file and visually checking output.

### 2.9.6 — Documentation

**Files:** `MANUAL.md`, `KLIPPER.md`, `README.md`, `CONTEXT.md`,
`TASK.md`

Changes:
- `MANUAL.md`: new section "Calibration workflow". Document 4 tuner
  modes (observe, +bias-writes, +baseline-writes, commit-flash).
  Update `LIVE_TUNE_LOCK` description to "debug-only; not used by
  default observe-only tuner".
- `KLIPPER.md`: new section "Calibration prints". Show parallel
  logger + tuner invocation, then analyzer.
- `README.md`: short flowchart calibrate → analyze → review → flash
  → standalone.
- `CONTEXT.md`: cross-reference Phase 2.9 alongside 2.8.
- `TASK.md`: amend Phase 2.8 status: "live writes deprecated as
  default; observe-only is canonical (Phase 2.9)".

### 2.9.7 — Optional firmware cleanup (deferred)

**Files:** `firmware/src/protocol.c`, `MANUAL.md`

Only proceed if observe-only flow has been exercised on real
hardware for ≥ 5 calibration prints without issue.

Changes:
- Remove `g_live_tune_lock` static + `SET:LIVE_TUNE_LOCK` /
  `GET:LIVE_TUNE_LOCK` handlers + `live_tune_locked_param`
  short-circuit in SET dispatch + `LIVE_TUNE_LOCKED` error reply.
- Remove `MANUAL.md` row for `LIVE_TUNE_LOCK`.
- Net delta: ~30 LOC removed.
- No `settings_t` change. No `SETTINGS_VERSION` bump (the field is
  not persisted).

Validation:
```
ninja -C build_local
```

This milestone is **opt-in**; can be skipped indefinitely. Keeps the
firmware-vs-host backward-compatibility surface stable for already
flashed units in the field.

## 12. Out of scope (Phase 2.9.x follow-ups)

- Firmware-side per-feature tunable lookup table (was Phase 2.8
  Option B). Defer until offline analysis shows global tunables are
  insufficient.
- Cross-machine bucket sharing.
- ML model fitting (xgboost, etc.). Linear regression and
  percentile fits cover all 7 tunables.
- Autonomous flashing from analyzer. Flashing remains operator-only.
- Live tuning during normal print runs (post-calibration). Phase 2.9
  explicitly retires this as a workflow.

## 13. Acceptance criteria

- Tuner `--observe` mode: zero `SET:` writes verified across a
  10-minute simulated run.
- Tuner schema 1 → 2 migration: load preserved, counters zero,
  re-persist emits schema 2.
- Three calibration prints (≥ 10 min each) on real hardware: at
  least 3 LOCKED buckets at production speeds; all bucket counters
  populated.
- Analyzer with acceptance gate: produces patch only when all §9
  checks pass; otherwise exits 1 with reason list.
- Patch file format: review-only; no key=value lines outside
  comments; never overwrites `config.ini`.
- Documentation: operator workflow reproducible from `MANUAL.md`
  alone, no recourse to commit history.
- Firmware build still passes after 2.9.7 (if executed):
  `ninja -C build_local`.
- Existing 2.8 commit-on-idle behavior preserved when explicit flags
  re-enabled (`--allow-bias-writes --commit-flash`).

## 14. Rollback path

- `--observe` mode is reversible at any time: revert to closed-loop
  by passing `--allow-bias-writes`. No state file changes needed.
- Schema 2 → schema 1: add a `--downgrade-schema` utility or just
  delete the state file (`rm ~/nosf-state/buckets-<id>.json`); next
  run cold-starts.
- 2.9.7 firmware cleanup reverts via `git revert` of the commit.
  Tuner's `SET:LIVE_TUNE_LOCK:1` (sent only when `--allow-*` set)
  becomes a silent error reply if the firmware is older than 2.9.7
  but the tuner is newer; benign.

## 15. Open questions (deferred)

- **Q-2.9-A.** Should `nosf_logger.py` and `nosf_live_tuner.py`
  share a reader process to avoid TTY contention? Same as Q-2.8-B;
  defer until operator pain confirms the workaround (run logger or
  tuner, not both) is unacceptable.
- **Q-2.9-B.** Should the analyzer learn `mid_creep_rate_sps_per_s`
  and `mid_creep_cap_frac` from data, or keep them as
  config-time-only choices (Phase 2.8 §12 position)? Phase 2.9
  computes them but defaults to current value when telemetry is
  insufficient. Reconsider after first calibration corpus.
- **Q-2.9-C.** Should `gcode_marker.py` emit per-layer markers by
  default? `--every-layer` is currently opt-in. Required for
  `layers_seen` counter; tuner could fall back to status seq if
  marker missing. Defer until first observe-only soak.
- **Q-2.9-D.** Should `--commit-flash` survive Phase 2.9 at all?
  Once the workflow is operator-driven, flashing through the tuner
  becomes a hazard surface. Lean toward removal in Phase 2.10 once
  documented operator flow is proven.

---

**Cross-references:**
- `SYNC_REFACTOR_PLAN.md` — main plan; Phase 2.7 telemetry pipeline.
- `SYNC_REFACTOR_PHASE_2_8.md` — closed-loop live tuner implementation;
  superseded as the default workflow by Phase 2.9, retained as the
  explicit-flag debug path.
- `scripts/gcode_marker.py` — marker injection; in-place mode (slicer
  postproc API) added in commit `951973c`.
- `scripts/nosf_live_tuner.py` — observe-only default after 2.9.0;
  bucket KF and persistence layer reused.
- `scripts/nosf_logger.py` — CSV capture; primary input to analyzer
  for tunable computation.
- `scripts/nosf_analyze.py` — multi-run aggregator; produces final
  reviewed patch.

---

## 16. Phase 2.9 Addendum — Bootstrap + Long-Tail Continuous Tuning

> **Status:** PROPOSED extension. Builds on milestones 2.9.0-2.9.6.
> Adds support for the two-stage operator flow: short bootstrap from
> repeated calibration prints, then continuous accumulation from
> production prints that won't be repeated.

### 16.1 Operator flow refinement

```
Stage A — Bootstrap (1-2 days)
  - Pick 1-2 representative test models (small calibration cube,
    speed/feature variety).
  - Slice each through gcode_marker.py.
  - Run each model 2-3 times with tuner --observe.
  - First analyze + commit landed: minimum viable defaults.
  - Flash. NOSF can now operate standalone with reasonable defaults.

Stage B — Continuous accumulation (weeks/months)
  - Keep gcode_marker.py as default slicer post-process.
  - Tuner --observe-daemon runs in background between prints.
  - Each production print contributes single-run samples to buckets.
  - Some buckets are touched repeatedly; many are one-shot.
  - Periodically (operator decides, or recommendation triggers),
    re-run nosf_analyze and consider re-flashing.

Stage C — Re-tune (occasional)
  - When recommendation fires or operator wants refresh:
    nosf_analyze --since-commit --acceptance-gate
  - Diff suggested vs currently flashed defaults.
  - If meaningful drift, merge, regenerate, build, flash.
```

### 16.2 Dual-path lock criteria

Stage B has prints that visit a bucket exactly once. The 2.9.2 rule
`runs_seen ≥ 2` blocks those. Replace with **OR of two paths**:

A bucket transitions **STABLE → LOCKED** when EITHER hold:

**Path A — Bootstrap (multi-run consensus)**
- `n ≥ N_MIN_SAMPLES_CUMULATIVE` (200)
- `runs_seen ≥ N_MIN_RUNS` (2)
- `layers_seen ≥ N_MIN_LAYERS` (3)
- `cumulative_mid_s ≥ MIN_MID_TIME_S` (60)

**Path B — Single-print high-confidence**
- `n ≥ N_SINGLE_PRINT_SAMPLES` (500)
- `layers_seen ≥ N_SINGLE_PRINT_LAYERS` (5)
- `cumulative_mid_s ≥ MIN_MID_TIME_S` (60)
- (`runs_seen` may be 1)

Path B accepts a bucket when one print gives strong evidence: a
common feature/speed combo seen across many layers in a single tall
print. The higher sample count compensates for missing cross-run
consensus.

Constants added:
- `N_SINGLE_PRINT_SAMPLES = 500`
- `N_SINGLE_PRINT_LAYERS = 5`

`_bucket_wait_reason` reports the easier-to-satisfy path's blocker
(whichever has fewer remaining gaps).

### 16.3 Last-committed watermark

Add per-machine watermark to state file. Tracks which values were
last committed/flashed so analyzer can compute drift since commit.

Schema 2 → 3 (bump). New top-level field per machine:

```json
{
  "_schema": 3,
  "<machine-id>": {
    "_meta": {
      "last_commit_at": "2026-05-10T18:30:00Z",
      "last_commit_run_seq": 7,
      "last_commit_sample_total": 18234,
      "last_commit_values": {
        "baseline_rate": 1582,
        "sync_trailing_bias_frac": 0.342,
        "mid_creep_timeout_ms": 3800,
        "mid_creep_rate_sps_per_s": 5,
        "mid_creep_cap_frac": 12,
        "buf_variance_blend_frac": 0.500,
        "buf_variance_blend_ref_mm": 0.500
      }
    },
    "PERIMETER_v1650": { ... },
    ...
  }
}
```

Written by `nosf_analyze.py --commit-watermark` after operator
confirms a flash. No automatic write — operator-driven so the
watermark always reflects what's actually flashed, not what was
suggested.

Migration schema 2 → 3: add empty `_meta` block. No data loss.

### 16.4 Recommend-recheck heuristic

Tuner gains a `--recommend-recheck` mode that consults watermark
and prints YES/NO with reason:

```
$ python3 scripts/nosf_live_tuner.py --machine-id myprinter --recommend-recheck
[recommend-recheck] last commit: 2026-05-10T18:30:00Z (12 days ago)
[recommend-recheck] sample mass since commit: +4231 (23.2%)
[recommend-recheck] new LOCKED buckets since commit: 5
[recommend-recheck] flagged buckets (drift > 1σ from committed): 2
[recommend-recheck] RECOMMEND: yes — re-run analyze
```

Recommendation fires when ANY hold:
- New LOCKED buckets since last commit ≥ `RECHECK_NEW_LOCKED` (default 3)
- Sample mass increased ≥ `RECHECK_SAMPLE_PCT` since commit (default 25%)
- ≥ 1 LOCKED bucket has drifted ≥ 1σ from committed value (`drift_flag`)
- Calendar age since commit ≥ `RECHECK_AGE_DAYS` (default 30)

Pure read-only operation; no state mutation.

### 16.5 Stale bucket pruning

Buckets stale (no `last_seen` update for `STALE_AGE_DAYS`, default
60) are auto-excluded from analyzer aggregation. Not deleted from
state file (future re-prints may revive them); just flagged.

`--state-info --include-stale` shows them with `STALE` decoration.

`nosf_analyze.py` skips stale buckets when computing weighted means
unless `--include-stale` passed.

`--prune-stale` utility command on tuner physically removes stale
buckets. Operator-driven only.

### 16.6 Continuous-observe daemon mode

For Stage B, tuner needs to survive across prints. Behavior:

- `--observe-daemon` flag: tuner does NOT exit on `FINISH`. Instead:
  - on `FINISH`: persist state, log "print N complete, K samples
    added", reset per-print flags, wait for next `NT:START`.
  - on `NT:START`: increment `run_seq`, clear `_run_seen_labels`,
    log "print N+1 starting".
- Recommendation check fires automatically at end of each print.
  If `--recommend-recheck` triggers, prints reminder. Does not
  emit patch automatically.
- SIGTERM / SIGINT: persist + exit clean.

Pairs naturally with systemd unit or `nohup`-style background run.

### 16.7 Implementation milestones (addendum)

```
[ ] 2.9.8  dual-path lock criteria
[ ] 2.9.9  watermark + schema 3 migration
[ ] 2.9.10 recommend-recheck mode
[ ] 2.9.11 stale bucket handling + prune utility
[ ] 2.9.12 continuous-observe daemon mode
[ ] 2.9.13 documentation update for two-stage workflow
```

#### 2.9.8 — Dual-path lock criteria

**Files:** `scripts/nosf_live_tuner.py`, `scripts/test_nosf_live_tuner.py`

- Add constants `N_SINGLE_PRINT_SAMPLES = 500`,
  `N_SINGLE_PRINT_LAYERS = 5`.
- `_maybe_lock`: lock if Path A OR Path B satisfied.
- `_bucket_wait_reason`: report the closer-to-locking path's gap.
- Tests:
  - `test_single_print_path_locks` — n=500, layers=5, mid_s=60,
    runs_seen=1; verify LOCKED.
  - `test_neither_path_no_lock` — neither path satisfied; verify
    not LOCKED.
  - `test_either_path_no_double_count` — bucket meeting both paths
    locks once, doesn't oscillate.

#### 2.9.9 — Watermark + schema 3 migration

**Files:** `scripts/nosf_live_tuner.py`, `scripts/nosf_analyze.py`,
`scripts/test_nosf_live_tuner.py`

- Bump `SCHEMA_VERSION = 3`.
- Add `_meta` block per machine in JSON.
- `migrate_state_data` handles 2 → 3: insert empty `_meta`.
- Tuner: read-only access to `_meta` for diagnostics.
- Analyzer: `--commit-watermark` flag writes `_meta` after operator
  confirms patch was reviewed/applied. Operator runs:
  ```
  python3 scripts/nosf_analyze.py --in run*.csv \
      --state ~/nosf-state/buckets-myprinter.json \
      --commit-watermark
  ```
  This consumes the most recent `[nosf_review]` patch values and
  freezes them as `_meta.last_commit_values`. **Never written
  automatically.**
- Tests:
  - `test_schema2_to_3_migration` — pre-existing schema-2 file
    loads, persists as schema 3 with empty `_meta`.
  - `test_watermark_write_via_analyzer` — analyzer with
    `--commit-watermark` updates `_meta` correctly.

#### 2.9.10 — Recommend-recheck mode

**Files:** `scripts/nosf_live_tuner.py`, `scripts/test_nosf_live_tuner.py`

- Add `--recommend-recheck` argument.
- Implement `recommend_recheck(state_path, machine_id) -> tuple[bool, list[str]]`.
- Constants: `RECHECK_NEW_LOCKED = 3`, `RECHECK_SAMPLE_PCT = 25.0`,
  `RECHECK_AGE_DAYS = 30`.
- Drift flag: per-bucket, `abs(b.x - committed_value) > P_STABLE_THR^0.5`
  (i.e., > 1 standard deviation from committed).
- Tests:
  - `test_recommend_no_watermark` — no `_meta`; always recommends
    yes (cold state).
  - `test_recommend_below_thresholds` — small additions; recommends
    no.
  - `test_recommend_above_sample_threshold` — 30% sample mass
    increase; recommends yes.
  - `test_recommend_above_age_threshold` — 31 days old; yes.

#### 2.9.11 — Stale bucket handling + prune utility

**Files:** `scripts/nosf_live_tuner.py`, `scripts/nosf_analyze.py`,
`scripts/test_nosf_live_tuner.py`

- Constant `STALE_AGE_DAYS = 60`.
- Helper `is_stale(b: Bucket, now_wall: float) -> bool`.
- `print_state_info`: stale buckets shown with `STALE` decoration
  in `state` column.
- `--state-info --include-stale` includes them; default hides.
- `--prune-stale` command: removes buckets where `is_stale()` true.
  Persists. Reports count.
- Analyzer: weighted-mean computation excludes stale buckets unless
  `--include-stale` passed.
- Tests:
  - `test_stale_excluded_from_state_info`
  - `test_prune_stale_removes_correct_buckets`
  - `test_analyzer_excludes_stale_by_default`

#### 2.9.12 — Continuous-observe daemon mode

**Files:** `scripts/nosf_live_tuner.py`, `scripts/test_nosf_live_tuner.py`

- Add `--observe-daemon` flag. Default off.
- When set: `FINISH` does not trigger exit. Instead:
  - persist state
  - log "[daemon] print %d complete: +%d samples, %d locked total"
  - reset `seen_print_activity`, `finish_seen`, `idle_since`,
    `_run_seen_labels`, `_seen_layer_keys`
  - print recommend-recheck inline if it would fire
- SIGTERM/SIGINT handler persists then exits 0.
- `--commit-on-finish` and `--commit-flash` are mutually exclusive
  with `--observe-daemon`. Argparse rejects.
- Tests:
  - `test_daemon_does_not_exit_on_finish` — simulate FINISH,
    verify `daemon_should_continue == True`.
  - `test_daemon_resets_per_print_state` — FINISH then START,
    verify per-run flags reset.

#### 2.9.13 — Documentation update

**Files:** `MANUAL.md`, `KLIPPER.md`, `README.md`

- New subsection in MANUAL.md "Continuous tuning workflow":
  - Stage A bootstrap (3 calibration prints + first commit)
  - Stage B continuous (gcode_marker.py default + observe-daemon)
  - Stage C re-tune (recommend-recheck → analyze → review → flash)
- Klipper config snippet for slicer post-process default.
- README short flowchart showing both stages.
- TASK.md: append addendum status.

### 16.8 Acceptance criteria addendum

- Single-print bucket with n=600, layers=6, mid_s=80 reaches LOCKED
  via Path B in one print.
- Watermark write/read round-trip stable.
- `--recommend-recheck` exits 0 with verdict on stdout, no state
  mutation.
- `--observe-daemon` runs across 5 simulated print cycles without
  exiting; persistence stable each cycle.
- Stale bucket excluded from analyzer; visible with
  `--include-stale`.
- All existing 2.9.0-2.9.6 tests still pass after addendum work.

### 16.9 Rollback path addendum

- Schema 3 → 2 downgrade: not supported; delete state file or
  manually strip `_meta`.
- `--observe-daemon` reverts to one-shot mode by removing flag.
- Watermark misuse: `nosf_analyze.py --clear-watermark` resets
  `_meta` to empty without touching bucket data.
- Stale prune is destructive; operator-only, not auto.

### 16.10 Open questions (addendum)

- **Q-2.9-E.** Should daemon mode auto-run analyzer when recommend
  triggers, or just remind operator? Phase 2.9.12 chooses reminder
  (operator runs analyzer manually). Auto-run risks unreviewed
  config drift.
- **Q-2.9-F.** Should single-print Path B require evidence the
  print was non-trivial (e.g., total print duration > 30 min)?
  Defer; tighten if false-positives observed.
- **Q-2.9-G.** Should watermark be per-tunable (track which
  parameters were applied vs left at defaults)? Defer until
  partial-merge workflow proves common.

---

## 17. Resolved Open Questions (operator decisions)

All §15 and §16.10 questions were resolved interactively with the
maintainer. Table summarizes outcomes; sub-sections amend the spec
where decisions diverge from §1-§16.

| ID | Topic | Resolution | Implementation status |
|---|---|---|---|
| Q-2.9-A | TTY contention logger vs tuner | **Tuner embeds logger.** Tuner becomes single TTY consumer. Optional CSV emission to disk. `nosf_logger.py` deprecated. | ❌ pending (milestone 2.9.14) |
| Q-2.9-B | Learn `mid_creep_*` from data | **Compute, suggest only.** Analyzer derives values, prints in `[nosf_review]` with confidence. Operator decides whether to apply. | ✅ DONE (commit `b428e2c`, milestone 2.9.4) |
| Q-2.9-C | Per-layer markers default | **On by default.** `gcode_marker.py` injects `NT:LAYER:N` automatically. `--no-layer-markers` opt-out flag added. | ❌ pending (milestone 2.9.15) |
| Q-2.9-D | `--commit-flash` survival | **Remove entirely.** Flashing is operator-only via `gen_config.py + ninja + flash_nosf.sh`. Tuner never sends `SV:`. | ❌ pending (milestone 2.9.16) |
| Q-2.9-E | Daemon recheck action | **Just remind.** Daemon prints stderr message; operator runs analyzer manually. | ❌ pending (milestone 2.9.12) |
| Q-2.9-F | Path B print-duration gate | **Require print duration ≥ 5 min cumulative MID time across whole print.** Defends against trivial test prints. | ❌ pending (milestone 2.9.8) |
| Q-2.9-G | Watermark scope | **Per-tunable record.** `_meta.last_commit_values` is a dict of `{key: {value, applied_at, source}}`. Tracks which keys were actually applied. | ❌ pending (milestone 2.9.9) |

### 17.0 Phase 2.9 implementation snapshot

State of repo after commit `6ea2c28`:

| Milestone | Status | Commit |
|---|---|---|
| 2.9.0 observe-only default | ✅ DONE | `f7ab2ba` |
| 2.9.1 schema 2 bucket counters | ✅ DONE | `db35cec` |
| 2.9.2 cumulative lock criteria | ✅ DONE | `ae6745e` |
| 2.9.3 state-info diagnostics | ✅ DONE | `1d954cc` |
| 2.9.4 analyzer + acceptance gate | ✅ DONE | `b428e2c` |
| 2.9.5 review-only patch | ✅ DONE | `4e9db9f` |
| 2.9.6 documentation | ✅ DONE | `e7c1163` |
| 2.9.7 firmware cleanup (LIVE_TUNE_LOCK) | ⏸️ deferred | per maintainer; needs ≥5 real-print soaks |
| 2.9.8 dual-path lock + Path B 5-min gate | ❌ TODO | implements §16.2 + §17.1.4 |
| 2.9.9 watermark + schema 3 | ❌ TODO | per-tunable per §17.1.5 |
| 2.9.10 recommend-recheck | ❌ TODO | reads watermark from 2.9.9 |
| 2.9.11 stale bucket handling | ❌ TODO | §16.5 |
| 2.9.12 observe-daemon mode | ❌ TODO | §16.6 + §17 Q-E reminder-only |
| 2.9.13 documentation update | ❌ TODO | §16.7 + §17 doc deltas |
| 2.9.14 embedded logger CSV | ❌ TODO | §17.1.2 |
| 2.9.15 gcode_marker default layer markers | ❌ TODO | §17.1.3 |
| 2.9.16 remove --commit-flash | ❌ TODO | §17.1.1 |

Open work begins at milestone **2.9.16** per §17.3 landing order
(surface-area cleanup before new feature work).

### 17.1 Spec amendments — supersedes prior sections

#### 17.1.1 Tuner mode matrix (supersedes §6)

`--commit-flash` removed. Patch emission no longer touches the
serial port for `SV:`.

| Mode | Reads markers | Reads status | Bucket KF | JSON persist | CSV out (if `--csv-out`) | `SET:LIVE_TUNE_LOCK:1` | `SET:TRAIL_BIAS_FRAC` | `SET:BASELINE_SPS` | `SV:` |
|---|---|---|---|---|---|---|---|---|---|
| `--observe` (default) | yes | yes | yes | yes | yes | no | no | no | no |
| `--allow-bias-writes` | yes | yes | yes | yes | yes | yes | yes (rate-limited) | no | no |
| `--allow-baseline-writes` | yes | yes | yes | yes | yes | yes | no | yes (rate-limited) | no |

**No row for `SV:` writes.** Operator-only flashing path.

#### 17.1.2 Embedded logger (Q-2.9-A resolution)

`nosf_live_tuner.py` becomes the sole TTY owner. New flag:
- `--csv-out PATH` — append per-status-row CSV alongside JSON state.
  Format compatible with `nosf_analyze.py` input. Ignored when
  empty / not passed.
- CSV row schema: `wall_ts, run_seq, layer, feature, v_fil, BL, BP, BPV, EST, RT, AD, APX, CF, TC, BUF, MK_seq`.
- File opened append-mode; rotates only on operator intervention.
- `--csv-out` defaults OFF; operator opts in for analyzer corpus.

`scripts/nosf_logger.py` marked deprecated in 2.9.13 docs. Kept
as-is for backward compatibility one phase; consider removal in
Phase 2.10.

#### 17.1.3 `gcode_marker.py` per-layer default (Q-2.9-C resolution)

Default behavior changed:
- `--every-layer` becomes the default. New `--no-layer-markers` flag
  to suppress for production prints that don't want extra G-code.
- Existing `--every-layer` flag becomes a no-op (still accepted for
  backward compatibility); print warning that it's now default.
- Module docstring + README documentation updated.

#### 17.1.4 Path B print-duration gate (Q-2.9-F resolution)

Path B (single-print high-confidence) gains an additional
constraint. Bucket transitions LOCKED via Path B only when ALL of:
- `n ≥ N_SINGLE_PRINT_SAMPLES` (500)
- `layers_seen ≥ N_SINGLE_PRINT_LAYERS` (5)
- `cumulative_mid_s ≥ MIN_MID_TIME_S` (60) (per bucket)
- **`total_print_mid_s ≥ MIN_PRINT_MID_S` (300)** — total MID time
  across the entire print, not just this bucket. Prevents lock-up
  from short single-feature spam tests.

`total_print_mid_s` is a tuner-level counter, reset on `NT:START`.
Persisted in `_meta` for the current run only; cleared at next
START.

#### 17.1.5 Watermark per-tunable (Q-2.9-G resolution, supersedes §16.3)

Schema 2 → 3 migration changes structure of `_meta`:

```json
{
  "_schema": 3,
  "<machine-id>": {
    "_meta": {
      "last_commit_values": {
        "baseline_rate":             {"value": 1582,  "applied_at": "2026-05-10T18:30:00Z", "source": "config.ini"},
        "sync_trailing_bias_frac":   {"value": 0.342, "applied_at": "2026-05-10T18:30:00Z", "source": "config.ini"},
        "mid_creep_timeout_ms":      {"value": 4000,  "applied_at": null,                    "source": "default"},
        "mid_creep_rate_sps_per_s":  {"value": 5,     "applied_at": null,                    "source": "default"},
        "mid_creep_cap_frac":        {"value": 10,    "applied_at": null,                    "source": "default"},
        "buf_variance_blend_frac":   {"value": 0.5,   "applied_at": null,                    "source": "default"},
        "buf_variance_blend_ref_mm": {"value": 1.0,   "applied_at": null,                    "source": "default"}
      },
      "last_commit_run_seq": 7,
      "last_commit_sample_total": 18234
    },
    "<bucket_label>": { ... }
  }
}
```

`source` field values:
- `"config.ini"` — operator merged from analyzer patch.
- `"default"` — value left at firmware default, never tuned.
- `"manual"` — operator hand-set value, not from analyzer.

`nosf_analyze.py --commit-watermark` accepts a `--keys` argument
(comma-separated list of keys actually merged). Only those keys
get `applied_at` updated. Others stay at previous state.

Drift detection (recommend-recheck): only fires for keys whose
`source == "config.ini"` (i.e., previously committed). Keys at
`default` or `manual` are skipped.

### 17.1.6 Schema migration chain — MANDATORY for 2.9.9

Current `migrate_state_data` (post-2.9.1) only handles `1 → 2`
because the `if schema == 1 and SCHEMA_VERSION == 2` branch hard-
codes the destination version. After bumping `SCHEMA_VERSION = 3`
without rewriting this helper, existing schema-2 state files will
FAIL to load and the maintainer will lose their accumulated tune
database. **Unacceptable.**

Milestone 2.9.9 MUST rewrite `migrate_state_data` as an explicit
chain of one-step migrations, executed in order until the data
matches the current `SCHEMA_VERSION`:

```python
def _migrate_1_to_2(data: dict) -> dict:
    # Schema 1 had no per-bucket counters. Bucket loader already
    # zero-fills the new fields via .get(default) calls, so the
    # only required mutation is the version bump.
    data["_schema"] = 2
    return data


def _migrate_2_to_3(data: dict) -> dict:
    # Schema 3 introduces _meta block with per-tunable watermark.
    # Insert empty _meta on every machine-id key, leaving bucket
    # data untouched.
    for machine_id, machine_data in data.items():
        if machine_id.startswith("_"):
            continue
        if not isinstance(machine_data, dict):
            continue
        meta = machine_data.setdefault("_meta", {})
        meta.setdefault("last_commit_values", {
            key: {"value": None, "applied_at": None, "source": "default"}
            for key in PATCH_KEYS
        })
        meta.setdefault("last_commit_run_seq", 0)
        meta.setdefault("last_commit_sample_total", 0)
    data["_schema"] = 3
    return data


_MIGRATIONS = {
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
}


def migrate_state_data(data: dict) -> dict:
    schema = data.get("_schema")
    if not isinstance(schema, int):
        raise ValueError(f"state file schema mismatch: got {schema!r}, expected int")
    if schema > SCHEMA_VERSION:
        raise ValueError(f"state file schema {schema} is newer than supported {SCHEMA_VERSION}")
    while schema < SCHEMA_VERSION:
        if schema not in _MIGRATIONS:
            raise ValueError(f"missing migration step from schema {schema}")
        data = _MIGRATIONS[schema](data)
        schema = data["_schema"]
    return data
```

Hard requirements for 2.9.9 implementation:
- All existing schema-2 state files load without data loss. Bucket
  records (x, P, n, bias, runs_seen, layers_seen, cumulative_mid_s,
  etc.) preserved byte-for-byte after migration.
- LOCKED buckets stay LOCKED across migration. No re-validation.
- The `_meta` block initial values for `last_commit_values`:
  `value: None`, `applied_at: None`, `source: "default"`. The
  analyzer fills real values when the operator runs
  `--commit-watermark`.
- Future schema bumps register a new `_migrate_N_to_N+1` and add
  to `_MIGRATIONS`. No more conditional branches keyed on
  `SCHEMA_VERSION`.

Required tests in `scripts/test_nosf_live_tuner.py`:
- `test_schema2_to_3_migration_preserves_buckets` — write a
  schema-2 file with at least 3 buckets including one LOCKED, one
  TRACKING with high `n`, and one with non-zero `runs_seen` /
  `layers_seen` / `cumulative_mid_s`. Load. Verify:
  1. All bucket fields equal their pre-load values.
  2. Each bucket's `state` and `locked` flags unchanged.
  3. `_meta` block created with all `PATCH_KEYS` entries at
     `source="default"`, `applied_at=None`.
  4. After `_persist`, file is schema 3 and re-load is idempotent.
- `test_schema_chain_1_to_3` — write a schema-1 file (legacy
  format), load. Verify it migrates 1 → 2 → 3 in one load and
  bucket data preserved through both hops.
- `test_schema_too_new_refused` — write a file with
  `_schema: 99`. Load must raise; no mutation; no implicit
  downgrade.
- `test_existing_production_state_loads` — load a snapshot of an
  actual operator state file (committed under `tests/fixtures/`
  if maintainer provides one) without errors. Skipped if fixture
  missing.

Operator safety net (not code; documentation in 2.9.13):
- Before running 2.9.9 the first time, operator should
  `cp ~/nosf-state/buckets-<id>.json ~/nosf-state/buckets-<id>.json.schema2.bak`.
- 2.9.13 docs MUST mention this.

### 17.2 New milestones (from resolutions)

```
[ ] 2.9.14 embedded logger CSV emission (--csv-out)
[ ] 2.9.15 gcode_marker.py default --every-layer (Q-2.9-C)
[ ] 2.9.16 remove --commit-flash + SV: code path (Q-2.9-D)
```

#### 2.9.14 — Embedded logger CSV emission

**Files:** `scripts/nosf_live_tuner.py`, `scripts/test_nosf_live_tuner.py`

Changes:
- Add `--csv-out PATH` argument.
- New helper `CsvEmitter(path)`: opens append, writes header on
  empty file, writes one row per `on_status` call.
- Hook into existing `on_status` after parse but before bucket
  update (so even gated rows are captured for analyzer).
- Row schema per §17.1.2.
- Persist on SIGTERM/SIGINT.

Tests:
- `test_csv_out_writes_rows` — verify N status calls produce N+1
  lines (header + rows).
- `test_csv_out_appends_across_runs` — second tuner instance with
  same path appends, header not duplicated.

#### 2.9.15 — gcode_marker.py default --every-layer

**Files:** `scripts/gcode_marker.py`, `scripts/test_*` if any,
`MANUAL.md`, `KLIPPER.md`.

Changes:
- Default `every_layer=True` in `process_gcode`.
- Argparse: replace `--every-layer` with `--no-layer-markers`
  (store_false). Keep `--every-layer` as deprecated alias that
  prints a warning.
- Update docstring + README.

Tests: existing test_nosf_live_tuner.py marker handling unchanged.
No new tests required; docstring + manual smoke test sufficient.

#### 2.9.16 — Remove --commit-flash

**Files:** `scripts/nosf_live_tuner.py`, `scripts/test_nosf_live_tuner.py`

Changes:
- Remove `--commit-flash` argument.
- Remove `SET:LIVE_TUNE_LOCK:0` / `time.sleep(0.1)` / `SV:` calls
  from commit path. `finish_commit()` now writes patch only.
- Remove `--commit-flash` from argparse mutex group with
  `--observe-daemon`.
- Update docstring (§17.1.1 mode matrix).
- Remove `test_commit_flash_invokes_sv` (or rewrite as
  `test_no_sv_in_finish_commit`).

Tests: replace `test_commit_flash_invokes_sv` →
`test_finish_commit_emits_patch_no_sv` that verifies patch file
written and `SV:` is NOT in `fake.writes`.

### 17.3 Updated milestone order

Recommended landing order:
1. 2.9.16 first (remove --commit-flash) — simplifies surface area
   before adding new flags.
2. 2.9.15 (gcode_marker default layers) — required for Path B
   layer counter accuracy.
3. 2.9.14 (CSV out) — provides analyzer corpus for upcoming work.
4. 2.9.8 (dual-path lock) — incorporates 17.1.4 print-duration gate.
5. 2.9.9 (watermark) — uses 17.1.5 per-tunable structure.
6. 2.9.10 (recommend-recheck) — uses watermark from 2.9.9.
7. 2.9.11 (stale prune).
8. 2.9.12 (observe-daemon).
9. 2.9.13 (docs) — covers all preceding.

### 17.4 Updated acceptance criteria

In addition to §13 and §16.8:
- `--csv-out` produces CSV consumable by `nosf_analyze.py`
  without changes to analyzer input parsing.
- `nosf_logger.py` deprecation message shown on invocation;
  workflow examples in MANUAL.md and KLIPPER.md use only
  `nosf_live_tuner.py --csv-out`.
- `gcode_marker.py` without flags produces output containing
  per-layer `NT:LAYER:N` markers (verify via grep).
- Path B does not lock buckets in a 2-minute synthetic test
  print; locks them in a 6-minute print with same per-bucket
  evidence.
- Watermark `--commit-watermark --keys baseline_rate` updates
  only `baseline_rate.applied_at`; other keys retain their
  prior `source`.

