# Sync Refactor — Phase 2.11

> **Phase 2.11 — Smarter bucket lock/unlock**
>
> Phase 2.10 Klipper motion tracking replaced shell-marker calibration
> and delivered a denser, more accurate sample stream. The new stream
> exposed a long-standing weakness in the live tuner: a single residual
> outlier unlocks an otherwise healthy bucket. Phase 2.11 hardens the
> lock/unlock algorithm against single-sample outliers without
> weakening the lock criteria, without changing the firmware, and
> without breaking the Phase 2.9 observe-only contract or the Phase
> 2.10 sidecar+UDS marker flow.
>
> This document is a **plan, not an implementation**. No code lands as
> part of this commit. Implementation begins at milestone 2.11.1 after
> the maintainer reads this file.

---

## 0. Decision addendum

| ID | Decision | Effect |
|---|---|---|
| K-1 | Host-side only. No firmware change, no `settings_t` change, no `SETTINGS_VERSION` bump. | Plan is pure tuner work. |
| K-2 | Pure stdlib + `pyserial` only. No `numpy`, `scipy`, `pandas`. | Residual statistics use scalar EWMA, not arrays. |
| K-3 | Observe-only default preserved. `--allow-bias-writes` and `--allow-baseline-writes` still gate the only live SETs. | No regression to Phase 2.9. |
| K-4 | `on_m118` ingress contract preserved byte-for-byte. SegmentMatcher emits the same strings. | No regression to Phase 2.10. |
| K-5 | `--recommend-recheck` stays read-only. `--prune-stale` stays operator-only. `--observe-daemon` does not auto-run analyzer. | No silent state mutation. |
| K-6 | State schema bumps from 3 → 4. Chained migration via existing `_MIGRATIONS` registry (§17.1.6 of Phase 2.9). | Existing schema-3 state survives intact. |
| K-7 | State remains compact. No unbounded per-sample histories. Per-bucket additions are scalars. | State file size grows by O(buckets), not O(samples). |
| K-8 | All new thresholds are named constants at module top, conservative initial values, documented. | Operator can tune by editing constants if 2.11.5 Pi soak proves too tight or too loose. |
| K-9 | Existing tests must continue to pass. New tests fix the regression and lock in the new behavior. | No test loosening. |

---

## 1. Current algorithm map

### 1.1 Bucket state fields (Phase 2.9/2.10 snapshot)

`scripts/nosf_live_tuner.py:125-148` — `@dataclass Bucket`:

```python
label, x, P, n, bias, bp_ewma, state, last_set_x, last_set_bias,
first_seen, last_seen, stable_since, locked, last_debug_t,
last_debug_state, runs_seen, layers_seen, cumulative_mid_s,
low_flow_skip_count, rail_skip_count, rollback_count, first_seen_run
```

State machine: `TRACKING → STABLE → LOCKED → TRACKING (on outlier)`.

### 1.2 Kalman update (line 174-180)

```python
def kf_predict_update(bucket, z, cf, apx, dt_s):
    bucket.P += Q_PROCESS * max(dt_s, 0.0)        # Q_PROCESS = 25.0
    R = (R_BASE / max(cf, 0.05)) * (1.0 + apx / float(APX_THR))
    K = bucket.P / (bucket.P + R)
    bucket.x += K * (z - bucket.x)
    bucket.P *= 1.0 - K
    bucket.n += 1
```

`R_BASE = 100.0`. Once `P` settles, gain `K` collapses; `x` essentially
freezes. Residual `(z - x)` is **not** persisted anywhere — every
sample is forgotten immediately.

### 1.3 Lock gate (`_maybe_lock`, line 680-710)

Path A (multi-run): `n ≥ 200`, `runs_seen ≥ 2`, `layers_seen ≥ 3`,
`cumulative_mid_s ≥ 60`.

Path B (single high-confidence print): `n ≥ 500`, `layers_seen ≥ 5`,
`cumulative_mid_s ≥ 60`, `total_print_mid_s ≥ 300`.

Both require `P < P_STABLE_THR (100.0)` AND bias inside
`[BIAS_SAFE_MIN, BIAS_SAFE_MAX]`. **No noise/variance check.**

Bucket moves `TRACKING → STABLE` on first satisfaction of the variance
gate; `STABLE → LOCKED` on next sample where either path is full.

### 1.4 Unlock gate (line 591-602)

```python
if b.state == "LOCKED" or b.locked:
    if abs(est - b.x) * abs(est - b.x) > 4.0 * (b.P + R_BASE):
        b.state = "TRACKING"
        b.locked = False
        b.P = max(b.P, 1e4)
    self._debug_bucket_progress(b, now, est, bp, cf, apx)
    return
```

Threshold radius: `sqrt(4 · (P + R_BASE))`. With `P ≈ 35`, `R_BASE = 100`,
radius ≈ **23 sps**. A single sample further than 23 sps from `x`
immediately unlocks. P then jumps to `1e4`, gate falls below
`P_STABLE_THR` after a few samples, and the bucket re-locks. **Chatter.**

### 1.5 Debug output (`_debug_bucket_progress`, line 616-630)

Prints `n, P, x, est, bias, bp, cf, apx, state, wait` every
`progress_interval` seconds OR on `state` change. Source of the
LOCKED / unlock outlier / TRACKING transitions observed in the field
evidence.

### 1.6 `state-info` wait derivation (`bucket_wait_reason`, line 851-874)

Reports the easier-to-satisfy path's blocker. Possible values:
`locked`, `variance P>=100`, `bias rail guard`,
`samples N/M`, `runs N/M`, `layers N/M`, `mid_time N/M s`,
`total_mid N/M s`, `stable`. **No noise reason.**

---

## 2. Root-cause assessment

### 2.1 Field evidence (operator print, May 2026)

```
TOTAL: 115 buckets, 1 locked, 9423 samples, 1475.3s MID
Outer wall_v1375  951 36.2 976 0.298 LOCKED 1 172 193.9 32 locked

[tuner] bucket Outer wall_v1375 n=500 P=16.4 x=567 est=524 ...
[tuner] bucket Outer wall_v1375 unlock outlier est=524 x=567 P=16.4
[tuner] bucket Outer wall_v1375 n=500 P=10000.0 x=567 est=524 ... state=TRACKING wait=variance P>=100

[tuner] bucket Outer wall_v1375 n=503 P=35.4 x=419 est=418 state=LOCKED wait=locked
[tuner] bucket Outer wall_v1375 unlock outlier est=555 x=419 P=35.4
[tuner] bucket Outer wall_v1375 n=509 P=35.8 x=524 est=603 state=LOCKED wait=locked
[tuner] bucket Outer wall_v1375 unlock outlier est=610 x=524 P=35.8
[tuner] bucket Outer wall_v1375 n=545 P=35.6 x=354 est=358 state=LOCKED wait=locked
[tuner] bucket Outer wall_v1375 unlock outlier est=559 x=354 P=35.6
```

Same bucket cycled through LOCKED → TRACKING → LOCKED at least three
times in one print. `x` walked across `567 → 419 → 524 → 354`.

### 2.2 Quantitative cause

Threshold formula collapses with `P`. At `P = 35`:
`radius = sqrt(4·135) ≈ 23 sps`.

Field residuals on locked buckets exceed 100 sps regularly. These are
not measurement faults; they are honest scatter from neighboring
extrusion conditions inside the 25 mm³/s `v_fil_bin`. Speed factor
and extrude factor multipliers (Phase 2.10) amplify per-segment
variation that the Kalman R model does not see.

After unlock, `P` resets to `1e4`. The bucket re-converges to a
**different** centroid because the next 200 samples cluster wherever
the print is currently extruding. The state machine therefore does
not learn; it samples.

### 2.3 Why now and not in Phase 2.8/2.9

Phase 2.8 ran on shell-marker injection. Each `RUN_SHELL_COMMAND`
forked Python and stalled the gcode queue 20-60 ms. Sample rate was
lower, sample-to-marker correlation was looser, and `est` values
were averaged over the stall window. The unlock threshold mostly
fired only on real regime changes.

Phase 2.10 sidecar+UDS removed the stall, sharpened the per-segment
v_fil estimate, and increased sample density by ~3-5×. The bug was
present all along; Phase 2.10 made it observable.

### 2.4 Three failure modes to fix

1. **Single-sample outlier** unlocks a healthy bucket.
2. **Noisy bucket that should stay STABLE** locks on a lucky window,
   then chatters forever.
3. **True regime change** (real drift) is currently detected only by
   the same single-sample test, so the fix must not eliminate
   detection of legitimate drift.

### 2.5 Out-of-scope hypotheses

- Bucket bin width (25 mm³/s) is **not** the primary cause. Widening
  bins would hide noise but reduce resolution; Phase 2.11 instead
  measures noise per bucket and uses it.
- Process noise `Q_PROCESS` could be tuned higher, but the
  field-observed scatter is largely measurement variance, not drift,
  so `Q_PROCESS` is left at 25.0.
- Kalman R modeling could be made more sophisticated (per-segment R
  from sidecar feedrate), but this is a tuner-internal cleanup
  Phase 2.11 chooses to defer.

---

## 3. Proposed algorithm

### 3.1 New bucket fields

Added to `@dataclass Bucket`:

```python
resid_ewma: float = 0.0            # signed EWMA of residual (z - x)
resid_abs_ewma: float = 0.0        # EWMA of |residual|
resid_var_ewma: float = R_BASE     # EWMA of residual^2 (scatter); warm-start at R_BASE
outlier_streak: int = 0            # consecutive moderate outliers since lock or last reset
locked_sample_count: int = 0       # samples consumed while in LOCKED state
locked_since_run_seq: int = 0      # run_seq when bucket entered LOCKED most recently
last_unlock_reason: str = ""       # "catastrophic", "drift", "streak", ""
last_unlock_at: float = 0.0        # wall-clock of last unlock
```

All scalar. No history arrays. State file growth is O(buckets) per
machine.

### 3.2 Per-sample residual statistics update

Every accepted MID sample (after KF predict/update) appends:

```python
resid = z - b.x
b.resid_ewma     = (1 - A_R) * b.resid_ewma     + A_R * resid
b.resid_abs_ewma = (1 - A_R) * b.resid_abs_ewma + A_R * abs(resid)
b.resid_var_ewma = (1 - A_V) * b.resid_var_ewma + A_V * (resid * resid)
```

Constants:
```python
A_R = 0.05    # ~ 20-sample memory
A_V = 0.05
```

Note: `resid` is computed against `b.x` AFTER the KF update — i.e.,
post-correction residual. This biases statistics low for the current
sample but produces a meaningful steady-state when `K` is small (the
locked regime), which is where the unlock decision matters.

### 3.3 Effective noise scale

```python
def _resid_sigma(b):
    return sqrt(max(b.resid_var_ewma, R_BASE))
```

`R_BASE` floor prevents single-sample lockup at zero scatter on
warm-start. Returns the bucket's observed standard deviation of `est`
around the converged mean.

### 3.4 Lock-side amendment

`_maybe_lock` keeps Phase 2.9 dual-path criteria intact, with two
additional gates between `STABLE` and `LOCKED`:

```python
noise_ok = b.resid_var_ewma <= V_NOISE_LOCK_THR
warm = b.n >= N_WARMUP_FOR_NOISE
```

`V_NOISE_LOCK_THR = 4 * R_BASE` (= 400 sps²; σ ≈ 20 sps).
`N_WARMUP_FOR_NOISE = 50` so `resid_var_ewma` is stable before it
gates locking.

A bucket whose post-warmup `resid_var_ewma` exceeds the threshold
stays at `STABLE`. It contributes to analyzer aggregation but does
not lock and does not chatter. `wait` reason becomes
`noise (σ²=σ²_obs > σ²_thr)`.

This is the **"naturally noisy bucket should not lock yet"** outcome
required by §Design Goal.

### 3.5 Unlock-side rewrite

Replace the single-sample test (line 591-602) with a **three-channel
unlock detector**:

#### 3.5.1 Channel C — Catastrophic

```python
def _is_catastrophic(b, resid):
    sigma = max(_resid_sigma(b), R_BASE ** 0.5)   # ≥ 10 sps
    return abs(resid) > K_CATA * sigma             # K_CATA = 8.0
```

A single sample with `|residual| > 8σ` (where σ is the bucket's
observed scatter, floored at 10 sps) is treated as a real fault and
unlocks immediately. This handles operator manually changing
filament, drive-train slip, or a sensor failure mid-print.

#### 3.5.2 Channel S — Sustained outlier streak

```python
threshold_S = K_STREAK_SIGMA * sigma     # K_STREAK_SIGMA = 3.0
if abs(resid) > threshold_S:
    b.outlier_streak += 1
else:
    b.outlier_streak = max(0, b.outlier_streak - 1)
if b.outlier_streak >= N_STREAK:           # N_STREAK = 5
    unlock(reason="streak")
```

Each moderate outlier (>3σ) increments the streak; each inlier
decrements (not resets — allows occasional inliers in the middle of
a real drift to still trigger unlock after N consecutive outliers).
Five consecutive outliers without intervening recovery is the
unlock criterion. With `A_R = 0.05`, the EWMA window is ~20 samples,
so 5 sustained outliers represents a real shift, not honest scatter.

#### 3.5.3 Channel D — Sustained mean drift

```python
drift_sigma = sigma / sqrt(EWMA_EFFECTIVE_N)         # = sigma / sqrt(1/A_R - 1) ≈ sigma / 4.4
if abs(b.resid_ewma) > K_DRIFT * drift_sigma:        # K_DRIFT = 4.0
    if b.locked_sample_count >= M_DRIFT_DWELL:        # = 30
        unlock(reason="drift")
```

`resid_ewma` is the post-lock signed mean of `(z - x)`. If it
persistently deviates from zero by more than 4 standard errors of
the EWMA mean, the lock is wrong. Effective sample count
`1/A_R - 1 ≈ 19`, so `drift_sigma ≈ σ / 4.4`. The `M_DRIFT_DWELL`
gate ensures at least 30 post-lock samples are observed before this
channel can fire, so a freshly-locked bucket cannot drift-unlock
before the mean estimate is meaningful.

#### 3.5.4 Channel order and minimum dwell

```python
def _evaluate_unlock(b, resid):
    if _is_catastrophic(b, resid):
        return "catastrophic"
    if b.locked_sample_count < MIN_LOCK_DWELL:        # 20
        return ""                                      # only catastrophic allowed during dwell
    if b.outlier_streak >= N_STREAK:
        return "streak"
    if _is_drift(b):
        return "drift"
    return ""
```

`MIN_LOCK_DWELL = 20` samples blocks moderate-channel unlocks until
the bucket has accumulated enough post-lock evidence. Catastrophic
unlock bypasses dwell — true faults must surface immediately.

#### 3.5.5 Unlock side-effects

On unlock:
- `state = "TRACKING"`, `locked = False`
- `P = max(P, P_UNLOCK_RESET)`. Use **`P_UNLOCK_RESET = 4 * P_STABLE_THR`** (= 400),
  **not 1e4** as today. The bucket has converged once; the new gate
  should require re-stabilization but not a full cold restart.
- `outlier_streak = 0`
- `resid_var_ewma = max(resid_var_ewma, R_BASE * 2)` — slight bump so
  the immediate next lock attempt does not happen on a single sample
- `last_unlock_reason = reason`
- `last_unlock_at = wall_fn()`
- `locked_sample_count = 0`

Critically: do **not** reset `resid_var_ewma` to zero. The bucket's
historical noise estimate is the most valuable thing the unlock
detector learned, and discarding it would re-arm chatter.

### 3.6 Interaction with Phase 2.9 Path A / Path B

Path A and Path B remain the lock gates **before** a bucket has ever
been locked. After Phase 2.11, both paths additionally require:

```python
noise_ok = b.n < N_WARMUP_FOR_NOISE or b.resid_var_ewma <= V_NOISE_LOCK_THR
```

i.e., during warmup the noise gate is bypassed; after warmup the
bucket must have observed scatter below threshold.

A bucket that has been LOCKED and then unlocked re-uses the same
paths to re-lock, but `resid_var_ewma` is now meaningful from
day one of re-tracking, so a chatter-prone bucket will fail
`noise_ok` on the second attempt and remain `STABLE`.

### 3.7 New constants (initial conservative values)

```python
# Residual statistics
A_R = 0.05                  # residual EWMA alpha
A_V = 0.05                  # residual variance EWMA alpha
N_WARMUP_FOR_NOISE = 50     # samples before noise gate activates

# Lock-side
V_NOISE_LOCK_THR = 400.0    # sps^2; σ ≈ 20 sps allowed for lock

# Unlock channels
K_CATA = 8.0                # catastrophic |residual| / σ
K_STREAK_SIGMA = 3.0        # moderate-outlier threshold in σ
N_STREAK = 5                # consecutive moderate outliers
K_DRIFT = 4.0               # drift mean / drift-sigma threshold
EWMA_EFFECTIVE_N = 19.0     # = 1/A_R - 1
MIN_LOCK_DWELL = 20         # samples
M_DRIFT_DWELL = 30          # samples before drift channel allowed

# Unlock side-effects
P_UNLOCK_RESET = 400.0
```

All constants live at the top of `nosf_live_tuner.py` next to the
existing `Q_PROCESS / R_BASE` block. Each gets a one-line comment.

### 3.8 Drift / noisy-bucket distinction

| Symptom | Channel | Outcome |
|---|---|---|
| Normal scatter (\|resid\| < 3σ) | none | no streak increment, no unlock |
| Occasional 3σ outlier among inliers | S decrements | streak stays low, no unlock |
| 5 consecutive 3σ outliers | S | unlock, reason `streak` |
| Sustained bias drift: many 1-2σ residuals all same sign | D | resid_ewma grows; unlock after dwell |
| Single 8σ event | C | unlock immediately |
| High `resid_var_ewma` from cold start | lock-side gate | bucket stays STABLE; no chatter; wait=noise |

---

## 4. State schema migration (schema 3 → 4)

### 4.1 Migration step

Add `_migrate_3_to_4` to the existing `_MIGRATIONS` registry per
§17.1.6 of Phase 2.9. Required structure:

```python
def _migrate_3_to_4(data: dict) -> dict:
    for machine_id, machine_data in data.items():
        if machine_id.startswith("_"):
            continue
        if not isinstance(machine_data, dict):
            continue
        for label, raw in machine_data.items():
            if label.startswith("_"):
                continue
            if not isinstance(raw, dict):
                continue
            raw.setdefault("resid_ewma", 0.0)
            raw.setdefault("resid_abs_ewma", 0.0)
            raw.setdefault("resid_var_ewma", R_BASE)
            raw.setdefault("outlier_streak", 0)
            raw.setdefault("locked_sample_count", 0)
            raw.setdefault("locked_since_run_seq", 0)
            raw.setdefault("last_unlock_reason", "")
            raw.setdefault("last_unlock_at", 0.0)
    data["_schema"] = 4
    return data

_MIGRATIONS[3] = _migrate_3_to_4
```

`SCHEMA_VERSION = 4` after this milestone.

### 4.2 Default values rationale

- `resid_var_ewma = R_BASE` (not 0) so the first 50 samples cannot
  cause spurious unlock attempts; floor matches `_resid_sigma`.
- `resid_ewma = 0` and `resid_abs_ewma = 0` — they re-converge fast.
- `locked_sample_count = 0` for buckets that were already LOCKED.
  This means a migrated LOCKED bucket gets a fresh `MIN_LOCK_DWELL`
  window. This is **intentional**: it gives the new algorithm room
  to observe before any unlock channel can fire, so day-one of
  schema 4 cannot be worse than day-zero.
- `last_unlock_reason = ""` and `last_unlock_at = 0.0`.

### 4.3 Bucket loader / persister updates

- `_load_state` reads all new fields from raw with safe defaults.
- `_persist` writes them in deterministic key order.
- `bucket_from_raw` (used by `state-info`) also reads them so
  diagnostic output sees real values.

### 4.4 Required tests

In `scripts/test_nosf_live_tuner.py`:

- `test_schema3_to_4_migration_preserves_buckets` — write a schema-3
  state file with one LOCKED + one TRACKING + one STABLE bucket;
  load via `migrate_state_data`. Assert:
  1. All schema-3 fields preserved byte-for-byte.
  2. All schema-4 fields populated with safe defaults.
  3. `LOCKED` status unchanged.
  4. `_meta` block untouched.
  5. Re-persist; reload; idempotent.
- `test_schema_chain_2_to_4` — write a schema-2 file, load; ensure
  it chains 2→3→4 with all fields populated.
- `test_schema_chain_1_to_4` — same from schema 1.
- `test_existing_production_state_loads_schema3_to_4` — if maintainer
  commits a real-state fixture under `tests/fixtures/`, exercise it;
  skipped if missing.

### 4.5 `--state-info` output

Default table unchanged. Add `--state-info --verbose` that appends
columns:

```
σ²    streak  dwell  last_unlock
```

(reads `resid_var_ewma`, `outlier_streak`, `locked_sample_count`,
`last_unlock_reason`).

CSV mode (`--state-info --csv`) gains columns
`resid_var_ewma,outlier_streak,locked_sample_count,last_unlock_reason`.
These extend the existing CSV header by appending; consumers that
read by name (analyzer) continue to work; consumers that read by
position (none today) are flagged in the docs section.

### 4.6 `wait` reason additions

Extend `bucket_wait_reason`:

```python
if b.state in ("TRACKING", "STABLE"):
    if b.n >= N_WARMUP_FOR_NOISE and b.resid_var_ewma > V_NOISE_LOCK_THR:
        return f"noise σ²={b.resid_var_ewma:.0f}>{int(V_NOISE_LOCK_THR)}"
if b.state == "LOCKED" and b.locked_sample_count < MIN_LOCK_DWELL:
    return f"dwell {b.locked_sample_count}/{MIN_LOCK_DWELL}"
```

These slot in before the existing reasons; the existing reasons are
unchanged otherwise.

---

## 5. Test plan

### 5.1 Algorithm unit tests (extend `scripts/test_nosf_live_tuner.py`)

Each test constructs a `Tuner` with a fake serial and synthesizes
status lines via the existing helpers, then asserts on bucket state.

| Test name | Setup | Expected outcome |
|---|---|---|
| `test_locked_bucket_survives_single_outlier` | Bucket reaches LOCKED, feed 19 inliers, inject 1 outlier at +6σ, feed 5 more inliers. | Bucket stays LOCKED. `outlier_streak ≤ 1`. |
| `test_locked_bucket_unlocks_on_streak` | Bucket reaches LOCKED, feed 5 consecutive 4σ outliers. | Bucket unlocks; `last_unlock_reason == "streak"`. |
| `test_locked_bucket_unlocks_on_catastrophic` | Bucket reaches LOCKED, single 12σ event. | Unlock with `last_unlock_reason == "catastrophic"`. |
| `test_locked_bucket_unlocks_on_drift` | Bucket reaches LOCKED, feed 40 samples each +2σ. | After dwell, unlock with `last_unlock_reason == "drift"`. |
| `test_noisy_bucket_never_locks` | Feed bucket samples whose `resid_var_ewma` settles above 400. | Bucket reaches STABLE, never LOCKED. `wait` contains `noise`. |
| `test_lock_dwell_blocks_immediate_unlock` | Force bucket into LOCKED then immediately feed 5 moderate outliers within `MIN_LOCK_DWELL`. | No unlock; outlier_streak grows but blocked by dwell. |
| `test_unlock_then_relock_does_not_chatter` | Force unlock by drift; feed samples that satisfy Path A but with same noise level. | Bucket stays STABLE on re-attempt (noise gate); does not flip-flop. |
| `test_resid_var_ewma_warm_start_does_not_unlock` | Bucket fresh, very low `n`, single moderate residual. | No unlock — warmup gate blocks moderate channels via dwell + warmup. |
| `test_phase_2_11_chatter_repro_fixture` | Replay synthesized `Outer_wall_v1375` trace from §2.1 field data. | Bucket locks ≤ 1 time per simulated run. **This test fails on current main and passes after 2.11.3.** |

### 5.2 Schema migration tests

See §4.4.

### 5.3 Phase 2.9 regression tests

These must continue to pass byte-for-byte:

- `test_observe_only_default` — tuner default sends no SETs.
- `test_path_a_locks` — multi-run consensus still locks.
- `test_path_b_locks` — single-print high-confidence still locks.
- `test_layer_credit_via_marker` and `test_layer_credit_via_status`.
- `test_no_sv_in_finish_commit` (post-2.9.16).
- `test_stale_bucket_excluded` etc.

### 5.4 Phase 2.10 regression tests

These must continue to pass:

- `test_phase_2_10_parity.py` — full sidecar→matcher→on_m118 chain
  produces the same event sequence as the shell-marker baseline.
- `test_klipper_motion_tracker.py` — UDS client + SegmentMatcher
  unit tests.

Phase 2.11 does not touch `klipper_motion_tracker.py`, so these
tests are run unchanged as regression evidence.

### 5.5 Test commands

```bash
python3 -m py_compile scripts/nosf_live_tuner.py
python3 -m py_compile scripts/test_nosf_live_tuner.py
python3 scripts/test_nosf_live_tuner.py
python3 scripts/test_klipper_motion_tracker.py
python3 scripts/test_phase_2_10_parity.py
python3 scripts/test_gcode_marker.py
python3 -m py_compile scripts/*.py
```

All seven commands must exit 0 at every milestone.

---

## 6. Pi validation plan

Pi validation runs after 2.11.3 lands and before 2.11.5 closes.

### 6.1 Sequence

1. **Baseline snapshot.** Operator copies current state file aside:
   `cp ~/nosf-state/buckets-<id>.json ~/nosf-state/buckets-<id>.json.schema3.bak`.
2. **Migrate.** Run `python3 scripts/nosf_live_tuner.py --state-info`
   once to trigger schema 3→4 migration and confirm loader works.
3. **Capture state-info verbose** (post-migration baseline). Save
   `state-info-pre.txt`.
4. **First print.** Run the same calibration print used to capture
   the May 2026 chatter evidence. Tuner runs in observe daemon mode:
   `python3 scripts/nosf_live_tuner.py --port <p> --machine-id <m>
   --klipper-uds <path> --observe-daemon --debug --progress-interval 5`.
5. **Save debug log** of the run. Capture state-info after run 1.
6. **Second print, same model.** Repeat. Capture state-info.
7. **Third print, same model.** Repeat. Capture state-info.
8. **Compare.**

### 6.2 Success criteria

- For at least one frequently-active bucket (e.g. `Outer wall_v1375`):
  - debug log contains **at most one** unlock line across all three
    prints combined.
  - bucket is `LOCKED` at the end of run 2 and **remains LOCKED**
    through end of run 3.
- Total `locked` bucket count at end of run 3 ≥ Phase 2.10 baseline.
- No bucket cycles LOCKED ↔ TRACKING more than once per print.
- `outlier_streak` for any LOCKED bucket reaches ≥ 3 only when a
  real material change is operator-recognizable.
- Schema 3 state file from prior runs loads without error.

### 6.3 Over-conservative signals

If any of the following are observed, treat as a warning and adjust
constants in 2.11.5:

- A bucket that previously locked in Phase 2.10 (under chatter) never
  reaches LOCKED in 2.11, with `wait=noise` even when residual
  scatter looks operationally fine (`σ < 25 sps`).
  - Mitigation: raise `V_NOISE_LOCK_THR` from 400 to 600.
- True material/regime change goes undetected for > 60 samples
  post-event.
  - Mitigation: lower `K_CATA` from 8 to 6.
- Sustained drift unlock fires on print-to-print warm-up wobble
  rather than real drift.
  - Mitigation: raise `M_DRIFT_DWELL` from 30 to 60.

### 6.4 Documentation of results

Append a `### Phase 2.11 Pi Validation` block to `TASK.md` (existing
phase pattern) with:
- per-bucket lock count and unlock count across 3 prints
- table of constant tweaks made, if any
- final `nosf-state.json` size and bucket count

---

## 7. Files touched (exact list)

| File | Reason | Risk |
|---|---|---|
| `scripts/nosf_live_tuner.py` | Bucket fields, KF residual stats, lock gate, three-channel unlock, schema 3→4 migration, state-info `--verbose`, `wait` reasons, constants. | Highest; touches the core algorithm. Mitigated by extensive unit tests. |
| `scripts/test_nosf_live_tuner.py` | Add §5 tests. | Low; additive. |
| `tests/fixtures/phase_2_11_chatter.json` (new) | Reproduction fixture for §5.1 last test row. Captures the Outer_wall_v1375 sample trace as JSON. | Low; new file, opt-in fixture. |
| `MANUAL.md` | New `state-info --verbose` columns; new `wait` reasons; algorithm change note under "Tuning workflow". | Doc-only. |
| `KLIPPER.md` | Operator note on "noisy bucket" outcome; recommend re-run after suspected fault. | Doc-only. |
| `CONTEXT.md` | Append Phase 2.11 to phase history. | Doc-only. |
| `README.md` | Bullet under tuner features: "Hysteresis-based lock/unlock; noisy buckets isolated, not chattered." | Doc-only. |
| `TASK.md` | Phase 2.11 Findings/Plan/Completed Steps/Pi Validation. | Doc-only; per AGENTS.md workflow. |

Files **NOT** touched:
- Any firmware source (`firmware/**`)
- `scripts/klipper_motion_tracker.py`
- `scripts/gcode_marker.py`
- `scripts/nosf_analyze.py` (unless analyzer's `load_state` rejects schema 4; if so, add a one-line `migrate_state_data` import; document in 2.11.2 commit body if needed)
- `scripts/nosf_marker.py`, `scripts/nosf_logger.py`
- `config.ini` (no new firmware tunables)

---

## 8. Milestones

> AGENTS.md rules: one milestone per commit, push immediately,
> validation gates per milestone, `Generated-By` footer. Schema and
> algorithm work lands separately from docs so a rollback to any
> milestone leaves the tree compilable and tests green.

### 8.1 — 2.11.0 — Plan (this document)

**Status:** completes when this file lands on `main`.

Commit subject: `docs(plan): phase 2.11 smarter lock/unlock`
Validation: none (doc-only).

### 8.2 — 2.11.1 — Reproduction fixtures and failing tests

**Goal:** add tests that **fail on current main**, proving the
chatter bug is captured before the algorithm changes.

Files:
- `tests/fixtures/phase_2_11_chatter.json` (new) — JSON sample
  trace synthesized from §2.1 evidence. Format documented in test.
- `scripts/test_nosf_live_tuner.py` — add
  `test_phase_2_11_chatter_repro_fixture` (decorated as
  `@expected_fail_on_main` if test infra supports it, or marked
  with a clear `# Expected to fail until 2.11.3` comment and
  reported with non-fatal stdout).

Implementation note: because the existing test runner returns
non-zero on any assertion failure, the chatter test must be
implemented as a **logged-but-not-failing** assertion in this
milestone (e.g., prints `EXPECTED FAIL: ...`). Milestone 2.11.3
upgrades it to a hard assertion.

Validation:
```
python3 -m py_compile scripts/nosf_live_tuner.py
python3 -m py_compile scripts/test_nosf_live_tuner.py
python3 scripts/test_nosf_live_tuner.py
```

Commit subject: `test(tuner): add phase 2.11 chatter repro fixture`

### 8.3 — 2.11.2 — Residual statistics and schema 4 migration

**Goal:** new bucket fields populated correctly, schema chain
extended, lock/unlock behavior unchanged.

Files:
- `scripts/nosf_live_tuner.py` — add new constants
  (§3.7), extend `Bucket` (§3.1), extend KF update path (§3.2),
  add `_migrate_3_to_4` (§4.1), bump `SCHEMA_VERSION = 4`,
  extend `_load_state` / `_persist` / `bucket_from_raw`.
- `scripts/test_nosf_live_tuner.py` — add §4.4 migration tests
  (3→4, chain 2→4, chain 1→4) and a
  `test_residual_stats_accumulate` test.

**Lock/unlock behavior is unchanged in this milestone.** Existing
single-sample unlock test (line 591-602 in current main) stays
intact. The point of 2.11.2 is to land the data plumbing
non-functionally so the algorithm change in 2.11.3 is a small,
isolated diff.

Validation:
```
python3 -m py_compile scripts/nosf_live_tuner.py
python3 -m py_compile scripts/test_nosf_live_tuner.py
python3 scripts/test_nosf_live_tuner.py
python3 scripts/test_phase_2_10_parity.py
python3 scripts/test_klipper_motion_tracker.py
```

Commit subject: `feat(tuner): residual stats and schema 4 migration`

### 8.4 — 2.11.3 — Lock/unlock hysteresis

**Goal:** activate three-channel unlock, noise-gated lock,
`MIN_LOCK_DWELL`, `P_UNLOCK_RESET`. Make the chatter-repro test
a hard assertion.

Files:
- `scripts/nosf_live_tuner.py` — rewrite the unlock branch and
  the `_maybe_lock` noise gate per §3.4 and §3.5. Add helper
  methods `_resid_sigma`, `_evaluate_unlock`, `_apply_unlock`,
  `_credit_locked_sample`. Update `bucket_wait_reason` per §4.6.
- `scripts/test_nosf_live_tuner.py` — flip
  `test_phase_2_11_chatter_repro_fixture` to a hard assertion;
  add §5.1 tests (8 new tests).

Validation:
```
python3 -m py_compile scripts/nosf_live_tuner.py
python3 -m py_compile scripts/test_nosf_live_tuner.py
python3 scripts/test_nosf_live_tuner.py
python3 scripts/test_phase_2_10_parity.py
python3 scripts/test_klipper_motion_tracker.py
python3 -m py_compile scripts/*.py
```

Commit subject: `feat(tuner): hysteresis-based lock unlock`

### 8.5 — 2.11.4 — `state-info` verbose, docs

**Goal:** expose new diagnostics; update docs.

Files:
- `scripts/nosf_live_tuner.py` — `--state-info --verbose` columns,
  CSV mode extension.
- `MANUAL.md` — algorithm note, new state-info columns, new wait
  reasons (`noise σ²=...`, `dwell N/M`).
- `KLIPPER.md` — operator note: noisy bucket behavior is
  intentional; recommend filament-load check if entire feature
  family is `wait=noise`.
- `README.md` — one-line update.
- `CONTEXT.md` — append Phase 2.11.

Validation:
```
python3 -m py_compile scripts/nosf_live_tuner.py
python3 scripts/test_nosf_live_tuner.py
```

Commit subject: `docs(tuner): document phase 2.11 lock unlock`

### 8.6 — 2.11.5 — Pi validation and constant tuning

**Goal:** run §6 Pi validation; if §6.3 over-conservative signals
fire, adjust constants and re-validate.

Files:
- `scripts/nosf_live_tuner.py` — only if constants need tuning
  per §6.3.
- `TASK.md` — append `### Phase 2.11 Pi Validation` block per
  §6.4.

Validation: all per-2.11.3 commands, plus the Pi soak run.

Commit subject: `docs(task): record phase 2.11 pi validation`
(or `tune(tuner): adjust phase 2.11 thresholds` if §6.3 fires).

---

## 9. Risks and open questions

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R-1 | Constant choices wrong on first land — chatter persists or true drift uncaught. | Medium | §6.3 over-conservative signals; tune in 2.11.5; constants are named at module top. |
| R-2 | Schema 4 migration destroys an operator's tune DB. | Low | §17.1.6 chained migration pattern; only adds fields; tests cover. Operator backup recommended in 2.11.4 docs. |
| R-3 | `resid_var_ewma` warm-start skews early lock decisions. | Low | Warm-start floor `R_BASE`; `N_WARMUP_FOR_NOISE = 50` gate; first 50 samples never noise-blocked. |
| R-4 | Catastrophic channel itself flip-flops on alternating extreme samples. | Low | Catastrophic unlock resets P to 400, not 1e4, but bucket must re-traverse Path A/B before re-locking — at minimum 200 samples plus warmup. |
| R-5 | Drift channel false-positive on print start (cold extrusion). | Medium | `M_DRIFT_DWELL = 30` post-lock samples before drift can fire. Print-start samples typically rejected via `CF` and `APX` gates already. |
| R-6 | Phase 2.10 sidecar segment boundaries cause large legitimate `est` jumps that look like outliers. | Medium | At segment boundary, `last_feature`/`last_v_fil` change, bucket key changes — different bucket. Same bucket only sees same feature/v_fil_bin; jumps within a bucket are real. |
| R-7 | Analyzer's `load_state` (which has its own schema-1/2 logic) breaks on schema 4. | Low-Medium | Check `scripts/nosf_analyze.py` `load_state` at start of 2.11.2; if it doesn't call `migrate_state_data`, add a one-line import. Document in commit body. |
| R-8 | New CSV columns break downstream consumers reading by column index. | Low | No known consumers read by index; analyzer uses keyed dict. Document in MANUAL.md. |
| R-9 | `K_DRIFT = 4` with `EWMA_EFFECTIVE_N ≈ 19` may be too sensitive on small σ buckets. | Low | If observed in §6, gate drift channel on `sigma >= R_BASE ** 0.5` (already implicit). |

Open questions (defer to maintainer if 2.11.5 surfaces them):

- **Q-2.11-A.** Should `resid_var_ewma` decay during long idle gaps
  (no samples for hours), or stay frozen? Current plan: stay frozen,
  since scatter is a property of the bucket, not of time.
- **Q-2.11-B.** Should an explicit `--unlock-all-and-relearn` flag
  be added for operators who want to force re-learning after a
  hardware change? Out of scope for 2.11; defer.
- **Q-2.11-C.** Should `nosf_analyze.py` weighted-mean computation
  weight by `1 / resid_var_ewma` to down-weight noisy buckets? This
  is an analyzer change; defer to a hypothetical Phase 2.12.

---

## 10. Rollback plan

- **Per-milestone:** each commit is independently revertable. If
  2.11.3 introduces a regression, revert the single commit; 2.11.2
  data plumbing stays (compatible with prior algorithm because new
  fields are unused) and schema 4 state files continue to load.
- **Schema 4 → 3 downgrade** is not supported. Operator workaround:
  delete or strip the schema 4 fields manually, set `_schema: 3`.
  `_meta` block is unaffected.
- **Full rollback** to pre-Phase 2.11 main: revert 2.11.1 through
  2.11.5 in reverse order; restore from `~/nosf-state/buckets-<id>.json.schema3.bak`
  (made per §6.1 step 1). Schema-3 file loads on pre-2.11.2 main
  unchanged.

---

## 11. Acceptance criteria for Phase 2.11

Phase 2.11 is **complete** when all of the following hold:

1. `python3 scripts/test_nosf_live_tuner.py` exits 0 with all §5.1,
   §5.2, §5.3 tests green.
2. `python3 scripts/test_phase_2_10_parity.py` and
   `python3 scripts/test_klipper_motion_tracker.py` exit 0
   unchanged.
3. `python3 -m py_compile scripts/*.py` exits 0.
4. `python3 scripts/nosf_live_tuner.py --state-info` and
   `--state-info --verbose` produce coherent output on a schema-4
   state file.
5. On the maintainer's Pi, three back-to-back calibration prints of
   the same model satisfy §6.2 success criteria.
6. `MANUAL.md`, `KLIPPER.md`, `README.md`, `CONTEXT.md` reflect the
   new algorithm, new diagnostics, and new wait reasons.
7. `TASK.md` contains a `### Phase 2.11 Pi Validation` block with
   measured locked/unlock counts.

---

## 12. First implementation recommendation

When the maintainer says "go", **start with 2.11.1**. Landing the
failing test first is the cheapest insurance against the
implementation accidentally weakening another behavior. After 2.11.1
turns red on `main`, the diff for 2.11.2 + 2.11.3 has a precise
endpoint: that test must turn green and nothing else may turn red.

If 2.11.5 Pi validation reveals over-conservatism, treat it as a
constant-tuning iteration; do not redesign the channels. The
constants are the only place tunable without re-validating the
schema and the algorithm.

---

## 13. Cross-references

- `SYNC_REFACTOR_PHASE_2_9.md` §15-§17 — observe-only contract,
  schema 1/2/3 chain, Path A/B lock criteria, watermark layout.
- `SYNC_REFACTOR_PHASE_2_10.md` §6-§9 — sidecar+UDS marker flow,
  SegmentMatcher event surface, `on_m118` contract.
- `scripts/nosf_live_tuner.py` line 174-180 — KF update path.
- `scripts/nosf_live_tuner.py` line 591-602 — current single-sample
  unlock rule (the bug Phase 2.11 fixes).
- `scripts/nosf_live_tuner.py` line 680-710 — current `_maybe_lock`.
- `scripts/nosf_live_tuner.py` line 207-241 — `_MIGRATIONS` registry
  and `migrate_state_data`.
- `scripts/klipper_motion_tracker.py` — unchanged by Phase 2.11.

---

*End of Phase 2.11 plan.*
