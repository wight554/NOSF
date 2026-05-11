# Sync Refactor — Phase 2.12

> **Phase 2.12 — Analyzer rigor and relative noise gate**
>
> Phase 2.11 fixed lock chatter but operator's first three-print Pi
> validation surfaced two new problems:
>
> 1. The tuner's absolute noise gate (`V_NOISE_LOCK_THR = 400 sps²`,
>    σ ≤ 20 sps) is too tight for this hardware. Real σ on
>    high-traffic buckets is 80-270 sps, so **zero of 150 buckets
>    locked** across 16512 samples and 3687 s of MID time. The gate
>    needs to be relative to flow rate, not absolute.
>
> 2. `scripts/nosf_analyze.py` produces wildly oscillating and
>    physically impossible recommendations on this same data set:
>    `baseline_rate` 1600 → 328 → 387 → 150 across three runs,
>    `mid_creep_timeout_ms` climbing to 17050 ms, and
>    `buf_variance_blend_ref_mm = 707.5 mm` against a 27 mm
>    physical buffer travel. Three independent bugs in the
>    analyzer combine to produce this output, and `--mode aggressive`
>    happily emits the patch even when the state file contains
>    zero LOCKED buckets.
>
> Phase 2.12 fixes both: a relative tuner noise gate and a hardened
> analyzer that rejects insufficient data, weights by precision, and
> reads the right CSV columns. Firmware unchanged. Phase 2.9
> observe-only contract and Phase 2.10 sidecar+UDS flow unchanged.
>
> This document is a **plan, not an implementation**. No code lands
> as part of this commit. Implementation begins at milestone 2.12.1.

---

## 0. Decision addendum

| ID | Decision | Effect |
|---|---|---|
| L-1 | Host-side only. No firmware change, no `settings_t` change. | Plan is tuner + analyzer work. |
| L-2 | Pure stdlib + `pyserial` only. | Per project rules. |
| L-3 | Observe-only default preserved. `on_m118` ingress frozen. `klipper_motion_tracker.py` frozen. | No regression to Phase 2.9 / 2.10. |
| L-4 | Phase 2.11 hysteresis fields (`resid_var_ewma`, `outlier_streak`, etc.) preserved. Only the noise gate **decision rule** changes; the data plumbing stays. | Minimal diff to lock logic. |
| L-5 | State schema bumps 4 → 5 only if a new persisted field is required. If the new noise gate is purely a decision-rule change against existing fields, schema stays at 4. **Plan currently chooses no schema bump.** | One less migration to write. |
| L-6 | Analyzer behavior changes are observable in `[nosf_review]` patch output. No CSV schema change required from the tuner side; analyzer reads the existing CSV columns correctly. | Backward-compatible with all on-disk CSVs. |
| L-7 | `--mode aggressive` becomes a loud warning rather than a silent gate bypass. It does NOT disable the new minimum-evidence floor. | Operator must opt in to junk patches with an explicit flag. |
| L-8 | New constants explicit, conservative initial values, documented at the top of each script. | Operator can tune by editing constants if 2.12.6 Pi soak proves too tight or too loose. |
| L-9 | Phase 2.9 dual-path lock criteria (Path A multi-run, Path B single-print) unchanged. The noise gate amends the variance check only. | No regression to lock semantics. |

---

## 1. Field evidence (operator soak, May 2026)

### 1.1 Tuner state-info summary

```
TOTAL: 150 buckets, 0 locked, 16512 samples, 3686.7s MID
```

Representative bucket sample (selected high-evidence rows):

```
Inner wall_v1400     1014    20.0  1899   0.358  STABLE  1   98  275.9  σ²=14620  wait=noise
Sparse infill_v1400   193    15.6  1460   0.280  STABLE  1   93  220.0  σ²=19653  wait=noise
Outer wall_v1400      956    21.0  1371   0.307  STABLE  1  148  198.2  σ²=17609  wait=noise
Inner wall_v1750      482    22.9   874   0.328  STABLE  1   27  513.8  σ²=24213  wait=noise drift
Outer wall_v1750      708    22.5   912   0.406  STABLE  1   45  366.6  σ²=54496  wait=noise
Top surface_v725      106    21.2   593   0.418  STABLE  2   12  129.4  σ²=15317  wait=noise
Outer wall_v1700      100    20.5   810   0.151  STABLE  1   47  151.1  σ²=1566   wait=noise drift
```

The pattern: **every bucket with enough samples to matter is `wait=noise`**. Every bucket with low σ² has n<50. The threshold is biting precisely the operationally important buckets.

Compute σ/x ratio for the rows above:

| Bucket | x | σ = √σ² | σ/x |
|---|---|---|---|
| Inner wall_v1400 | 1014 | 121 | 0.12 |
| Sparse infill_v1400 | 193 | 140 | 0.73 |
| Outer wall_v1400 | 956 | 133 | 0.14 |
| Inner wall_v1750 | 482 | 156 | 0.32 |
| Outer wall_v1750 | 708 | 233 | 0.33 |
| Top surface_v725 | 106 | 124 | 1.17 |
| Outer wall_v1700 | 100 | 40 | 0.40 |

Buckets with σ/x ≤ 0.20 (Inner wall_v1400, Outer wall_v1400) are
operationally clean and should lock. Buckets with σ/x ≥ 0.30 are
genuinely noisy and should not lock — but they should not be flagged
as a hard failure either, just held at STABLE.

### 1.2 Analyzer output across three runs

```
run 1
  baseline_rate                   1600 -> 328     (HIGH, n=1290, sigma=195.0)
  sync_trailing_bias_frac        0.400 -> 0.327   (HIGH, n=10558)

run 2
  baseline_rate                    328 -> 387     (HIGH, n=1899, sigma=253.6)
  sync_trailing_bias_frac        0.327 -> 0.324   (HIGH, n=9716)
  mid_creep_timeout_ms            4000 -> 14859   (HIGH, 142 dwells)
  buf_variance_blend_ref_mm      1.000 -> 374.000 (HIGH, sigma p95 374.20)

run 3
  baseline_rate                    387 -> 150     (HIGH, n=2766, sigma=253.6)
  sync_trailing_bias_frac        0.324 -> 0.382   (HIGH, n=14202)
  mid_creep_timeout_ms           14859 -> 17050   (HIGH, 286 dwells)
  buf_variance_blend_ref_mm    374.000 -> 707.500 (HIGH, sigma p95 707.50)
```

Generated with `nosf_analyze.py --mode aggressive --commit-watermark`
against a state file containing **zero LOCKED buckets**. All values
labeled `HIGH` confidence because the analyzer counts CSV rows, not
LOCKED bucket evidence.

Quantitatively impossible signals:
- `baseline_rate` moved by 178 sps between consecutive prints with
  the same model. A stable estimator should move <10 sps.
- `mid_creep_timeout_ms = 17050` exceeds total MID time of several
  buckets in the same print. The timeout would never fire.
- `buf_variance_blend_ref_mm = 707.5 mm` against a buffer travel of
  ~27 mm — off by a factor of 26.

### 1.3 Why every value is labeled HIGH

`confidence()` (`nosf_analyze.py:178-187`) keys off row count alone.
With CSV runs of 5000-15000 rows the row threshold for HIGH is
trivially met, regardless of whether those rows produced any LOCKED
buckets. The confidence label is misleading.

---

## 2. Root-cause assessment

### 2.1 Tuner — absolute noise gate

`scripts/nosf_live_tuner.py` constants from Phase 2.11:

```python
V_NOISE_LOCK_THR = 400.0    # σ² in sps²  → σ ≤ 20 sps
```

This is a single absolute number for buckets that span x = 100 sps
to x = 1500 sps. Field σ on **all** high-evidence buckets exceeds
80 sps. Gate fires universally; no bucket can lock.

The right model is **relative**: σ as a fraction of the bucket's
mean x. Low-flow buckets tolerate small absolute σ; high-flow buckets
tolerate proportionally larger absolute σ.

### 2.2 Analyzer bug A — single-bucket baseline

`nosf_analyze.py:207`:

```python
dominant = max(by_bucket.values(), key=len) if by_bucket else []
est_vals = [to_float(r.get("est_sps")) for r in dominant]
```

`baseline_rate` is computed from the rows of **one bucket** — the
single bucket with the most CSV rows in this run. Different runs
have different "dominant" buckets:

- run 1: probably `Inner wall_v1400` (n≈1290)
- run 2: `Inner wall_v1400` (n≈1899) — different x because the print
  was longer and other features contributed
- run 3: `Inner wall_v1400` or `Outer wall_v1400` (n≈2766 combined)

Each "dominant" bucket has a different physical extrusion-rate
centroid, so `baseline_rate` chases whichever bucket happened to
dominate that run. This is the primary cause of the
1600 → 328 → 387 → 150 oscillation.

### 2.3 Analyzer bug B — baseline math subtracts σ

`nosf_analyze.py:211`:

```python
baseline = max(0.0, est_p50 - safety * est_sigma) if est_vals else current["baseline_rate"]
```

With `safety = 1.0` (aggressive) or `safety = 1.5` (safe), the
analyzer subtracts σ (or 1.5σ) from the median EST. For a bucket
with p50 = 1000 sps and σ = 250, baseline becomes 750 or 625.

This conflates **the centroid** (what `baseline_rate` should be)
with **a lower confidence bound** (where to set a conservative
target). `baseline_rate` is the predicted typical flow; it should
be the centroid. A safety margin belongs in a separate decision
elsewhere (e.g., choosing the operating point of the sync
controller, not the centroid itself).

### 2.4 Analyzer bug C — mid_creep_timeout from p95 of dwells

`nosf_analyze.py:234`:

```python
mid_timeout = percentile(dwell_ms, 95) if len(dwell_ms) >= 50 else current["mid_creep_timeout_ms"]
```

`mid_creep_timeout_ms` is the wait time before active wall-seek
creep activates during MID dwell. The semantics are: "if MID dwell
lasts longer than this, start creeping to wake the system."

The p95 of observed dwells is the **longest 5%** of dwells. As the
print runs more features and the buffer settles into more
heterogeneous behavior, the right tail of the dwell distribution
grows. So `mid_timeout` grows monotonically with print complexity.
This is exactly backwards: a timeout should be derived from when
creep is **needed**, not from the dwells where creep was **not
needed** (the long ones, where everything was fine).

The right model: take the p50 or p25 of MID dwells that lasted long
enough to actually warrant intervention (e.g., dwells exceeding
some absolute threshold). Or treat `mid_creep_timeout_ms` as not
learnable from positive-control data alone and keep its default
unless an explicit failure mode is captured.

### 2.5 Analyzer bug D — `BL` field labeled `sigma_mm`

`nosf_analyze.py:118` in `read_csv_runs`:

```python
"sigma_mm": row.get("BL") or row.get("sigma_mm"),
```

The tuner's `CsvEmitter` (Phase 2.11) writes columns:

```python
self.fields = ["wall_ts", "run_seq", "layer", "feature", "v_fil",
    "BL", "BP", "BPV", "EST", "RT", "AD", "APX", "CF", "TC", "BUF", "MK_seq"]
```

`BL` is the firmware-reported **baseline rate** in sps, parsed from
the `BL:` status field. It is not a buffer-position σ in mm. The
analyzer reads `BL` (e.g., 707 sps after the runaway baseline
landed) into `sigma_mm`, then `nosf_analyze.py:268`:

```python
var_ref = max(0.5, round(sigma_p95 / 0.5) * 0.5) if sigma_vals else current["buf_variance_blend_ref_mm"]
```

quantizes 707 to the nearest 0.5 and reports `707.5 mm`. The
analyzer never sees a real σ_mm.

The real σ_mm of the buffer position lives in the `BPV` column (BP
variance) or must be computed as a rolling stdev of the `BP` column
across recent MID samples. `BPV` is reported as a percentage in
firmware status (divided by 100 in tuner CSV at line 524 of
`nosf_live_tuner.py`), so its unit is **fractional**, not mm — it
needs conversion through buffer geometry, or the analyzer must
compute σ of `BP` directly.

### 2.6 Analyzer bug E — no LOCKED-bucket floor

`nosf_analyze.py:190-279` aggregates CSV rows without consulting
the `LOCKED` set in the state file. `--commit-watermark` writes
recommendations even when zero buckets are LOCKED.

`load_state` and `locked_bucket_labels` exist (lines 133-158) but
are not used to gate aggregation. They are used only by callers
that already filter for printing purposes.

### 2.7 Analyzer bug F — confidence label is misleading

`confidence()` returns HIGH when row count exceeds 1000. With 9000+
CSV rows per print, every value lands at HIGH. The label does not
reflect whether the rows came from clean buckets, from converged
buckets, from a single dominant bucket, or from sparse
contributions across heterogeneous buckets.

### 2.8 Bucket fragmentation — secondary

150 buckets after 3 prints, with median n per bucket ≈ 14. The
25 mm³/s bin width fragments the data and makes per-bucket
estimates noisy. Coarsening to 50 mm³/s would roughly halve the
bucket count without losing meaningful resolution (extrusion
behavior does not change abruptly at 25 mm³/s steps).

This is **secondary** to the main bugs and can be addressed
separately. Plan defers it; see §11 risks.

---

## 3. Proposed fixes

### 3.1 Tuner — relative noise gate

Replace the absolute `V_NOISE_LOCK_THR` test with a ratio test in
`_maybe_lock` and in `bucket_wait_reason`:

```python
NOISE_RATIO_THR = 0.25       # σ / x must be ≤ 25%
V_NOISE_FLOOR = 100.0        # σ² floor (= σ ≥ 10 sps); below this, always considered clean
NOISE_GATE_MIN_X = MIN_LEARN_EST_SPS   # = 100 sps; below this, fall back to absolute floor

def _noise_ratio(b):
    sigma2 = max(b.resid_var_ewma, V_NOISE_FLOOR)
    sigma = math.sqrt(sigma2)
    x = max(b.x, NOISE_GATE_MIN_X)
    return sigma / x

def _noise_ok(b):
    if b.n < N_WARMUP_FOR_NOISE:
        return True
    return _noise_ratio(b) <= NOISE_RATIO_THR
```

`V_NOISE_LOCK_THR` is removed. The unlock-side residual variance
machinery (`resid_var_ewma`, `outlier_streak`, three-channel
detector) is untouched.

For the operator's field data, `Inner wall_v1400` (σ/x = 0.12),
`Outer wall_v1400` (0.14), and similar high-traffic buckets will
pass `_noise_ok`. `Inner wall_v1750` (0.32) and `Top surface_v725`
(1.17) will not pass and remain STABLE with `wait=noise σ/x=0.32`.

### 3.2 Tuner — `wait` reason update

`bucket_wait_reason` updated:

```python
if b.state in ("TRACKING", "STABLE") and b.n >= N_WARMUP_FOR_NOISE:
    ratio = _noise_ratio(b)
    if ratio > NOISE_RATIO_THR:
        return f"noise σ/x={ratio:.2f}"
```

Existing reasons (samples N/M, runs, layers, mid_time, dwell)
unchanged. The old `noise σ²=N>thr` is replaced by the ratio form.

### 3.3 Analyzer — LOCKED-bucket floor

Default behavior:

- If `state_buckets` is provided and contains **at least one**
  LOCKED bucket, baseline / bias / variance suggestions are
  computed only from LOCKED buckets' samples (plus the existing
  recency weighting in the patch emitter).
- If `state_buckets` contains **zero** LOCKED buckets, the
  analyzer:
  - In `safe` mode: refuses to emit suggestions; writes a patch
    with `# REFUSED: no LOCKED buckets in state file` at the top
    and exits non-zero.
  - In `aggressive` mode: prints a loud stderr warning, writes the
    patch with `# WARNING: zero LOCKED buckets; suggestions are
    pre-lock estimates and may not converge` and exits 0.
- A new `--force` flag is added to bypass the floor entirely (for
  tooling that explicitly opts in). Default behavior follows the
  mode.

This is the single biggest change. `--mode aggressive
--commit-watermark` with zero LOCKED buckets currently writes
junk and exits 0. After Phase 2.12, the operator gets a clear
"insufficient evidence" signal.

### 3.4 Analyzer — precision-weighted baseline across all qualifying buckets

Replace `dominant = max(by_bucket.values(), key=len)` with a
**precision-weighted mean across all qualifying buckets**.

A bucket qualifies if:
- LOCKED (when state floor is satisfied per §3.3), OR
- in `--force` mode: `n >= 50` AND `σ/x ≤ NOISE_RATIO_THR` AND
  `cumulative_mid_s >= 10`.

For each qualifying bucket:

```python
weight_b = bucket["n"] / max(bucket["resid_var_ewma"], V_NOISE_FLOOR)
mean_b = bucket["x"]
```

`baseline_rate = sum(weight * mean) / sum(weight)`. Equivalent to
inverse-variance weighting standard in sensor fusion. Then a
5/95 percentile trim across the qualifying buckets' x values to
discard outlier buckets before the weighted mean.

`safety_k` is **removed from baseline computation**. The patch
will optionally print a `_lower_bound` annotation if useful, but
the recommended `baseline_rate` value is the centroid.

### 3.5 Analyzer — bias from qualifying buckets only

`sync_trailing_bias_frac` already comes from `bp_delta / 7.8`
across all MID rows (line 215). Replace with:

- bias mean computed only from CSV rows whose bucket is qualifying
  per §3.4.
- precision-weighted by bucket: each bucket's `bias_b` from its
  median `(BP - RT) / 7.8`, then weighted by `n / σ²`.

### 3.6 Analyzer — `mid_creep_timeout_ms` redefinition

Replace the p95-of-dwells heuristic with a model derived from the
correct semantics:

- A MID dwell that ends in BUF leaving MID **before** any wall
  contact (BPV high) is a successful natural dwell and does NOT
  inform the timeout.
- A MID dwell that ends due to detected wall contact at the
  far end (BPV low after long quiescence) **should have triggered
  creep earlier**. The timeout should be set near the **p25 of
  such dwells**, not p95 of all dwells.

If the CSV does not contain enough wall-contact-classified dwells
(< 20), the analyzer keeps `current["mid_creep_timeout_ms"]` (4000)
and marks confidence `DEFAULT`. This is the conservative
fallback and accepts that the timeout is operator-tuned, not
learnable from the available signals.

This is a heuristic change that may itself need Pi validation;
plan §9 risk R-3 calls this out.

### 3.7 Analyzer — `buf_variance_blend_ref_mm` unit fix

Replace `"sigma_mm": row.get("BL") or row.get("sigma_mm")` with:

```python
"sigma_mm": row.get("sigma_mm"),       # only old nosf_logger.py path
"bp_mm": row.get("BP") or row.get("bp_mm"),
"bpv_frac": row.get("BPV") or row.get("bpv"),
```

When the source is the new tuner CSV (no `sigma_mm` column),
compute σ of `BP` directly:

```python
def buffer_position_sigma_mm(rows):
    bp = [to_float(r.get("bp_mm")) for r in rows if r.get("bp_mm") not in ("", None)]
    if len(bp) < 50:
        return None
    return stats.stdev(bp)
```

Compute over qualifying buckets only (per §3.4). Take the p95 of
the per-bucket σ_BP values. Clamp to a physically plausible range
`[0.1, 5.0]` mm and report.

If the result is None (insufficient data), keep the current value
and mark `DEFAULT`.

### 3.8 Analyzer — confidence rewrite

`confidence()` keys off **bucket evidence**, not CSV rows:

```python
def confidence_from_buckets(qualifying_buckets, locked_only):
    if not locked_only and len(qualifying_buckets) >= 5:
        return "MEDIUM"
    if locked_only and len(qualifying_buckets) >= 5:
        return "HIGH"
    if locked_only and len(qualifying_buckets) >= 2:
        return "MEDIUM"
    if qualifying_buckets:
        return "LOW"
    return "DEFAULT"
```

HIGH requires at least 5 LOCKED buckets contributing. MEDIUM
requires either 5 qualifying (non-LOCKED, --force mode) or 2 LOCKED.
LOW for less. DEFAULT for nothing.

### 3.9 Analyzer — `--mode aggressive` semantics

The `--mode` flag now controls the **floor** behavior, not the
safety-k subtraction:

- `safe`: refuse to emit if zero LOCKED. Confidence floor for
  emitting non-DEFAULT values is MEDIUM.
- `aggressive`: emit with warnings even at LOW confidence,
  zero LOCKED. Always prints a banner that the patch is
  pre-validation.

`safety_k` constant is removed. The subtract-σ behavior is gone.
The `SAFETY_K` constant block becomes a no-op or a backward-compat
shim that emits a deprecation warning if a caller passes the old
flag.

### 3.10 Analyzer — patch contributors block

The emitted `[nosf_review]` patch gains a `[nosf_contributors]`
block:

```
[nosf_contributors]
# baseline_rate          5 LOCKED buckets, total n=12410
#   Inner wall_v1400     n=1899  x=1014  σ/x=0.12  w=0.31
#   Outer wall_v1400     n=1371  x= 956  σ/x=0.14  w=0.22
#   Sparse infill_v1400  n=1460  x= 193  σ/x=0.73  w=0.05  [marginal]
#   ...
# sync_trailing_bias_frac 4 LOCKED buckets, total n=10240
#   ...
```

Lets the operator see why a given recommendation landed where it
landed. Critical for diagnostic feedback.

---

## 4. State schema

**No schema bump in Phase 2.12.** All bucket fields needed by the
new noise gate (`resid_var_ewma`, `n`, `x`, etc.) are already in
schema 4 from Phase 2.11. The analyzer changes use existing CSV
columns. `migrate_state_data` is unchanged.

This decision can be revisited in Phase 2.12.x if Pi validation
surfaces a missing field — but the current plan does not require
one.

---

## 5. Test plan

### 5.1 Tuner unit tests (extend `scripts/test_nosf_live_tuner.py`)

| Test name | Setup | Expected |
|---|---|---|
| `test_noise_gate_relative_passes_low_ratio` | Bucket with x=1000, resid_var_ewma=14400 (σ=120, ratio=0.12), n≥200. | `_noise_ok` True; bucket reaches LOCKED via Path A/B. |
| `test_noise_gate_relative_blocks_high_ratio` | Bucket with x=200, resid_var_ewma=14400 (σ=120, ratio=0.60), n≥200. | `_noise_ok` False; bucket stays STABLE; `wait` reports `noise σ/x=0.60`. |
| `test_noise_gate_below_warmup_passes` | n < N_WARMUP_FOR_NOISE with any σ. | `_noise_ok` True; gate inactive during warmup. |
| `test_noise_gate_below_min_x_uses_floor` | x = 50 sps with σ = 25 sps. Ratio computed against `NOISE_GATE_MIN_X = 100`. | Gate uses floor; bucket can still be evaluated without divide-by-near-zero pathology. |
| `test_phase_2_12_field_repro_inner_wall_v1400` | Synthesized trace mirroring `Inner wall_v1400` (n=1899, x=1014, σ²=14620). | Bucket reaches LOCKED. **Fails on pre-2.12.1 main; passes after.** |

### 5.2 Analyzer unit tests (new file `scripts/test_nosf_analyze.py`)

Stdlib `unittest` style consistent with the rest of the test suite.

| Test name | Setup | Expected |
|---|---|---|
| `test_refuses_emit_when_zero_locked_in_safe_mode` | State with 0 LOCKED; CSV with 5000 rows; `--mode safe`. | Patch contains `# REFUSED: no LOCKED buckets`; exits non-zero. |
| `test_warns_emit_when_zero_locked_in_aggressive_mode` | Same state; `--mode aggressive`. | Patch contains `# WARNING: zero LOCKED`; exits 0; values are not the cratered ones. |
| `test_precision_weighted_baseline_across_buckets` | Synthetic state with 5 LOCKED buckets, mixed x and σ. | `baseline_rate` is precision-weighted mean; differs from any single bucket's x. |
| `test_dominant_single_bucket_does_not_dictate_baseline` | One LOCKED bucket with n=10000, four LOCKED buckets with n=500 each. | `baseline_rate` reflects weighting, not just the dominant bucket. |
| `test_bias_only_from_qualifying_buckets` | Five LOCKED buckets at bias 0.30; CSV contains 1000 rows from non-qualifying buckets at bias 0.50. | Resulting bias near 0.30, not 0.40. |
| `test_buf_variance_blend_ref_mm_from_bp_not_bl` | CSV with `BL` field at 707 and `BP` rolling σ ≈ 0.45 mm. | `buf_variance_blend_ref_mm` ≈ 0.5 (clamped to range), NOT 707.5. |
| `test_mid_creep_timeout_default_when_insufficient_data` | CSV with 10 wall-contact dwells. | Confidence DEFAULT; value unchanged from current. |
| `test_confidence_high_requires_5_locked` | State with 1 LOCKED. | Confidence MEDIUM at best; never HIGH. |
| `test_contributors_block_emitted` | Standard config. | Patch contains `[nosf_contributors]` with per-bucket lines. |
| `test_safety_k_removed_no_subtraction` | Single LOCKED bucket at x=1000, σ=200. | `baseline_rate` = 1000 (centroid), NOT 800 or 700. |
| `test_field_oscillation_repro` | Three CSVs mirroring operator's run 1/2/3 (synthetic). | Successive analyzer invocations produce baseline_rate within ±50 sps of each other, not 1600→328→387→150. **Fails on pre-2.12.2 main; passes after.** |

### 5.3 Regression tests (must stay green)

- All Phase 2.9 tests in `scripts/test_nosf_live_tuner.py`.
- All Phase 2.10 tests (`test_phase_2_10_parity.py`,
  `test_klipper_motion_tracker.py`).
- All Phase 2.11 tests (chatter repro, three-channel unlock, dwell
  guard, schema 3→4 migration).
- `python3 -m py_compile scripts/*.py`.

### 5.4 Test commands

```bash
python3 -m py_compile scripts/nosf_live_tuner.py
python3 -m py_compile scripts/nosf_analyze.py
python3 -m py_compile scripts/test_nosf_live_tuner.py
python3 -m py_compile scripts/test_nosf_analyze.py
python3 scripts/test_nosf_live_tuner.py
python3 scripts/test_nosf_analyze.py
python3 scripts/test_phase_2_10_parity.py
python3 scripts/test_klipper_motion_tracker.py
python3 scripts/test_gcode_marker.py
python3 -m py_compile scripts/*.py
```

All must exit 0 at every milestone.

---

## 6. Pi validation plan

### 6.1 Sequence

1. **Baseline snapshot.** Operator preserves current state:
   `cp ~/nosf-state/buckets-myprinter.json ~/nosf-state/buckets-myprinter.json.pre-2-12.bak`
2. **Run print 1** with the same calibration model used in the
   May 2026 evidence. Tuner: `--observe-daemon --debug
   --progress-interval 5`.
3. **Capture state-info `--verbose`** after run 1. Note locked
   count, σ/x ratios on top buckets.
4. **Run analyzer**: `python3 scripts/nosf_analyze.py --in run1.csv
   --state ~/nosf-state/buckets-myprinter.json --machine-id
   myprinter --out /tmp/nosf-patch.ini --mode safe`.
   Expect either a clean patch with at least one LOCKED bucket,
   or `REFUSED: no LOCKED`.
5. **Run print 2.** Capture state-info. Run analyzer again.
6. **Run print 3.** Capture state-info. Run analyzer with
   `--commit-watermark`.

### 6.2 Success criteria

- At least 5 LOCKED buckets at the end of run 3.
- `baseline_rate` recommendation across runs 1/2/3 is monotonically
  approaching a stable value; total drift across the three runs is
  < 50 sps after run 2.
- `buf_variance_blend_ref_mm` recommendation is in `[0.1, 5.0]`
  mm.
- `mid_creep_timeout_ms` recommendation is in `[2000, 8000]` ms or
  the analyzer reports `DEFAULT` and leaves the value at 4000.
- `--mode safe` refuses to emit before run 1 lands a single LOCKED
  bucket.
- Confidence labels reflect bucket evidence: zero LOCKED → DEFAULT
  or LOW with warning; 2-4 LOCKED → MEDIUM; ≥5 → HIGH.

### 6.3 Over-conservative signals and mitigations

| Signal | Mitigation |
|---|---|
| Same buckets that were `wait=noise` in the May 2026 evidence still don't lock under σ/x ≤ 0.25 despite operationally fine print quality. | Raise `NOISE_RATIO_THR` to 0.30 or 0.35 in 2.12.6. |
| `baseline_rate` precision-weighted output dominated by one tiny-σ bucket. | Add an upper cap on per-bucket weight: `weight_b = min(weight_b, MAX_WEIGHT)` where `MAX_WEIGHT = 5 * median(weights)`. |
| `safe` mode refuses every run because no LOCKED buckets ever form. | Run a 4th calibration print, then switch to `aggressive` for the operator's specific re-tune. Document this in 2.12.5. |
| `mid_creep_timeout_ms` redefinition produces a value that the operator finds wrong on the bench. | Plan §3.6 acknowledges this is heuristic; defer to default with `DEFAULT` confidence and revisit. |

### 6.4 Documentation of results

Append `### Phase 2.12 Pi Validation` to `TASK.md` per project
pattern, with:
- LOCKED bucket count progression run-1/-2/-3
- `baseline_rate` and `bias_frac` recommendation progression
- `buf_variance_blend_ref_mm` final value
- σ/x ratios for top 5 high-traffic buckets
- Constant adjustments applied (if any)

---

## 7. Files touched (exact list)

| File | Reason | Risk |
|---|---|---|
| `scripts/nosf_live_tuner.py` | New constants (`NOISE_RATIO_THR`, `V_NOISE_FLOOR`, `NOISE_GATE_MIN_X`); `_noise_ratio` / `_noise_ok` helpers; `_maybe_lock` noise check; `bucket_wait_reason` reason text. **No schema bump.** | Medium. Existing tests must stay green. |
| `scripts/nosf_analyze.py` | `dominant` removed; precision-weighted aggregation; LOCKED floor; mode semantics rewrite; `BL` → real σ_mm derivation; confidence rewrite; `[nosf_contributors]` block; `--force` flag. | Highest in Phase 2.12; touches every per-tunable computation. |
| `scripts/test_nosf_live_tuner.py` | Add §5.1 tests. | Low; additive. |
| `scripts/test_nosf_analyze.py` (new) | §5.2 tests. | Low; new test file. |
| `tests/fixtures/phase_2_12_field_csv.csv` (new) | Synthetic CSV mirroring operator's run 1/2/3 evidence. Used by oscillation-repro test. | Low; new fixture. |
| `tests/fixtures/phase_2_12_field_state.json` (new) | Synthetic state with zero LOCKED buckets but high-evidence STABLE buckets, mirroring operator's actual state. | Low; new fixture. |
| `MANUAL.md` | `state-info` reason text update; `--force` flag; mode-semantics change. | Doc-only. |
| `KLIPPER.md` | Operator note: "Why analyzer refuses to emit"; how to read `[nosf_contributors]`. | Doc-only. |
| `README.md` | One-line update under tuner features. | Doc-only. |
| `CONTEXT.md` | Append Phase 2.12 to phase history. | Doc-only. |
| `TASK.md` | Phase 2.12 Findings/Plan/Completed Steps/Pi Validation. | Doc-only. |

Files **NOT** touched:
- Firmware sources.
- `scripts/klipper_motion_tracker.py` and its tests.
- `scripts/gcode_marker.py`.
- `scripts/nosf_marker.py`, `scripts/nosf_logger.py`.
- `config.ini` / `config.ini.example`.
- State schema 4 (no bump).

---

## 8. Milestones

> One milestone = one commit + push. Validation gates per milestone.
> `Generated-By: GPT-5.4 (High)` footer for this phase.

### 8.1 — 2.12.0 — Plan (this document)

**Status:** completes when this file lands on `main`.

Commit subject: `docs(plan): phase 2.12 analyzer rigor and noise gate`
Validation: none (doc-only).

### 8.2 — 2.12.1 — Tuner relative noise gate

**Goal:** replace absolute `V_NOISE_LOCK_THR` with ratio gate.

Files:
- `scripts/nosf_live_tuner.py` — new constants, helpers, `_maybe_lock`
  noise check, `bucket_wait_reason` text. Remove or comment out
  `V_NOISE_LOCK_THR` and the absolute check.
- `scripts/test_nosf_live_tuner.py` — add §5.1 tests
  (relative_passes_low_ratio, relative_blocks_high_ratio,
  below_warmup_passes, below_min_x_uses_floor,
  field_repro_inner_wall_v1400).

Validation:
```
python3 -m py_compile scripts/nosf_live_tuner.py
python3 -m py_compile scripts/test_nosf_live_tuner.py
python3 scripts/test_nosf_live_tuner.py
python3 scripts/test_phase_2_10_parity.py
python3 scripts/test_klipper_motion_tracker.py
```

Commit subject: `feat(tuner): relative noise gate by sigma over x`

### 8.3 — 2.12.2 — Analyzer LOCKED floor + mode semantics

**Goal:** safe mode refuses, aggressive mode warns. Confidence
rewrite. `--force` flag. Remove `safety_k` subtraction from
baseline.

Files:
- `scripts/nosf_analyze.py` — LOCKED floor, mode rewrite,
  `--force`, `SAFETY_K` removal, confidence helper.
- `scripts/test_nosf_analyze.py` (new) — `test_refuses_emit_*`,
  `test_warns_emit_*`, `test_confidence_high_requires_5_locked`,
  `test_safety_k_removed_no_subtraction`.

Validation:
```
python3 -m py_compile scripts/nosf_analyze.py
python3 -m py_compile scripts/test_nosf_analyze.py
python3 scripts/test_nosf_analyze.py
python3 scripts/test_nosf_live_tuner.py
```

Commit subject: `feat(analyze): require locked buckets to emit`

### 8.4 — 2.12.3 — Analyzer precision-weighted baseline and bias

**Goal:** centroid from inverse-variance weighting across
qualifying buckets, not dominant-single-bucket. 5/95 trim.

Files:
- `scripts/nosf_analyze.py` — replace `dominant` and the bias
  computation per §3.4 and §3.5.
- `scripts/test_nosf_analyze.py` —
  `test_precision_weighted_baseline_across_buckets`,
  `test_dominant_single_bucket_does_not_dictate_baseline`,
  `test_bias_only_from_qualifying_buckets`,
  `test_field_oscillation_repro`.
- `tests/fixtures/phase_2_12_field_csv.csv`,
  `tests/fixtures/phase_2_12_field_state.json` — synthesize.

Validation: same as 2.12.2 plus the new tests.

Commit subject: `feat(analyze): precision weighted baseline and bias`

### 8.5 — 2.12.4 — Analyzer σ_mm from BP, not BL

**Goal:** stop reading `BL` (sps) as `sigma_mm`. Compute σ of `BP`
column directly. Clamp `buf_variance_blend_ref_mm` to physical
range.

Files:
- `scripts/nosf_analyze.py` — `read_csv_runs` column map update,
  `buffer_position_sigma_mm` helper, `var_ref` computation
  rewrite. Also reconsider `mid_creep_timeout_ms` per §3.6:
  conservative path is to set its confidence to `DEFAULT` and
  not emit a learned value until a better heuristic exists.
- `scripts/test_nosf_analyze.py` —
  `test_buf_variance_blend_ref_mm_from_bp_not_bl`,
  `test_mid_creep_timeout_default_when_insufficient_data`.

Validation: same as 2.12.3.

Commit subject: `fix(analyze): derive sigma mm from bp not bl`

### 8.6 — 2.12.5 — Contributors block + docs

**Goal:** patch contributors block; MANUAL/KLIPPER/README/CONTEXT
updates.

Files:
- `scripts/nosf_analyze.py` — emit `[nosf_contributors]` block.
- `scripts/test_nosf_analyze.py` —
  `test_contributors_block_emitted`.
- `MANUAL.md` — analyzer mode-semantics section update,
  `--force`, contributors block reading guide,
  `state-info` noise-ratio reason.
- `KLIPPER.md` — "Why analyzer refuses to emit" subsection;
  3-5 sentence reading guide for `[nosf_contributors]`.
- `README.md` — one line update.
- `CONTEXT.md` — Phase 2.12 history entry.

Validation: same as 2.12.4.

Commit subject: `docs(analyze): document phase 2.12 mode and contributors`

### 8.7 — 2.12.6 — Pi validation + constant tuning

**Goal:** run §6 Pi validation; tune `NOISE_RATIO_THR` and
analyzer thresholds if needed.

Files:
- `scripts/nosf_live_tuner.py` — only if
  `NOISE_RATIO_THR` needs adjustment per §6.3.
- `scripts/nosf_analyze.py` — only if a threshold needs
  adjustment.
- `TASK.md` — `### Phase 2.12 Pi Validation` block per §6.4.

Validation: full test command suite from §5.4 plus the Pi soak.

Commit subject: `docs(task): record phase 2.12 pi validation`
(or `tune(...): adjust phase 2.12 thresholds`).

---

## 9. Risks and open questions

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R-1 | `NOISE_RATIO_THR = 0.25` is wrong for this hardware; either too tight (no locks) or too lax (locking noisy buckets). | Medium | Pi soak in 2.12.6 tunes; constant is at module top; adjustment is one-line. |
| R-2 | Precision-weighted baseline is dominated by a single low-σ bucket whose σ is artificially low (under-sampled). | Medium | 5/95 trim across qualifying buckets; cap per-bucket weight at `MAX_WEIGHT = 5 * median(weights)` (deferred to 2.12.6 if observed). |
| R-3 | `mid_creep_timeout_ms` redefinition heuristic is also wrong. The conservative fallback (keep current default) avoids regression but does not learn the value. | High | 2.12.4 deliberately falls back to default with `DEFAULT` confidence. A correct heuristic is out of scope. Defer to a hypothetical Phase 2.13. |
| R-4 | `buf_variance_blend_ref_mm` computed from σ of `BP` is itself the wrong target; the firmware's variance-blend reference may need a different physical quantity. | Medium | Clamp to `[0.1, 5.0]` mm physically plausible range. If clamp fires, log warning. Pi soak validates qualitative behavior; if wrong, defer to a follow-up. |
| R-5 | `--mode safe` refuses everything for a new operator because the very first soak has zero LOCKED buckets. | Medium | Document `--mode aggressive` as the bootstrap path in 2.12.5 KLIPPER.md; safe is for re-tunes. |
| R-6 | Analyzer test fixture (`phase_2_12_field_csv.csv`) is large; commit may grow repo. | Low | Trim to ≤ 500 rows that capture the oscillation pattern; not a full real-print capture. |
| R-7 | Removing `SAFETY_K` breaks any external tooling that imports it. | Low | Keep `SAFETY_K = {}` as a deprecated stub for one release cycle; emit a warning if it's accessed. Or remove cleanly and document in commit body. |
| R-8 | Bucket fragmentation (150 buckets, median n=14) remains a problem; analyzer rigor helps but doesn't fix it. | High | Out of scope for Phase 2.12. Defer to a follow-up that coarsens bin width to 50 mm³/s; that's a tuner-side schema-impacting change with a migration of its own. |

Open questions:

- **Q-2.12-A.** Should `NOISE_RATIO_THR` itself be per-feature? Outer wall and inner wall have systematically different σ/x. Plan currently uses one global constant; defer to operator feedback.
- **Q-2.12-B.** Should `baseline_rate` be split per feature
  (`Outer_wall_baseline`, `Inner_wall_baseline`)? Currently it is a
  single firmware tunable. Out of scope until firmware supports
  per-feature baselines.
- **Q-2.12-C.** Bucket bin width 25 → 50 mm³/s — when?

---

## 10. Rollback plan

- **Per-milestone:** each commit is independently revertable. 2.12.1
  reverts to the absolute noise gate without affecting analyzer.
  2.12.2 reverts to old mode semantics without affecting tuner.
  2.12.3 reverts the precision weighting without losing the LOCKED
  floor.
- **Schema:** no bump, so no migration to undo.
- **State file:** untouched; operator's tune DB is preserved.

---

## 11. Acceptance criteria for Phase 2.12

1. All §5.1, §5.2, §5.3 tests green.
2. `python3 -m py_compile scripts/*.py` exits 0.
3. On the operator's Pi, three back-to-back calibration prints of
   the same model satisfy §6.2 success criteria.
4. `nosf_analyze.py --mode safe` against a zero-LOCKED state file
   produces a refusal banner and exits non-zero.
5. `nosf_analyze.py --mode aggressive` against the same state
   produces a warning banner and emits a patch with `LOW` or
   `DEFAULT` confidence labels, not `HIGH`.
6. After a real calibration soak yields ≥ 5 LOCKED buckets, the
   recommended `baseline_rate` is within ±50 sps of the
   subsequent run's recommendation. No more
   `1600 → 328 → 387 → 150` oscillations.
7. `buf_variance_blend_ref_mm` recommendation is in `[0.1, 5.0]`
   mm in every patch.
8. `MANUAL.md`, `KLIPPER.md`, `README.md`, `CONTEXT.md` reflect
   the new analyzer semantics, the `--force` flag, and the
   contributors block.
9. `TASK.md` contains a `### Phase 2.12 Pi Validation` block.

---

## 12. First implementation recommendation

Land **2.12.1 first**. The tuner-side gate fix is the prerequisite
for the analyzer's LOCKED floor to ever pass: with the absolute
noise gate, no bucket ever locks, and the LOCKED floor refuses to
emit. Without 2.12.1, the operator cannot validate 2.12.2.

After 2.12.1 lands and produces LOCKED buckets on the operator's
existing soak, 2.12.2 → 2.12.5 can land in sequence with the
analyzer changes verified against real LOCKED state.

If 2.12.6 Pi validation reveals tuning constants need adjustment,
treat as a constant-tuning iteration; do not redesign the
ratio gate or the weighting model. The constants are the only
place tunable without re-validating the algorithm.

---

## 13. Cross-references

- `SYNC_REFACTOR_PHASE_2_9.md` §15-§17 — observe-only contract,
  schema 1/2/3 chain, Path A/B lock criteria, watermark layout.
- `SYNC_REFACTOR_PHASE_2_10.md` §6-§9 — sidecar+UDS marker flow.
- `SYNC_REFACTOR_PHASE_2_11.md` — residual statistics fields,
  three-channel unlock, chatter repro pattern.
- `scripts/nosf_live_tuner.py` `_maybe_lock`, `bucket_wait_reason`,
  `V_NOISE_LOCK_THR` — current absolute gate.
- `scripts/nosf_analyze.py:207` — `dominant` single-bucket pick.
- `scripts/nosf_analyze.py:211` — `est_p50 - safety * est_sigma`.
- `scripts/nosf_analyze.py:234` — p95 mid_timeout.
- `scripts/nosf_analyze.py:118` — `BL` → `sigma_mm` map bug.
- `scripts/nosf_analyze.py:268` — `var_ref` quantization.

---

*End of Phase 2.12 plan.*
