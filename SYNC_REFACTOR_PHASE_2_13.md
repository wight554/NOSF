# Sync Refactor — Phase 2.13

> **Phase 2.13 — Acceptance Gate Parity**
>
> Phase 2.12 hardened the analyzer recommendation path: LOCKED-bucket
> floor, precision-weighted baseline and bias, σ/x noise gate. Pi
> validation showed those recommendations are stable when the
> analyzer is run on each calibration print alone: across run2, run3,
> run4 the standalone outputs are within 33 sps for `baseline_rate`
> and 0.003 for `sync_trailing_bias_frac`. But running the analyzer
> over all three CSVs with `--acceptance-gate` fails with a 704 sps
> baseline delta and 0.351 bias delta. The gate measures something
> different from the recommendation path.
>
> Phase 2.13 fixes the gate so that it asks the same question the
> recommendation path answers: "If we ran the analyzer on just this
> one run, what would it say?" — and compares those per-run answers,
> not per-bucket per-run raw medians. Firmware unchanged. Schema
> unchanged. On-disk CSVs and the operator's existing tune DB
> (`buckets-myprinter.json`) remain usable.
>
> This document is a **plan, not an implementation**. No code lands
> as part of this commit. Implementation begins at milestone 2.13.1.

---

## 1. Problem Summary

The acceptance gate fails on field data that the recommendation
path agrees is consistent. Two root issues:

1. **The gate's consistency check is structurally different from
   the recommendation it gates.** `consistency_by_run()` builds
   per-bucket lists of per-run est medians, then reports the max
   delta across all buckets. The recommendation path produces a
   single precision-weighted estimate across all qualifying
   buckets. The two answer different questions, so they disagree.

2. **Per-bucket inter-run variation is treated as inconsistency.**
   A v_fil bin like `Outer wall_v475` legitimately contains many
   physically different extrusion regimes across runs (different
   layer heights, speed factors, temperature settling). A 700 sps
   est swing inside one bin between runs is normal data scatter,
   not a gate failure.

3. **Immature / fallback per-run estimates poison the gate.** When
   a bucket only has 5 rows in run3 vs 1000 rows in run2, the
   run3 median is statistical noise, not a comparable estimate.
   The current gate weights them equally.

4. **The coverage gate uses raw MID-row counts in LOCKED buckets.**
   On short calibration cubes with many distinct feature/v_fil
   combinations but only a handful of LOCKED buckets, coverage
   under 80% trips even though those LOCKED buckets contain the
   meaningful signal the analyzer actually uses.

Phase 2.13 fixes these and adds enough diagnostics so the operator
can see exactly which runs contributed and why.

---

## 2. Current Evidence

### 2.1 Operator field run (May 2026, Phase 2.12 post-ship)

Same tuner DB (`buckets-myprinter.json`) preserved across runs.
Standalone analyzer invocations on each CSV alone produce stable
recommendations:

| Run | `baseline_rate` | `sync_trailing_bias_frac` |
|---|---|---|
| run2 alone | 718 | 0.303 |
| run3 alone | 694 | 0.303 |
| run4 alone | 727 | 0.306 |

Spread:
- baseline: max − min = 33 sps
- bias: max − min = 0.003

Combined invocation with `--acceptance-gate`:

```
acceptance gate failed: coverage 74.5% < 80.0%
acceptance gate failed: baseline consistency delta 704 sps > 50
acceptance gate failed: bias consistency delta 0.351 > 0.050
```

The gate's `max_baseline_delta = 704` and `max_bias_delta = 0.351`
do not match the standalone recommendation spread by orders of
magnitude. Something other than the recommendation path is producing
those numbers.

### 2.2 What the gate is actually measuring

The 704 sps and 0.351 bias delta correspond to per-bucket
inter-run scatter. Several v_fil bins on this hardware show very
wide est ranges between runs because:

- Layer height varies (bottom layer 0.30 mm, body layer 0.20 mm),
  changing the BP/RT relation inside the same bin.
- Speed factor / extrude factor differ slightly between Klipper
  warmups.
- Some features visit a bin only briefly in one run, producing a
  median dominated by 3-5 noise samples.

These produce 600-800 sps swings inside individual buckets, but
those buckets are exactly the kind of noisy data the Phase 2.12
σ/x gate already excludes from the recommendation. The gate
ignores Phase 2.12's qualifying-bucket filter and consumes raw
mid_rows.

---

## 3. Source Walkthrough Findings

### 3.1 `scripts/nosf_analyze.py:452-468` — `consistency_by_run`

```python
def consistency_by_run(runs):
    baseline_vals = defaultdict(list)
    bias_vals = defaultdict(list)
    for run in runs:
        grouped = defaultdict(list)
        for r in mid_rows(run["rows"]):
            grouped[bucket_label(r.get("feature", ""),
                                 to_float(r.get("v_fil")))].append(r)
        for label, bucket_rows in grouped.items():
            ests = [to_float(r.get("est_sps")) for r in bucket_rows]
            bp_delta = [to_float(r.get("bp_mm")) - to_float(r.get("rt_mm"))
                        for r in bucket_rows]
            if ests:
                baseline_vals[label].append(median(ests))
            if bp_delta:
                bias_vals[label].append(
                    clamp(0.4 + stats.mean(bp_delta) / 7.8,
                          BIAS_SAFE_MIN, BIAS_SAFE_MAX))
    max_baseline_delta = max(
        (max(vs) - min(vs) for vs in baseline_vals.values() if len(vs) >= 2),
        default=0.0)
    max_bias_delta = max(
        (max(vs) - min(vs) for vs in bias_vals.values() if len(vs) >= 2),
        default=0.0)
    return max_baseline_delta, max_bias_delta
```

Findings:

- The function takes **all** `mid_rows`. No `qualifying` filter, no
  LOCKED filter, no σ/x filter. Raw row-level data.
- Each `baseline_vals[label]` is a list of **per-run per-bucket est
  medians**, one entry per run that has any rows in that bucket.
- The gate reports the **max delta across all buckets**. So one
  noisy bucket dominates the entire delta value.
- There is no minimum-sample-count guard: a bucket with 3 rows in
  one run and 1500 rows in another contributes equally to the
  delta.
- Bias is computed as `mean((BP - RT)/7.8)` from raw rows, not from
  the qualifying buckets the recommendation path uses.

### 3.2 `scripts/nosf_analyze.py:303-449` — `compute_recommendations`

This is the path the standalone recommendations come from. It:

- Loads `state_buckets` (Phase 2.12).
- Filters to `qualifying = locked or force_qualifying`.
- Trims 5/95 percentile of `x` across qualifying labels.
- Precision-weights with `bucket.n / max(resid_var_ewma, V_NOISE_FLOOR)`.
- Produces a single `baseline_rate` and `sync_trailing_bias_frac`.

Critical observation: `compute_recommendations` consumes **the
state file** as the primary source of bucket centroids and
weights. The CSV rows are used for `mid_creep_*`, `BPV` sigma,
and a fallback path when no qualifying buckets exist. The
**state is shared across all three runs**, so calling
`compute_recommendations` with run2 CSV alone vs run3 CSV alone vs
run4 CSV alone yields almost identical numbers — that is exactly
what the operator observes (718 / 694 / 727).

The gate, by contrast, ignores state entirely. It only looks at
the raw CSV rows.

### 3.3 `scripts/nosf_analyze.py:471-522` — `acceptance_gate`

```python
coverage = (len(locked_mid) / len(mids)) if mids else 0.0
if coverage < 0.80:
    reasons.append(...)

base_delta, bias_delta = consistency_by_run(runs)
if base_delta > 50.0:
    reasons.append(...)
if bias_delta > 0.05:
    reasons.append(...)

sigma_vals = [to_float(r.get("sigma_mm")) ...]
sigma_p95 = percentile(sigma_vals, 95)
if sigma_p95 >= current["buf_variance_blend_ref_mm"]:
    reasons.append(...)

if len(runs) < 3: ...
durations = [run_duration_s(run) for run in runs]
if not (durations and all(d >= 600.0 for d in durations) or sum(durations) >= 1800.0):
    reasons.append(...)
if len(locked) < 3: ...

telemetry = {"ADV_DWELL_STOP": 0, "ADV_RISK_HIGH": 0, "EST_FALLBACK": 0}
```

Additional findings:

- `sigma_p95` here reads CSV `sigma_mm` column. Phase 2.12 removed
  the `BL → sigma_mm` alias from the recommendation path but did
  not update the gate, so for any CSV that does not contain a real
  `sigma_mm` column (i.e., all post-Phase-2.9 captures from
  `nosf_live_tuner.py --csv-out`), this collapses to 0.0 and the
  gate quietly passes the σ check. The `buf_variance_blend_ref_mm`
  comparison against current is therefore latent dead code for
  current CSVs.
- `telemetry` is hard-coded to zero. The gate never inspects logs
  for `EV:` events. This is pre-Phase-2.10 vestige.
- Coverage uses raw MID rows in LOCKED buckets divided by all MID
  rows. No Phase 2.12 qualifying filter, no minimum-n weighting.

### 3.4 `scripts/test_nosf_analyze.py`

Existing tests cover the recommendation path (Phase 2.12) but no
test exercises the acceptance gate's consistency or coverage
computation directly. There is no fixture that asserts gate output
parity with standalone recommendations.

### 3.5 `scripts/nosf_live_tuner.py` cross-reference

No tuner-side change required. The schema-4 bucket fields
(`resid_var_ewma`, `n`, `x`, `last_seen`, `cumulative_mid_s`,
`locked`/`state`) are already what the gate needs. `on_m118`,
`SegmentMatcher`, `gcode_marker.py`, and the live tuner learning
loop are untouched by Phase 2.13.

---

## 4. Root-Cause Hypotheses

| ID | Hypothesis | Confidence | Evidence |
|---|---|---|---|
| H-1 | Gate consistency measures per-bucket per-run est medians instead of per-run analyzer-output recommendations. | HIGH | Code inspection of §3.1. Standalone outputs (33 sps spread) vs gate output (704 sps delta) differ by 20×. |
| H-2 | One or two noisy buckets (wide intra-bin scatter) dominate the gate's max-delta reduction. | HIGH | Plausible from the cube geometry; v_fil bins like `Outer wall_v475` collect heterogeneous samples. Confirmable once 2.13.1 fixture exists. |
| H-3 | Immature per-run buckets (few rows) contribute to the delta as if they were mature estimates. | MEDIUM | Gate has no min-n guard inside `consistency_by_run`. |
| H-4 | Coverage gate uses raw MID-row counts; for short cube prints with many feature types, the LOCKED fraction is naturally below 80% even when the LOCKED buckets carry the bulk of the contributor weight the analyzer actually uses. | HIGH | Phase 2.12 recommendation path uses `contributor_entries` and `trimmed_labels_by_x`; gate uses neither. |
| H-5 | σ_p95 check in gate is dead code on current CSVs (no `sigma_mm` column). | MEDIUM | Code inspection of §3.3. Gate currently does not fail or pass on this check; it silently sees 0.0. |
| H-6 | Telemetry counters are hard-coded zero; gate is not actually inspecting Klipper logs for ADV/EST events. | LOW | Code inspection. Out of scope unless 2.13 incidentally surfaces it; the operator's report does not implicate telemetry. |

H-1, H-2, H-4 explain the operator's evidence completely. H-3 is a
contributing factor. H-5 and H-6 are documentation/code-hygiene
findings that 2.13 should surface but does not have to fix to ship.

---

## 5. Proposed Algorithm

### 5.1 Single recommendation helper, shared by gate and emission

Introduce one analyzer function that all callers use to derive a
recommendation set for a given CSV subset and the operator's
state. Pseudocode:

```python
def recommend_for_subset(runs_subset, rows_subset, state_buckets,
                         current, mode, force, include_stale):
    """Produce a Recommendation object for one CSV slice.
    Returns the same shape as compute_recommendations(): a dict of
    key -> (value, confidence, detail). All upstream filters
    (LOCKED, σ/x, n>=50, cumulative_mid_s, 5/95 trim, precision
    weight) apply identically to gate consistency checks and to
    final patch emission.
    """
```

Implementation: refactor the body of `compute_recommendations` so
that this function is the single source of truth. The current
`compute_recommendations(rows, runs, ...)` becomes a thin wrapper
around `recommend_for_subset(runs, rows, ...)`. The gate's
consistency code calls the same helper, once per run, with that
run's slice of CSV rows. This is the **parity** requirement:
emission and gate cannot diverge by construction.

### 5.2 Per-run mature-estimate filter

For each run, compute its standalone recommendation via
`recommend_for_subset([run], run["rows"], ...)`. Then classify:

- **Comparable**: confidence on `baseline_rate` is `HIGH` or
  `MEDIUM`, AND the number of contributing buckets is ≥
  `MIN_COMPARABLE_BUCKETS = 3`, AND at least one of those buckets
  has the run's MID rows representing ≥ `MIN_RUN_BUCKET_ROWS = 50`
  samples for that bucket.
- **Immature**: comparable conditions not met. Run is skipped from
  consistency reduction with reason recorded.

Reasons recorded per-skip:

- `LOW confidence (n contributing buckets = K)`
- `DEFAULT confidence`
- `REFUSED: no LOCKED buckets` (only possible if the state has
  zero LOCKED and the run was processed under safe mode)
- `<MIN_RUN_BUCKET_ROWS rows per contributing bucket`

### 5.3 Consistency reduction over comparable runs only

```python
comparable = [r for r in runs if classify(r) == "comparable"]
baseline_estimates = [recommend_for_subset([r], r["rows"], ...).baseline_rate.value
                      for r in comparable]
bias_estimates = [recommend_for_subset([r], r["rows"], ...).bias_frac.value
                  for r in comparable]
```

Then:

- If `len(comparable) < 2`: skip consistency check entirely. Gate
  reports `comparable_runs N < 2; consistency check skipped`. This
  is **not** a gate failure on its own. It is recorded in the
  patch as a diagnostic.
- Else: `baseline_delta = max(baseline_estimates) - min(baseline_estimates)`.
  `bias_delta = max(bias_estimates) - min(bias_estimates)`.
- Compare against existing thresholds: `baseline_delta > 50` →
  fail; `bias_delta > 0.05` → fail.

This is the central change. For the operator's evidence:
`baseline_estimates = [718, 694, 727]` → delta 33 → PASS.
`bias_estimates = [0.303, 0.303, 0.306]` → delta 0.003 → PASS.

### 5.4 Coverage gate: contributor-mass formulation

Replace raw-row coverage with a contributor-mass coverage that
mirrors what the recommendation path actually consumes:

```python
qualifying = locked or force_qualifying_labels(state_buckets, ...)
contributor_n = sum(state_buckets[label].get("n", 0) for label in qualifying)
total_n = sum(state_buckets[label].get("n", 0)
              for label in state_buckets if not label.startswith("_"))
contributor_mass = contributor_n / max(total_n, 1)
```

Default threshold: `contributor_mass >= 0.50` (50% of total bucket
sample mass lives in qualifying buckets).

Rationale: 80% raw-row coverage was a guess. Contributor mass
aligns with the precision-weighted aggregation Phase 2.12 actually
uses; if 50% of the operator's total sample evidence is in
qualifying buckets, the recommendation has the same epistemic
weight as if 50% of MID time were spent in LOCKED zones.

Soft warning tier: if `contributor_mass < 0.65`, emit a warning
banner but still pass. Hard failure: `< 0.50`.

The current 80% raw-row gate is retained as **a separate WARN
tier**, downgraded from FAIL to WARN. Operators reading the patch
see both numbers and can decide whether to investigate.

### 5.5 σ_p95 gate cleanup

Remove the `sigma_mm` column dependency from `acceptance_gate`.
Compute `buffer_position_sigma_mm` from the qualifying buckets'
contributor rows, same as the recommendation path. Compare the
p95 against `current["buf_variance_blend_ref_mm"]`. If the
clamped Phase 2.12 σ_BP is ≥ current ref, raise the same warning
that the recommendation path raises; do not duplicate the logic
inline.

### 5.6 Telemetry counters

The hard-coded zeros in §3.3 are noted but **out of scope** for
Phase 2.13. The plan documents the finding in the patch comments
("# Telemetry: not currently parsed from logs; counters reflect
pending feature, not real events") so the operator does not
misread zero as a clean bill of health. A follow-up phase would
have to wire Klipper log parsing or status-line event tracking
into the analyzer, which is out of Phase 2.13's scope.

### 5.7 Patch diagnostics in rejected output

When the gate produces a rejected patch, the patch includes:

```
# Acceptance gate: FAIL
# Per-run estimates used in consistency check:
#   run 1 (path /home/pi/nosf-runs/run2.csv):
#       baseline=718, bias=0.303, contributors=12, conf=HIGH
#   run 2 (path /home/pi/nosf-runs/run3.csv):
#       baseline=694, bias=0.303, contributors=11, conf=HIGH
#   run 3 (path /home/pi/nosf-runs/run4.csv):
#       baseline=727, bias=0.306, contributors=10, conf=HIGH
# Comparable runs: 3 of 3
# Skipped runs:
#   (none)
# Consistency: baseline delta=33 sps, bias delta=0.003
# Coverage: contributor mass 78.2%, raw MID coverage 74.5%
# Failure reasons:
# - (none)
```

If the gate passes, the same block is emitted with `Acceptance
gate: PASS`. The block is also written when a check is skipped:

```
# Comparable runs: 1 of 3
# Skipped runs:
#   run 2: LOW confidence (2 contributing buckets)
#   run 3: <50 rows per contributing bucket
# Consistency: skipped (need ≥ 2 comparable runs)
```

### 5.8 Optional CLI: `--acceptance-debug`

For automated tooling, add a no-op CLI flag `--acceptance-debug`
that prints the per-run-estimate block to stderr in addition to
emitting it in the patch. Pure convenience; not strictly required
to ship 2.13. The default behavior (writing diagnostics into the
rejected patch) already satisfies the user's "always include
enough diagnostics in rejected patch comments" requirement.

### 5.9 Constants and defaults

```python
MIN_COMPARABLE_BUCKETS = 3
MIN_RUN_BUCKET_ROWS = 50
CONTRIBUTOR_MASS_PASS = 0.50
CONTRIBUTOR_MASS_WARN = 0.65
RAW_COVERAGE_WARN = 0.80         # downgraded from FAIL to WARN
BASELINE_DELTA_FAIL_SPS = 50.0
BIAS_DELTA_FAIL = 0.05
```

All at module top, documented. Operator can tune via constant
edit if Pi soak shows 50% mass too lax / 65% too strict.

---

## 6. Milestones

> One milestone = one commit + push. Per-milestone validation
> gates below. `Generated-By: GPT-5.5 (High)` footer.
> Plan-only milestone 2.13.0 is implicit; this document is it.

### 6.1 — 2.13.1 — Reproduce field acceptance-gate mismatch

**Goal:** add a fixture and test that fails on current main and
will turn green after 2.13.2 + 2.13.3 land.

Files:
- `tests/fixtures/phase_2_13_three_run_state.json` (new) —
  synthetic state mirroring operator's `buckets-myprinter.json`.
  Contains ~30 buckets total, ~8 LOCKED, with realistic
  `resid_var_ewma`, `n`, `x`, `last_seen`. Keep file under 200
  lines.
- `tests/fixtures/phase_2_13_run_a.csv`,
  `phase_2_13_run_b.csv`, `phase_2_13_run_c.csv` (new) — three
  synthetic CSVs whose standalone recommendations under Phase
  2.12 land at baseline near 700-730 sps and bias near 0.303-0.306,
  but whose raw per-bucket per-run est medians vary by 600+ sps
  across runs in at least one v_fil bin (matching the operator's
  pattern). Keep each fixture under 500 rows.
- `scripts/test_nosf_analyze.py` — add
  `test_phase_2_13_field_repro_gate_should_pass`. Pre-2.13.2 main
  asserts the test currently produces the mismatched result
  (delta ≥ 600 sps from gate vs delta ≤ 50 from recommendations);
  marked with `# EXPECTED to expose bug until 2.13.2` and prints
  the discrepancy without `sys.exit(1)` so the existing test
  suite still passes.

Validation gate:
```
python3 -m py_compile scripts/nosf_analyze.py
python3 -m py_compile scripts/test_nosf_analyze.py
python3 scripts/test_nosf_analyze.py
```

Commit subject: `test(analyze): add phase 2.13 acceptance-gate repro fixture`

### 6.2 — 2.13.2 — Shared recommendation path

**Goal:** factor `compute_recommendations` so the gate and emission
share `recommend_for_subset(runs_subset, rows_subset, ...)`.
Gate's `consistency_by_run` is replaced with a per-run
recommendation reduction.

Files:
- `scripts/nosf_analyze.py` — refactor per §5.1 and §5.3.
  Replace the function body of `consistency_by_run` with the new
  comparable-run reduction. Keep the function signature so any
  callers continue to work. `acceptance_gate` calls
  `consistency_by_run(runs, state_buckets, current, mode, force,
  include_stale)` — note the signature **gains** the state-aware
  parameters.
- `scripts/test_nosf_analyze.py` — add
  `test_consistency_uses_recommendation_path`,
  `test_consistency_matches_standalone_recommendations`.
  Flip the 2.13.1 repro test to a hard assertion (the
  recommendation-vs-gate parity now holds).

Validation gate: same commands as 2.13.1.

Commit subject: `feat(analyze): share recommendation path with gate`

### 6.3 — 2.13.3 — Mature-run filtering and diagnostics

**Goal:** classify runs as comparable / immature; skip immature
runs; emit per-run estimate diagnostics in the patch.

Files:
- `scripts/nosf_analyze.py` — implement `classify_run` per §5.2.
  Update `acceptance_gate` to skip immature runs from the
  consistency reduction and to record skip reasons. Update
  `write_patch` to emit the per-run block from §5.7. New
  constants `MIN_COMPARABLE_BUCKETS = 3`,
  `MIN_RUN_BUCKET_ROWS = 50`.
- `scripts/test_nosf_analyze.py` — add
  `test_immature_run_skipped_with_reason`,
  `test_only_one_comparable_run_skips_consistency_check`,
  `test_three_run_field_repro_passes_after_filter`,
  `test_true_disagreement_still_fails`,
  `test_rejected_patch_includes_per_run_estimates`.

Validation gate: same commands as 2.13.2 plus the new tests.

Commit subject: `feat(analyze): filter immature runs in gate`

### 6.4 — 2.13.4 — Coverage gate refinement

**Goal:** introduce contributor-mass coverage; downgrade raw-row
80% threshold from FAIL to WARN.

Files:
- `scripts/nosf_analyze.py` — implement contributor-mass coverage
  per §5.4. Constants `CONTRIBUTOR_MASS_PASS = 0.50`,
  `CONTRIBUTOR_MASS_WARN = 0.65`, `RAW_COVERAGE_WARN = 0.80`.
  Clean up the σ_p95 path per §5.5 (use BP-derived σ from the
  qualifying buckets; no `sigma_mm` column dependency).
  Document the telemetry hard-zero as pending follow-up per §5.6.
- `scripts/test_nosf_analyze.py` — add
  `test_contributor_mass_below_threshold_fails`,
  `test_contributor_mass_warn_tier_emits_warning_but_passes`,
  `test_raw_coverage_below_80_does_not_fail_alone`,
  `test_sigma_p95_uses_bp_derived_value`.

Validation gate: same commands as 2.13.3.

Commit subject: `feat(analyze): contributor-mass coverage gate`

### 6.5 — 2.13.5 — Docs + Pi validation against existing DB

**Goal:** docs; rerun analyzer on operator's existing CSVs +
existing tune DB; record Pi validation results.

Files:
- `MANUAL.md` — analyzer acceptance-gate section update; new
  failure / skip reasons; per-run estimate block guide.
- `KLIPPER.md` — "Why the acceptance gate skipped a run"
  subsection (3-5 sentences).
- `README.md` — one line update under analyzer features.
- `CONTEXT.md` — Phase 2.13 history entry.
- `TASK.md` — `### Phase 2.13 Pi Validation` block with the
  re-run gate output on `~/nosf-runs/run2.csv`,
  `~/nosf-runs/run3.csv`, `~/nosf-runs/run4.csv` against the
  current `~/nosf-state/buckets-myprinter.json`.

No reprint required for 2.13.5; the operator's existing CSVs and
DB are sufficient because the gate change is offline-only. The
expected outcome on the operator's evidence: gate passes
consistency, possibly emits a contributor-mass warning depending
on Phase 2.12 LOCKED bucket count.

Validation gate: full test suite + the rerun.

Commit subject: `docs(analyze): document phase 2.13 acceptance gate`

---

## 7. Validation Gates

Per milestone, run **all** of:

```
python3 -m py_compile scripts/nosf_analyze.py
python3 -m py_compile scripts/test_nosf_analyze.py
python3 scripts/test_nosf_analyze.py
python3 scripts/test_nosf_live_tuner.py
python3 scripts/test_phase_2_10_parity.py
python3 scripts/test_klipper_motion_tracker.py
python3 scripts/test_gcode_marker.py
python3 -m py_compile scripts/*.py
```

All must exit 0. The Phase 2.13.1 repro test is allowed to print
`EXPECTED FAIL` in 2.13.1 only; it must pass as a hard assertion
from 2.13.2 onward.

After 2.13.5, the operator's Pi validation re-runs the analyzer
manually:

```
python3 scripts/nosf_analyze.py \
    --in ~/nosf-runs/run2.csv ~/nosf-runs/run3.csv ~/nosf-runs/run4.csv \
    --state ~/nosf-state/buckets-myprinter.json \
    --machine-id myprinter \
    --acceptance-gate \
    --mode safe \
    --out /tmp/nosf-gate.ini
```

Expected outcome under §5 algorithm:

- Per-run estimates listed in `/tmp/nosf-gate.ini` matching the
  standalone runs (718 / 694 / 727 ± weighting variation).
- Baseline delta ≈ 33 sps, bias delta ≈ 0.003. Both PASS.
- Coverage block shows contributor mass; if mass ≥ 50% (likely
  given 8+ LOCKED buckets), PASS. Raw 74.5% raw-row coverage
  emits WARN, not FAIL.

If the gate still fails for non-spurious reasons (e.g.,
insufficient LOCKED buckets to meet contributor-mass 50%), the
operator can run with `--force` to bypass the LOCKED floor per
Phase 2.12 semantics, or run a 4th calibration print.

---

## 8. Regression Risks

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R-1 | Refactor of `compute_recommendations` breaks the recommendation path. | Medium | Phase 2.12 tests in `test_nosf_analyze.py` exercise the recommendation path with 11+ existing assertions; refactor must keep them green. The new `recommend_for_subset` is exercised by both the existing tests and the new gate tests. |
| R-2 | The new comparable-run filter is too strict; small calibration prints all classify as immature. | Medium | `MIN_COMPARABLE_BUCKETS = 3` is conservative. If 2.13.5 Pi validation shows too-strict, lower to 2 (still ≥ Phase 2.12 MEDIUM threshold) before shipping. |
| R-3 | Contributor mass 50% is wrong for short cubes. | Medium | Threshold tunable via the named constant; 2.13.5 Pi validation tunes if needed; raw-row coverage WARN remains as fallback observability. |
| R-4 | Backward compatibility: existing tooling relies on `consistency_by_run(runs)` two-argument signature. | Low | The function gains parameters with defaults that preserve the legacy single-arg behavior, OR a new helper is named differently. Implementation prefers a renamed helper and removes the old one cleanly. Document in commit body. |
| R-5 | The 2.13.1 repro fixture lies (the synthetic data does not actually reproduce the operator's discrepancy). | Medium | Fixture design must be verified before 2.13.2 commits: run the pre-2.13.2 analyzer against the fixture and confirm the gate fails with ≥ 600 sps delta. If it does not, expand the fixture until it does. |
| R-6 | Phase 2.12 σ_p95 removal in 2.13.4 inadvertently changes a `buf_variance_blend_ref_mm` recommendation that the operator already committed. | Low | The recommendation path already uses BP-derived σ since Phase 2.12 (commit b0c2152); 2.13.4 only fixes the gate, not the emission. Verify with a regression test. |
| R-7 | Telemetry counters being hard-coded zero is a real bug that 2.13 documents but does not fix. | Low | Phase 2.13 scope explicitly excludes wiring real telemetry parsing. The documentation in §5.6 prevents the operator from misreading the zero counters. |
| R-8 | Gate becomes too permissive; a real regression slips past. | Medium | Add `test_true_disagreement_still_fails` in 2.13.3 with synthetic runs whose per-run recommendations differ by 200 sps. Gate must still fail in that case. |
| R-9 | `--force` interaction with the new gate is undertested. | Low | Add a test where state has zero LOCKED, `--force` is passed, and the gate runs the per-run consistency over force-qualifying contributors. Expected: WARN, not FAIL. |

---

## 9. GPT-5.5 Implementation Prompt

```
You are an embedded-systems and host-tooling engineer working on
NOSF firmware host tooling: RP2040 dual-lane MMU/RELOAD controller
for FYSETC ERB V2.0.

Repository: /Users/Volodymyr_Zhdanov/playground/nightowl-standalone-controller
(or pull from origin/main).

Task: Implement Phase 2.13 — acceptance-gate parity and mature-run
consistency. Plan committed at SYNC_REFACTOR_PHASE_2_13.md.
Phase 2.13.0 (this plan) is DONE. Begin at 2.13.1.

Source of truth: SYNC_REFACTOR_PHASE_2_13.md. Read fully. Cross-
reference SYNC_REFACTOR_PHASE_2_12.md for the LOCKED-bucket floor,
precision-weighted recommendation path, σ/x noise gate, --force
flag semantics, [nosf_contributors] block — all of which must be
preserved.

Mandatory pre-work, in order:
1. Read AGENTS.md fully. Post the session-start banner per the
   "Session Start Protocol".
2. Read TASK.md. Confirm Phase 2.12 marked DONE.
3. Read SYNC_REFACTOR_PHASE_2_13.md fully (all 9 sections).
4. Read SYNC_REFACTOR_PHASE_2_12.md (the recommendation path you
   must refactor without breaking).
5. Read these files in their current state — they have been
   modified through Phase 2.12 and several hotfixes; do NOT
   assume any pristine form:
   - scripts/nosf_analyze.py
   - scripts/test_nosf_analyze.py
   - scripts/nosf_live_tuner.py (for bucket schema only)
   - MANUAL.md, KLIPPER.md, README.md, CONTEXT.md
6. Append "## Phase 2.13 — Acceptance Gate Parity" to TASK.md
   with Findings / Plan / Completed Steps placeholder before
   touching any code.

Implementation rules (from AGENTS.md and the plan):
- One milestone = one commit + push. Do not batch.
- Per-milestone validation gates (run all from repo root):
    python3 -m py_compile scripts/nosf_analyze.py
    python3 -m py_compile scripts/test_nosf_analyze.py
    python3 scripts/test_nosf_analyze.py
    python3 scripts/test_nosf_live_tuner.py
    python3 scripts/test_phase_2_10_parity.py
    python3 scripts/test_klipper_motion_tracker.py
    python3 scripts/test_gcode_marker.py
    python3 -m py_compile scripts/*.py
  All must exit 0. The 2.13.1 repro test is allowed to print
  "EXPECTED FAIL: phase 2.13 acceptance-gate parity" in 2.13.1
  only. It must pass as a hard assertion from 2.13.2 onward.
- Commit message format from AGENTS.md "Commit Format". Subject
  lowercase imperative <= 72 chars. Body explains why, not just
  what. Footer:
    Generated-By: GPT-5.5 (High)
- Use shell git for commit/push. Git MCP optional for status/diff.
- Update TASK.md after each milestone with the commit short SHA.
- Never commit local AI config (.agents/, .claude/, .github/,
  skills-lock.json) or unrelated dirty files.
- No firmware changes. No tuner schema bump. SCHEMA_VERSION
  stays at 4.
- on_m118 ingress contract is FROZEN. klipper_motion_tracker.py
  is FROZEN. gcode_marker.py is FROZEN. nosf_live_tuner.py
  learning loop is FROZEN.
- Existing operator DB and CSVs must remain usable. No
  reprocessing of bucket data; no destructive state edits.

Landing order (per plan section 6):
1. 2.13.1  tests/fixtures/phase_2_13_three_run_state.json
           + tests/fixtures/phase_2_13_run_a.csv (b.csv, c.csv)
           + failing-on-main repro test in test_nosf_analyze.py
2. 2.13.2  refactor: shared recommend_for_subset; gate uses
           per-run recommendations not per-bucket per-run medians;
           flip repro test to hard assertion
3. 2.13.3  classify_run for comparable/immature; skip immature;
           per-run estimate diagnostics in patch; tests cover
           skipped runs, single-comparable-run path, true
           disagreement still fails
4. 2.13.4  contributor-mass coverage gate; raw 80% downgraded to
           WARN; σ_p95 path uses BP-derived sigma; telemetry
           hard-zero documented as pending
5. 2.13.5  docs (MANUAL/KLIPPER/README/CONTEXT) + Pi validation
           on operator's existing CSVs and DB

Hard constraints:
- Pure stdlib + pyserial only. No numpy, scipy, pandas.
- Phase 2.12 recommendation path semantics must be preserved
  byte-for-byte for any existing test: same baseline,
  same bias, same confidence labels on the same input.
- The new recommend_for_subset MUST accept (runs_subset,
  rows_subset, state_buckets, current, mode, force,
  include_stale) and return the same dict shape as today's
  compute_recommendations(): {key: (value, confidence, detail)}.
- compute_recommendations becomes a thin wrapper around
  recommend_for_subset for backward compatibility with existing
  test imports.
- consistency_by_run signature changes to accept the state-aware
  parameters needed for parity. If anything outside
  acceptance_gate imports it (grep first), update accordingly
  or rename and remove the old name in a single commit.
- Per-run classification thresholds:
    MIN_COMPARABLE_BUCKETS = 3
    MIN_RUN_BUCKET_ROWS = 50
  Document at module top with one-line comments.
- Coverage gate thresholds:
    CONTRIBUTOR_MASS_PASS = 0.50
    CONTRIBUTOR_MASS_WARN = 0.65
    RAW_COVERAGE_WARN = 0.80
  Both contributor_mass and raw row coverage are reported in the
  patch. Only contributor_mass FAILs; raw_coverage WARNs.
- σ_p95 in acceptance_gate must derive from
  buffer_position_sigma_mm() of qualifying buckets' rows. Do
  not read CSV "sigma_mm" column inside acceptance_gate.
- Telemetry counters: leave as hard-coded zeros in the dict but
  add a comment "# TODO Phase 2.14+: parse real ADV / EST events;
  zero here is placeholder, not a clean signal" and add a line
  in the patch comment block:
    # Telemetry: not currently parsed from logs;
    # counters reflect pending feature, not real events
- Patch diagnostics block from plan section 5.7 must be in the
  patch on PASS, FAIL, or SKIPPED-consistency cases. Operator
  needs the same diagnostic regardless of outcome.
- --acceptance-debug flag (plan section 5.8) is optional. If
  implementing, default off, prints the per-run block to stderr.
  Patch diagnostics are mandatory; --acceptance-debug is
  convenience.

Specific notes per milestone:

- 2.13.1: build the synthetic state JSON and 3 CSVs with care.
  The standalone recommendations from compute_recommendations on
  the synthetic state alone must produce baseline near 700-730
  and bias near 0.303-0.306 for all three CSVs. The raw
  per-bucket per-run est medians (the current gate's input) must
  swing by ≥ 600 sps across runs in at least one bucket. Verify
  pre-2.13.2 main with the fixture before committing:
    python3 scripts/nosf_analyze.py \
        --in tests/fixtures/phase_2_13_run_{a,b,c}.csv \
        --state tests/fixtures/phase_2_13_three_run_state.json \
        --machine-id myprinter --out /tmp/probe.ini \
        --acceptance-gate --mode safe
  Should report consistency delta >= 600 sps on pre-2.13.2 main.
  If not, expand the fixture until it does.

- 2.13.2: refactor without semantic change to the recommendation
  path. After the refactor, run the existing Phase 2.12 tests
  unchanged; they must all pass. Then implement the per-run
  comparable-run reduction in acceptance_gate. Flip the 2.13.1
  test from "EXPECTED FAIL" print to a hard assertion.

- 2.13.3: classify_run takes a run dict, state_buckets, current,
  mode, force, include_stale; returns dict {"comparable": bool,
  "reason": str, "baseline": float, "bias": float,
  "contributors": int, "confidence": str}. acceptance_gate
  collects these into a per-run-estimates list that gets written
  to the patch. The consistency reduction operates ONLY on
  comparable runs. Skipped-run reasons are written verbatim into
  the patch diagnostics block.

- 2.13.4: contributor_mass = sum(n for label in qualifying) /
  sum(n for all non-_meta labels). Compute over the operator's
  state file. Raw_row_coverage continues to be computed as
  before but only WARNs. If contributor_mass < 0.50, FAIL. If
  0.50 <= contributor_mass < 0.65, WARN tier (write to patch,
  not fail). σ_p95 path: collect BP samples from CSV rows whose
  bucket label is in qualifying; group by bucket; compute
  per-bucket stdev via buffer_position_sigma_mm; take p95 across
  buckets; clamp to [0.1, 5.0]; compare to current ref.

- 2.13.5: rerun the operator's analyzer command listed in plan
  section 7. Capture the resulting /tmp/nosf-gate.ini and paste
  the per-run-estimates block plus the gate summary into TASK.md.
  Expected: gate passes consistency (delta ≈ 33 sps), reports
  contributor mass, and possibly emits coverage WARN. If real
  failures persist, document them in TASK.md as Phase 2.13 Pi
  Validation findings; do NOT loosen any constant to make the
  operator's data pass. Threshold tuning belongs in a follow-up
  on top of the operator's feedback.

Regression awareness:
- Phase 2.12 tests must stay green: LOCKED floor, mode
  semantics, precision-weighted baseline, BP-derived sigma,
  contributors block emission, --force behavior.
- Phase 2.11 tests must stay green: chatter repro, three-channel
  unlock, dwell guard, schema 3->4 migration.
- Phase 2.10 tests must stay green.
- Phase 2.9 tests must stay green.
- on_m118 dispatch unchanged.
- klipper_motion_tracker.py unchanged.
- gcode_marker.py unchanged.
- nosf_live_tuner.py learning loop unchanged.
- State file schema 4 unchanged.

Risk mitigation:
- If MIN_COMPARABLE_BUCKETS = 3 disqualifies all of the
  operator's runs in 2.13.5 Pi validation, lower to 2 (still
  >= Phase 2.12 MEDIUM threshold). Do not lower below 2.
- If contributor mass < 50% on the operator's real DB, document
  in TASK.md as a Phase 2.12 LOCKED-bucket coverage issue, not a
  Phase 2.13 bug. The right fix is more calibration prints, not
  a loosened constant.
- If a Phase 2.12 test breaks after 2.13.2 refactor, the refactor
  is incorrect — stop and re-derive the wrapper instead of
  loosening the test.
- Telemetry counters are intentionally not parsed in Phase 2.13.
  Resist the urge to wire them; that is a larger architectural
  change requiring Klipper log access patterns Phase 2.13 has
  not specified.

When the plan is ambiguous, prefer the more conservative
interpretation, note the choice in the commit body, and
continue. If a real conflict surfaces (e.g., a Phase 2.12 test
imports something that the 2.13.2 refactor would have to
restructure invasively), stop and ask before guessing.

Deliverable: 5 milestones (2.13.1 through 2.13.5) each landed as
one commit + push with the GPT-5.5 (High) footer. TASK.md
updated with per-milestone Completed Steps entries with short
SHAs and a Pi Validation block after 2.13.5.

Begin with the AGENTS.md session-start banner, then preflight
reads, then 2.13.1.
```

---

*End of Phase 2.13 plan.*
