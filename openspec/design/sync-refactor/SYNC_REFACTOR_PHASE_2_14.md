# Sync Refactor — Phase 2.14

> **Phase 2.14 — Gate Question Semantics**
>
> Phase 2.13 refactored the acceptance-gate consistency check so it
> uses the same recommendation path as patch emission. Consistency
> now works. But Pi validation against four real calibration prints
> shows the gate still rejects every invocation, because three
> remaining checks conflate two different questions:
>
> - "Are the **recommendations themselves** unreliable?"
> - "Is the operator's **current config** stale relative to the
>   recommendations we just computed?"
>
> The first should FAIL the gate. The second should WARN: the
> recommendation is good, the operator should flash it.
>
> Three concrete bugs ship that mix these:
>
> 1. `contributor_mass()` denominator sums `n` across every bucket
>    including a long tail of sparse one-shot buckets, so locked
>    contributors get diluted. Operator reports `44.1%` constant
>    across 2/3/4-CSV invocations because the metric is a property
>    of the state file, not the CSV input, and the state has 150+
>    buckets diluting 8 LOCKED ones.
> 2. `sigma p95 >= current ref` FAILs against `config.ini`'s
>    Phase 2.7 default of 1.00 mm. The same recommendation path
>    emits a new ref ≈ 2.0 mm to fix it. Operator cannot commit
>    the fix because the gate refuses the patch that contains the
>    fix.
> 3. `run count < 3` is a hard FAIL even when both runs produce
>    consistent recommendations. The Phase 2.13 comparable-run
>    classifier already answers this question; the raw-count check
>    is redundant and wrong.
>
> Phase 2.14 audits each gate condition against the FAIL/WARN
> principle, fixes the three bugs, and documents the rule so future
> gates do not regress. Firmware unchanged. Schema unchanged. CSVs
> and operator's tune DB remain usable as-is.
>
> This document is a **plan, not an implementation**. No code lands
> as part of this commit. Implementation begins at milestone 2.14.1.

---

## 1. Problem Summary

Operator's Pi validation (post-Phase-2.13):

```
$ python3 scripts/nosf_analyze.py \
    --in ~/nosf-runs/phase212-run{1,2,3,4}.csv \
    --state ~/nosf-state/buckets-myprinter.json \
    --machine-id myprinter \
    --out ~/nosf-runs/phase212-final.patch.ini \
    --mode safe --acceptance-gate
acceptance gate failed: contributor mass 44.1% < 50.0%
acceptance gate failed: sigma p95 2.06 >= current ref 1.00
```

Four mature calibration prints. Recommendations are stable (Phase
2.13.5 evidence: 718/694/727 ± 33 sps spread across runs). Yet the
gate keeps refusing.

The recommendations are not the problem. The **current config** is
the problem: its Phase 2.7 default `buf_variance_blend_ref_mm =
1.00` is too low for this hardware. The analyzer has computed the
correction (≈ 2.0 mm). The gate refuses the correction because
the current config is stale. This is logically backwards: the
analyzer should help the operator update the config, not block them
from doing so.

`contributor_mass = 44.1%` is a separate but adjacent issue: the
metric correctly captures that LOCKED buckets are a minority of the
operator's bucket set, but the denominator includes buckets that
could never qualify (sparse one-shot n=2-15 buckets). The metric
penalizes the operator for tracking exploratory data the tuner
collected for free. Locked bucket *mass* needs a useful-denominator
fix.

---

## 2. Current Evidence

### 2.1 Mass invariance across CSV input

```
3-CSV invocation:  contributor mass 44.1%
2-CSV invocation:  contributor mass 44.1%
4-CSV invocation:  contributor mass 44.1%
```

`contributor_mass` is computed from `state_buckets` only; CSVs do
not enter the calculation. Same number across all invocations
confirms the metric is a state-level property. The denominator is
the entire state's bucket sample mass, including very-low-n
buckets that have no path to ever locking.

### 2.2 Sigma drift across CSV input

```
2-CSV (runs 3,4):  sigma p95 2.24
3-CSV (runs 2,3,4): sigma p95 2.00
4-CSV (runs 1..4): sigma p95 2.06
```

Sigma comes from CSV BP rows aggregated per qualifying bucket.
Different CSV sets produce slightly different per-bucket BP
stdevs, hence slight σ_p95 variation. All values are in the same
2.0-2.2 mm band, well above the 1.00 mm `current` ref.

The recommendation path's own `var_ref` output for this same data
clamps to ≈ 2.0 mm and emits it as the suggested new value for
`buf_variance_blend_ref_mm`. Gate and recommendation path agree on
the σ measurement; they disagree on whether to interpret the
measurement as a FAIL or as a recommendation to commit.

### 2.3 Run-count guard fires on 2-CSV invocation

```
2-CSV (runs 3,4):  run count 2 < 3
```

This FAIL fires even though both runs are mature and produce
consistent recommendations. Phase 2.13.3 introduced
`MIN_COMPARABLE_RUNS_FOR_CONSISTENCY = 2` and the consistency
reduction handles this case correctly. The raw `len(runs) < 3`
guard at line 664 is left over from pre-Phase-2.13 logic and never
got revisited.

---

## 3. Source Walkthrough Findings

### 3.1 `scripts/nosf_analyze.py:317-327` — `contributor_mass`

```python
def contributor_mass(state_buckets, labels):
    total_n = 0
    contributor_n = 0
    for label, raw in state_buckets.items():
        if label.startswith("_") or not isinstance(raw, dict):
            continue
        n = int(raw.get("n", 0))
        total_n += n
        if label in labels:
            contributor_n += n
    return (contributor_n / total_n) if total_n > 0 else 0.0
```

Denominator is `sum(n)` across every non-meta bucket. No floor on
`n`. Buckets with `n=2` from a single visit contribute equally to
the denominator as buckets with `n=2000`.

For the operator's state file:
- ~8 LOCKED buckets with cumulative n ≈ 8000
- ~150 STABLE/TRACKING buckets with cumulative n ≈ 10000, median n ≈ 14
- mass = 8000 / 18000 = 44%

The right denominator is "buckets that could in principle qualify"
i.e., have enough samples to be candidates for locking. The Phase
2.12 force-qualifying floor uses `n >= 50` as one of its
conditions. The same floor applies here: a bucket with `n < 50`
cannot have been a meaningful contributor under any
recommendation rule, so it should not count against the operator
in the mass denominator.

### 3.2 `scripts/nosf_analyze.py:660-662` — σ_p95 gate

```python
sigma_p95 = percentile(sigma_vals, 95)
if sigma_p95 >= current["buf_variance_blend_ref_mm"]:
    reasons.append(
        f"sigma p95 {sigma_p95:.2f} >= current ref "
        f"{current['buf_variance_blend_ref_mm']:.2f}"
    )
```

Compares the observed buffer-position σ_p95 against `current`
(i.e., the value currently in `config.ini`). For a printer whose
hardware genuinely runs at σ_p95 ≈ 2 mm, the `current` ref of 1
mm is just an out-of-date default. The analyzer's own
recommendation block will tell the operator to set
`buf_variance_blend_ref_mm` to 2.0 — which the gate then refuses
to emit.

The gate is conflating two distinct conditions:
- "σ is so large the hardware is broken" — legitimate FAIL.
- "σ is larger than the current config expects" — operator should
  update config. Not a recommendation failure.

Absolute physical hardware ceiling is the right FAIL threshold
(e.g., 5 mm exceeds the 27 mm PTFE travel's reasonable variance
band, suggests a sensor issue). The current-ref comparison is the
right WARN tier.

### 3.3 `scripts/nosf_analyze.py:664-670` — run count, duration, locked count

```python
if len(runs) < 3:
    reasons.append(f"run count {len(runs)} < 3")
durations = [run_duration_s(run) for run in runs]
if not (durations and all(d >= 600.0 for d in durations)
        or sum(durations) >= 1800.0):
    reasons.append(
        f"duration total {sum(durations) / 60.0:.1f} min < 30 min "
        f"and at least one run < 10 min")
if len(locked) < 3:
    reasons.append(f"locked bucket count {len(locked)} < 3")
```

- `len(runs) < 3`: raw CSV count. Should defer to the
  comparable-run classifier from Phase 2.13.3. If
  `consistency["comparable_runs"] >= 2`, the gate has the evidence
  it needs.
- `duration < 30 min`: convoluted compound boolean. The condition
  becomes a fail if total duration is under 30 min AND no single
  run is over 10 min. For three 12-minute runs, the AND fails on
  the right side, so it passes — but the failure message is
  printed wrong. The check is also overspecified for what it's
  trying to ensure: that the calibration captured enough thermal
  and feature variety. Recommend downgrade to WARN.
- `locked bucket count < 3`: redundant with contributor_mass.
  Three LOCKED buckets with 100 n each give mass 1.5% on the
  operator's state, which already FAILs. Three LOCKED buckets
  with 5000 n each give mass 80%+, which passes. The locked-count
  rule is a clumsy proxy for mass. Demote to WARN.

### 3.4 `scripts/nosf_analyze.py:672-683` — telemetry placeholder

```python
telemetry = {
    # TODO Phase 2.14+: parse real ADV / EST events; zero here
    # is placeholder, not a clean signal.
    "ADV_DWELL_STOP": 0,
    "ADV_RISK_HIGH": 0,
    "EST_FALLBACK": 0,
}
if telemetry["ADV_DWELL_STOP"] != 0: ...
```

Comment explicitly says Phase 2.14+ should fix. The conditions
never fire because the counters are hard-coded zero. Phase 2.14
does NOT wire real telemetry parsing (that requires log access
and is a separate scope). Phase 2.14 instead removes the dead
conditions and keeps the telemetry dict as a known-zero structure
in the result for compatibility, with a clarified comment.

### 3.5 `scripts/nosf_analyze.py:635-648` — consistency reduction (Phase 2.13)

This block works correctly per Phase 2.13. Operator's evidence
shows the consistency check is not in the FAIL reasons. Phase 2.14
does not touch this code.

---

## 4. Root-Cause Hypotheses

| ID | Hypothesis | Confidence | Evidence |
|---|---|---|---|
| H-1 | Mass denominator includes long-tail sparse buckets, diluting LOCKED contribution. | HIGH | Mass invariant across CSV input (state-level metric) and operator has ~150 buckets median n=14. |
| H-2 | σ_p95 check is currently a recommendation-vs-current comparison disguised as a quality FAIL. | HIGH | Recommendation path itself produces ref ≈ 2.0 mm for the same data; refusing to emit it is circular. |
| H-3 | `len(runs) < 3` was not updated when Phase 2.13 introduced the comparable-run classifier. | HIGH | Direct code inspection. |
| H-4 | Duration and locked-count thresholds are heuristic carryovers from Phase 2.9 that no longer match the Phase 2.12/2.13 contributor model. | MEDIUM | Both rules are clumsy and overlap with contributor_mass; operator evidence does not implicate them but they are part of the gate-question audit. |
| H-5 | The FAIL/WARN rule was never written down. Phase 2.13 introduced WARN tiers but the existing FAIL set was not audited against the new tier. | HIGH | Phase 2.13 documentation does not contain a FAIL/WARN principle; each new check has chosen FAIL or WARN ad hoc. |

H-1, H-2, H-3, H-5 explain the operator's evidence. H-4 surfaces
during the same audit and should be fixed in the same pass.

---

## 5. Proposed Algorithm

### 5.1 The FAIL/WARN rule (codify in code comment and MANUAL.md)

```
Each acceptance-gate condition answers one of two questions:

  (a) Are the analyzer's RECOMMENDATIONS unreliable?
      Examples: per-run consistency disagrees; contributors are
      too sparse to support the centroid; per-bucket scatter
      is so large the precision-weighted mean is meaningless.
      Outcome on failure: FAIL the gate. The recommendations
      should NOT be committed.

  (b) Is the operator's CURRENT config stale relative to the
      recommendations we just computed?
      Examples: current ref is below observed σ; current
      baseline is far from contributor centroid; current bias
      is on the wrong side of zero from contributors.
      Outcome on failure: WARN. The recommendations are good,
      but the operator should review and flash them.

If a condition fits neither shape, it does not belong in the gate.
```

This rule lives at the top of `acceptance_gate()` as a docstring
and in the analyzer section of `MANUAL.md`. Future gate additions
must declare which question they answer.

### 5.2 `contributor_mass` denominator floor

```python
DENOMINATOR_MIN_BUCKET_N = 50

def contributor_mass(state_buckets, labels):
    total_n = 0
    contributor_n = 0
    for label, raw in state_buckets.items():
        if label.startswith("_") or not isinstance(raw, dict):
            continue
        n = int(raw.get("n", 0))
        if n < DENOMINATOR_MIN_BUCKET_N and label not in labels:
            continue
        total_n += n
        if label in labels:
            contributor_n += n
    return (contributor_n / total_n) if total_n > 0 else 0.0
```

Effect:
- LOCKED buckets always count toward both numerator and
  denominator regardless of n.
- Non-LOCKED buckets count toward the denominator only if
  `n >= 50`. Sparse one-shot buckets are excluded.

For the operator's state:
- ~8 LOCKED with cumulative n ≈ 8000
- ~30 STABLE buckets with `n >= 50` totaling ~6000
- Denominator: 8000 + 6000 = 14000
- Mass: 8000 / 14000 = 57% → PASS (above 50%)

This treats sparse exploratory buckets as "not counting against
the operator," consistent with the Phase 2.12 qualifying-bucket
rule that excludes them from contributor_entries already.

`DENOMINATOR_MIN_BUCKET_N = 50` matches the existing
`MIN_RUN_BUCKET_ROWS` for run-level estimates; one constant value
covers "what counts as a real bucket" across the analyzer.

### 5.3 σ_p95 gate becomes WARN; absolute hardware ceiling stays FAIL

```python
SIGMA_HARDWARE_CEILING_MM = 5.0    # Phase 2.12 clamp; above this, hardware is suspect

# ...

if sigma_p95 >= SIGMA_HARDWARE_CEILING_MM:
    reasons.append(
        f"sigma p95 {sigma_p95:.2f} >= hardware ceiling "
        f"{SIGMA_HARDWARE_CEILING_MM:.2f} mm; sensor or buffer issue"
    )
elif sigma_p95 >= current["buf_variance_blend_ref_mm"]:
    warnings.append(
        f"sigma p95 {sigma_p95:.2f} >= current ref "
        f"{current['buf_variance_blend_ref_mm']:.2f}; flash recommended "
        f"buf_variance_blend_ref_mm to align"
    )
```

The current-ref comparison becomes a WARN — exactly the (b)
question from §5.1: current config is stale.

The hardware-ceiling FAIL guards against pathological hardware:
if the printer's buffer position genuinely fluctuates by more
than 5 mm σ, the variance-blend approach itself is moot. This
threshold matches the Phase 2.12 σ_BP clamp range `[0.1, 5.0]`.

### 5.4 Run-count check defers to comparable runs

```python
if consistency["comparable_runs"] < 2:
    reasons.append(
        f"comparable runs {consistency['comparable_runs']} < 2; "
        f"consistency cannot be verified"
    )
elif len(runs) < 3:
    warnings.append(
        f"run count {len(runs)} < 3; recommend 3+ runs for "
        f"thermal and feature variety"
    )
```

The raw 3-run heuristic survives as a WARN: more runs are still
better for thermal and feature-time variety. But it does not FAIL
when two mature runs both produce identical recommendations.

If `comparable_runs < 2`, the gate cannot verify consistency at
all; that is a legitimate (a)-question FAIL.

### 5.5 Duration check rewritten as WARN

```python
DURATION_WARN_MIN_S = 1800.0    # 30 min total recommended

total_duration_s = sum(run_duration_s(run) for run in runs)
if total_duration_s < DURATION_WARN_MIN_S:
    warnings.append(
        f"duration total {total_duration_s / 60.0:.1f} min "
        f"< {DURATION_WARN_MIN_S / 60.0:.0f} min; recommend longer "
        f"calibration soak for thermal stability"
    )
```

Drop the confusing per-run-vs-total compound boolean. WARN tier
only. Operator with sufficient evidence (high mass, consistent
recommendations) can ignore the warning.

### 5.6 Locked-count check demoted to WARN

```python
if len(locked) < 3:
    warnings.append(
        f"locked bucket count {len(locked)} < 3; "
        f"mass {mass * 100:.1f}% is primary signal"
    )
```

A FAIL on locked count is already covered by contributor_mass
when it matters. WARN keeps the operator informed without
double-rejecting on a partial signal.

### 5.7 Telemetry placeholder cleanup

```python
# Phase 2.14: telemetry parsing is not yet implemented.
# Counters are reported as zero so downstream tooling has a
# stable result shape. The FAIL conditions are removed; once
# real log parsing lands in a follow-up phase, restore the
# conditional reasons.
telemetry = {
    "ADV_DWELL_STOP": 0,
    "ADV_RISK_HIGH": 0,
    "EST_FALLBACK": 0,
}
```

Remove the three `if telemetry[...] != 0: reasons.append(...)`
lines. Keep the dict in the gate result for compatibility with
the patch writer and tests. Document the omission in MANUAL.md.

### 5.8 Patch output: WARN block alongside FAIL block

The patch already emits FAIL reasons under
`# Failure reasons:`. Phase 2.14 adds:

```
# Warnings (recommendations still valid):
# - contributor mass 57.1% < 65.0%
# - sigma p95 2.06 >= current ref 1.00; flash recommended buf_variance_blend_ref_mm to align
# - run count 2 < 3; recommend 3+ runs for thermal and feature variety
```

The block prints whenever `gate.warnings` is non-empty,
regardless of pass/fail. Operator sees both pieces of information
in one place.

### 5.9 Constants and defaults

```python
DENOMINATOR_MIN_BUCKET_N = 50           # Matches MIN_RUN_BUCKET_ROWS.
SIGMA_HARDWARE_CEILING_MM = 5.0         # Above this, sensor/buffer issue.
DURATION_WARN_MIN_S = 1800.0            # 30 min total recommended.
```

Each at module top with a one-line `# why` comment. Operator can
tune via constant edit if Pi soak shows them too tight or too
lax.

---

## 6. Milestones

> One milestone = one commit + push. Per-milestone validation
> gates below. `Generated-By: Gemini 3.1 Pro (High)` footer.

### 6.1 — 2.14.1 — Reproduce field gate failures with fixtures

**Goal:** synthetic state + CSVs that reproduce mass 44.1%, σ_p95
2.0 mm, and 2-run scenario. Tests fail on current main, will pass
after 2.14.2 + 2.14.3 + 2.14.4 land.

Files:
- `tests/fixtures/phase_2_14_diluted_state.json` (new) — synthetic
  state mirroring operator's bucket distribution: 8 LOCKED with
  realistic n, plus ~30 STABLE n>=50, plus ~100 sparse n=2-15
  buckets. Mass against current denominator: ~44%. Mass against
  proposed n>=50 denominator: ~57%.
- `tests/fixtures/phase_2_14_high_sigma_run_a.csv`,
  `phase_2_14_high_sigma_run_b.csv` (new) — two CSVs where BP
  rows produce per-bucket σ ≈ 1.8-2.2 mm across qualifying
  buckets. Each fixture under 400 rows.
- `scripts/test_nosf_analyze.py` — add three new tests, each marked
  `# EXPECTED to expose bug until 2.14.{2,3,4}` and printing
  `EXPECTED FAIL: ...` without `sys.exit(1)`:
    - `test_phase_2_14_diluted_mass_should_pass_after_floor`
    - `test_phase_2_14_high_sigma_should_warn_not_fail`
    - `test_phase_2_14_two_mature_runs_should_pass`

Validation gate:
```
python3 -m py_compile scripts/nosf_analyze.py
python3 -m py_compile scripts/test_nosf_analyze.py
python3 scripts/test_nosf_analyze.py
```

Commit subject: `test(analyze): add phase 2.14 gate-question fixtures`

### 6.2 — 2.14.2 — Contributor mass denominator floor

**Goal:** mass denominator excludes non-LOCKED buckets with
`n < 50`. LOCKED always count.

Files:
- `scripts/nosf_analyze.py` — `DENOMINATOR_MIN_BUCKET_N = 50`;
  `contributor_mass()` per §5.2. Update `contributor_mass`
  docstring with the FAIL/WARN rule example.
- `scripts/test_nosf_analyze.py` —
  `test_mass_denominator_excludes_sparse_buckets`,
  `test_mass_includes_locked_regardless_of_n`,
  `test_mass_unchanged_when_no_sparse_buckets`. Flip
  `test_phase_2_14_diluted_mass_should_pass_after_floor` to hard
  assertion.

Validation gate: same as 2.14.1.

Commit subject: `feat(analyze): floor mass denominator at n=50`

### 6.3 — 2.14.3 — σ_p95 WARN tier, hardware ceiling FAIL

**Goal:** current-ref σ comparison becomes WARN; only the
hardware ceiling (5 mm) FAILs.

Files:
- `scripts/nosf_analyze.py` — `SIGMA_HARDWARE_CEILING_MM = 5.0`;
  σ block per §5.3. Add the FAIL/WARN rule docstring to
  `acceptance_gate()`. Add `gate["warnings"]` writing to patch
  per §5.8 if not already present (Phase 2.13 may have started
  this).
- `scripts/test_nosf_analyze.py` —
  `test_sigma_above_current_ref_warns_not_fails`,
  `test_sigma_above_hardware_ceiling_fails`. Flip
  `test_phase_2_14_high_sigma_should_warn_not_fail` to hard
  assertion.

Validation gate: same as 2.14.2.

Commit subject: `feat(analyze): sigma gate warns on stale ref`

### 6.4 — 2.14.4 — Run-count, duration, locked-count audit

**Goal:** raw-run-count FAIL deferred to comparable-runs;
duration and locked-count demoted to WARN; telemetry placeholder
cleanup.

Files:
- `scripts/nosf_analyze.py` — §5.4, §5.5, §5.6, §5.7. New
  constant `DURATION_WARN_MIN_S = 1800.0`. Remove the three
  telemetry-zero `if` conditions but keep the dict.
- `scripts/test_nosf_analyze.py` —
  `test_two_runs_pass_if_comparable_2`,
  `test_zero_comparable_runs_fails`,
  `test_duration_short_warns_not_fails`,
  `test_locked_count_low_warns_not_fails`,
  `test_telemetry_placeholder_does_not_fail`. Flip
  `test_phase_2_14_two_mature_runs_should_pass` to hard
  assertion.

Validation gate: same as 2.14.3.

Commit subject: `feat(analyze): defer run count to comparable runs`

### 6.5 — 2.14.5 — Docs + Pi rerun on operator's existing data

**Goal:** docs; rerun the operator's command from §1; record
results in TASK.md.

Files:
- `MANUAL.md` — analyzer acceptance-gate section: codify the
  FAIL/WARN rule from §5.1, document the new warnings, document
  the σ hardware ceiling, document `DENOMINATOR_MIN_BUCKET_N`.
- `KLIPPER.md` — "Reading acceptance-gate output" subsection
  (3-5 sentences) covering the difference between FAIL and WARN
  and recommending the operator flash and rerun rather than
  fighting the gate.
- `README.md` — one-line update under analyzer features
  highlighting the FAIL/WARN distinction.
- `CONTEXT.md` — Phase 2.14 history entry.
- `TASK.md` — `### Phase 2.14 Pi Validation` block. Run the
  command from §1 against the operator's actual state file and
  CSVs. Expected outcome:
    - gate PASSES (mass ~57%, consistency 33 sps within 50, σ
      hardware ceiling not exceeded)
    - WARN block lists the σ ≥ current ref and possibly run-count
      tier
    - operator can then flash and rerun for a clean PASS

Validation gate: full test suite + the rerun.

Commit subject: `docs(analyze): document phase 2.14 fail warn rule`

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

All must exit 0. The Phase 2.14.1 repro tests are allowed to print
`EXPECTED FAIL` in 2.14.1 only; each turns into a hard assertion
in its corresponding implementation milestone.

After 2.14.5, the operator's Pi rerun uses the exact command from
§1:

```
python3 scripts/nosf_analyze.py \
    --in ~/nosf-runs/phase212-run1.csv ~/nosf-runs/phase212-run2.csv \
        ~/nosf-runs/phase212-run3.csv ~/nosf-runs/phase212-run4.csv \
    --state ~/nosf-state/buckets-myprinter.json \
    --machine-id myprinter \
    --out ~/nosf-runs/phase214-final.patch.ini \
    --mode safe --acceptance-gate
```

Expected:
- Exit code 0.
- Patch contains the same recommendation values Phase 2.13 already
  produced (no recommendation-path change in Phase 2.14).
- Patch contains a `# Warnings (recommendations still valid):`
  block listing σ-vs-current and possibly run-count if 2 CSVs are
  used.
- The operator flashes the recommended config and reruns; second
  rerun shows no warnings.

---

## 8. Regression Risks

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R-1 | Floored mass denominator overshoots: too many small-but-real buckets are excluded, mass overstates contributor coverage, gate misses a true low-coverage signal. | Medium | The floor is conservative (n=50 matches existing Phase 2.12 thresholds). Synthetic Pi data should reach ~57% on operator's state; if Pi soak shows mass > 90% with thin LOCKED coverage, lower the floor to 30. |
| R-2 | σ hardware ceiling 5.0 mm is too lax for some printers. | Low | Phase 2.12 already clamps σ_BP output to [0.1, 5.0]; the ceiling matches the clamp upper bound. If a printer genuinely runs σ > 5 mm, it has bigger problems. |
| R-3 | Demoting `len(runs) < 3` to WARN allows a operator with one truly mature run and one immature run to slip through. | Low | The comparable-runs classifier from Phase 2.13.3 still requires `comparable_runs >= 2` for the consistency check. If only one comparable run exists, gate still FAILs via the consistency-cannot-be-verified path. |
| R-4 | The FAIL/WARN principle docstring drifts out of sync with code. | Medium | Add a `# FAIL/WARN: (a) recommendation reliability / (b) current-config staleness` annotation next to each gate check. Reviewer enforces during PR. |
| R-5 | Operator already committed a config based on Phase 2.13 fail-then-bypass workflow; Phase 2.14 PASS changes when they should next re-tune. | Low | Phase 2.14 does not change the recommendation values; only the gate's accept/warn decision changes. Existing committed configs are unaffected. Document in MANUAL.md. |
| R-6 | The repro fixtures lie (synthetic data does not reproduce the operator's discrepancy). | Medium | Verify by running pre-2.14.2 main against the fixture and confirming mass = 44%; if the synthetic mass lands elsewhere, expand the fixture's sparse-bucket tail. |
| R-7 | Telemetry placeholder cleanup removes a check the operator was relying on. | Low | The conditions never fired (counters always zero). Operators cannot have relied on them. |
| R-8 | A future Phase 2.x adds a new gate that ignores the FAIL/WARN rule. | Medium | MANUAL.md documents the rule. Reviewer enforces. Same risk class as any unwritten convention. |
| R-9 | The warnings block in the patch confuses operators who skim and miss the distinction. | Low | KLIPPER.md subsection in 2.14.5 explicitly addresses operator workflow. |

---

## 9. Gemini 3.1 Pro Implementation Prompt

```
You are an embedded-systems and host-tooling engineer working on
NOSF firmware host tooling: RP2040 dual-lane MMU/RELOAD controller
for FYSETC ERB V2.0.

Repository: /Users/Volodymyr_Zhdanov/playground/nightowl-standalone-controller
(or pull from origin/main).

Task: Implement Phase 2.14 — gate question semantics. Plan
committed at SYNC_REFACTOR_PHASE_2_14.md. Begin at 2.14.1.

Source of truth: SYNC_REFACTOR_PHASE_2_14.md. Read fully. Cross-
reference SYNC_REFACTOR_PHASE_2_13.md for the recommendation-path
sharing and comparable-run classifier, and Phase 2.12 plan for
the LOCKED floor, σ/x noise gate, force semantics, and
[nosf_contributors] block — all of which must be preserved.

Mandatory pre-work, in order:
1. Read AGENTS.md fully. Post the session-start banner per the
   "Session Start Protocol".
2. Read TASK.md. Confirm Phase 2.13 marked DONE.
3. Read SYNC_REFACTOR_PHASE_2_14.md fully (all 9 sections).
4. Read SYNC_REFACTOR_PHASE_2_13.md sections 5-9 (the recommendation
   path you must not break, plus comparable-run classifier).
5. Read SYNC_REFACTOR_PHASE_2_12.md for floor / qualifying semantics.
6. Read these files in their current state — they have been
   modified through Phase 2.13 and hotfixes; do NOT assume any
   pristine form:
   - scripts/nosf_analyze.py
   - scripts/test_nosf_analyze.py
   - scripts/nosf_live_tuner.py (for bucket schema only)
   - MANUAL.md, KLIPPER.md, README.md, CONTEXT.md
7. Append "## Phase 2.14 — Gate Question Semantics" to TASK.md
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
  All must exit 0. The 2.14.1 repro tests are allowed to print
  "EXPECTED FAIL: phase 2.14 ..." in 2.14.1 only. Each turns
  into a hard assertion in its corresponding fix milestone
  (2.14.2 mass, 2.14.3 sigma, 2.14.4 run-count).
- Commit message format from AGENTS.md "Commit Format". Subject
  lowercase imperative <= 72 chars. Body explains why, not just
  what. Footer:
    Generated-By: Gemini 3.1 Pro (High)
- Use shell git for commit/push.
- Update TASK.md after each milestone with the commit short SHA.
- Never commit local AI config (.agents/, .claude/, .github/,
  skills-lock.json) or unrelated dirty files.
- No firmware changes. No tuner schema bump. SCHEMA_VERSION
  stays at 4.
- on_m118 ingress contract is FROZEN. klipper_motion_tracker.py
  is FROZEN. gcode_marker.py is FROZEN. nosf_live_tuner.py
  learning loop is FROZEN.
- The Phase 2.13 recommendation path (recommend_for_subset,
  comparable-run classifier, consistency reduction) is FROZEN.
  Phase 2.14 only changes gate ACCEPT/WARN decisions.
- Existing operator DB and CSVs must remain usable. No
  reprocessing of bucket data; no destructive state edits.

Landing order (per plan section 6):
1. 2.14.1  tests/fixtures/phase_2_14_diluted_state.json +
           phase_2_14_high_sigma_run_a.csv +
           phase_2_14_high_sigma_run_b.csv + three
           EXPECTED-FAIL tests in test_nosf_analyze.py
2. 2.14.2  contributor_mass denominator floor at n=50; flip
           mass repro test to hard assertion
3. 2.14.3  sigma_p95 WARN tier; SIGMA_HARDWARE_CEILING_MM=5.0
           FAIL; flip sigma repro test
4. 2.14.4  raw-run-count defers to comparable_runs; duration
           and locked-count demoted to WARN; telemetry
           placeholder cleanup; flip two-runs repro test
5. 2.14.5  docs (MANUAL/KLIPPER/README/CONTEXT) + Pi rerun on
           operator's existing CSVs and DB

Hard constraints:
- Pure stdlib + pyserial only.
- The FAIL/WARN rule from plan section 5.1 must be a docstring
  at the top of acceptance_gate() AND a paragraph in MANUAL.md.
  Every existing gate condition must be annotated in code with
  a one-line comment "# FAIL/WARN: (a) recommendation reliability"
  OR "# FAIL/WARN: (b) current-config staleness".
- contributor_mass() signature unchanged. Floor logic per
  plan section 5.2. DENOMINATOR_MIN_BUCKET_N = 50 at module top.
- sigma_p95 path: compute as before (per qualifying bucket BP
  stdev, p95 across buckets, clamped). FAIL only if
  sigma_p95 >= SIGMA_HARDWARE_CEILING_MM. WARN if sigma_p95 >=
  current["buf_variance_blend_ref_mm"]. Both messages explicit.
- Run-count: FAIL only if consistency["comparable_runs"] < 2.
  WARN if len(runs) < 3 AND comparable_runs >= 2.
- Duration: FAIL removed. WARN only at sum(durations) < 1800.
  Simplify the compound boolean from line 666-668 of current
  main.
- Locked-count: FAIL removed. WARN if len(locked) < 3.
- Telemetry: remove the three "if telemetry[X] != 0: ..." lines.
  Keep the dict in the gate result for compatibility. Update
  the comment to clarify "not yet parsed; will be added in a
  follow-up phase".
- Patch output: add "# Warnings (recommendations still valid):"
  block per plan section 5.8 if Phase 2.13 did not already
  emit one. If Phase 2.13 already writes a warnings block,
  extend it; do not duplicate.
- All FAIL/WARN messages must contain numbers, not just status
  words, so the operator can verify the decision.

Specific notes per milestone:

- 2.14.1: build phase_2_14_diluted_state.json carefully. Use 8
  LOCKED buckets with n=1000 each (total 8000), 30 STABLE
  buckets with n=100-300 each (total ~6000), and 100 sparse
  buckets with n=2-15 each (total ~600). With current
  denominator: mass = 8000 / 14600 = ~55% (still passes 50%).
  Adjust counts so current mass = ~44%. Most likely needs more
  sparse buckets (~200) and fewer mid-tier ones. Verify with
  pre-2.14.2 main: contributor_mass(state, locked_labels)
  must return ~0.44 before committing the fixture. After 2.14.2,
  same fixture must return ~0.57.
  The high-sigma CSV fixture: 400 BP rows per file, BP values
  drawn from N(0, sigma=2.0) clamped to [-4, +4] mm. Verify
  buffer_position_sigma_mm returns 1.8-2.2 mm per fixture.

- 2.14.2: function body change per plan section 5.2. Other
  callers of contributor_mass (if any) are not affected because
  the signature is unchanged. Run a grep for "contributor_mass("
  before committing.

- 2.14.3: add SIGMA_HARDWARE_CEILING_MM = 5.0 at module top.
  Restructure the sigma block in acceptance_gate per plan
  section 5.3 — two branches, FAIL above ceiling, WARN above
  current ref. Update the FAIL message to mention "sensor or
  buffer issue" so the operator knows it is a hardware-level
  alarm.

- 2.14.4: Rewrite the run-count, duration, locked-count, and
  telemetry blocks per plan sections 5.4 through 5.7. The
  duration compound boolean from current main line 666-668 is
  unreadable; replace with a single `if total_duration_s <
  DURATION_WARN_MIN_S: warnings.append(...)`. Keep
  run_duration_s helper.

- 2.14.5: rerun the operator's exact command from plan section
  7. Capture the patch output and paste the Warnings block
  plus the gate status line into TASK.md. Expected: PASS with
  warnings about sigma-vs-current and possibly run-count.
  Do NOT loosen constants to make the operator's data pass
  artificially. If real failures persist after Phase 2.14
  semantics are correct, that is genuine information; document
  it.

Regression awareness:
- Phase 2.13 tests must stay green: shared recommendation path,
  per-run consistency, immature-run filtering, contributor-mass
  reporting (just the metric definition shifts).
- Phase 2.12 tests must stay green: LOCKED floor, precision-
  weighted baseline, BP-derived sigma, contributors block.
- Phase 2.11 tests must stay green: chatter repro, three-channel
  unlock, dwell guard, schema 3->4 migration.
- Phase 2.10 tests must stay green.
- Phase 2.9 tests must stay green.
- on_m118 dispatch unchanged.
- klipper_motion_tracker.py unchanged.
- gcode_marker.py unchanged.
- nosf_live_tuner.py learning loop unchanged.
- State file schema 4 unchanged.
- Recommendation values in emitted patches unchanged. Only
  the gate's FAIL/WARN classification changes.

Risk mitigation:
- If DENOMINATOR_MIN_BUCKET_N = 50 inflates the operator's mass
  above 90%, the floor is too aggressive. Lower to 30 before
  shipping. Document in commit body.
- If SIGMA_HARDWARE_CEILING_MM = 5.0 trips for a legitimate
  hardware setup, the recommendation path's clamp already
  bounded the value at 5.0; investigate whether the actual sigma
  exceeds 5.0 or the clamp was bypassed. Do not lower the
  ceiling without understanding why.
- If the comparable-runs classifier from Phase 2.13 starts
  returning < 2 for the operator's 4 CSVs, that is a Phase 2.13
  regression and must be debugged before Phase 2.14 conclusions
  can be drawn.
- Resist the urge to wire real telemetry parsing in Phase 2.14;
  that is a separate architectural change (log file access,
  event correlation) that needs its own design pass.

When the plan is ambiguous, prefer the more conservative
interpretation, note the choice in the commit body, and continue.
If a real conflict surfaces (e.g., a Phase 2.13 test depends on
contributor_mass returning the pre-floor value), stop and ask
before guessing.

Deliverable: 5 milestones (2.14.1 through 2.14.5) each landed as
one commit + push with the Gemini 3.1 Pro (High) footer. TASK.md
updated with per-milestone Completed Steps entries with short
SHAs and a Pi Validation block after 2.14.5.

Begin with the AGENTS.md session-start banner, then preflight
reads, then 2.14.1.
```

---

*End of Phase 2.14 plan.*
