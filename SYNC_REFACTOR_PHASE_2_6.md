# Phase 2.6 Planning Proposal — Drift Observer & ADVANCE-Risk Telemetry

> **Status:** planning only, no code. Builds on Phases 0–2.5 (committed).
> Phase 2.5 added a confidence-gated integral over reserve error and a
> physics-based sigma model. This proposal adds the *measurement* layer:
> a transition-anchored residual observer that quantifies virtual-vs-physical
> mismatch directly, with default-OFF correction and an additive
> ADVANCE-risk channel for upstream warning.

> **Hard constraints carried forward.** D1–D7 from `SYNC_REFACTOR_PLAN.md §0`
> apply unchanged. §4.5 endstop-mode parity gate is the release-blocking
> criterion. All new behavior ships behind feature flags with conservative
> defaults (`= 0` ⇒ OFF). No PSF-specific assumptions.

## 0. Operational Context (this iteration)

- **Speed:** 1500 mm/min nominal feed.
- **Observed pattern.** Long stretches in `BUF_MID` with `BP ≈ −4.5 mm` (low
  to moderate negative `RE`), interrupted by sudden `BUF:ADVANCE` events
  with `BP` clamped near `+threshold` (large positive `RE`). `AD:` climbs
  in the ADVANCE windows.
- **Operator settings recently observed:** `buf_half_travel_mm = 7.8`,
  `sync_reserve_pct = 50`, `join_rate = 1600.1`, `trailing_rate = 90`,
  `sync_max_rate = 4000`, `sync_auto_stop_ms = 5000`, `est_alpha_min = 0.120`,
  `est_alpha_max = 0.650`, `zone_bias_max_rate = 600`. (Repo defaults differ;
  this is a tuned deployment.)
- **Phase 2.5 result on this rig:** confidence + integral did not meaningfully
  reduce ADVANCE pinning at this feed rate. The integrator either freezes
  (confidence falls below 0.7) or has insufficient authority to overcome the
  systematic offset that drives the arm to advance under load.

The reserve target at the operator's settings is approximately
`−(7.8 × 0.50) − (7.8 × 0.05) ≈ −4.29 mm`. So `BP ≈ −4.5 mm` is *very close
to target* per the model. The model is satisfied. The physical arm is not.
That is the gap.

---

## 1. Phase Naming Recommendation

**Recommend: `Phase 2.6 — Drift Observer & ADVANCE-Risk Telemetry`.**

Rationale:

- Phase 2.5 (integral + sigma confidence) introduced no new abstraction; it
  refined the control law on top of `buf_signal_t`. Phase 2.6 also refines on
  the same surface (it adds an observer that consumes existing transition
  events). Same surface ⇒ minor bump.
- Phase 3 is reserved in the plan for the analog/PSF drop-in (a new sensor
  source). Promoting this to Phase 3 would collide with that scope and
  conflate "improve endstop estimator" with "introduce new sensor". Don't
  collide.
- "Phase 3A" was considered but breaks the existing N.M convention used in
  Phase 2.5.

**Numbering rule going forward (proposed):**
- *Major bump (Phase N+1)* when introducing a new module, new abstraction,
  or new persistent file (e.g. `Phase 3` adds analog calibration commands).
- *Minor bump (Phase N.M)* when refining behavior on existing surfaces with
  default-OFF feature flags.

This keeps `SYNC_REFACTOR_PLAN.md` semantic-versioned for future readers.

---

## 2. Root-Cause Ranking

The control loop reports "near target" while the physical arm sits closer
to the advance switch. Five candidate causes, ranked by fit to the observed
log pattern.

### A. Estimator self-reference in `BUF_MID`
**Where.** `sync.c:887–891`:
```c
} else if (s == BUF_MID && (now_ms - g_buf.entered_ms) > 2000u &&
    A->task == TASK_FEED && A->fault == FAULT_NONE &&
    buf_near_target && sync_current_sps > 0) {
    extruder_est_sps += 0.05f * ((float)lane_motion_sps(A) - extruder_est_sps);
    extruder_est_last_update_ms = now_ms;
}
```
While the controller is happy, the estimator EWMA-blends toward
`lane_motion_sps(A)`, which is `sync_current_sps` — itself derived from
`extruder_est_sps + corrections`. Steady state: `extruder_est_sps` converges
to the controller's own command, **not** to the true extruder rate. If the
real extruder pulls faster than the commanded MMU rate, no transition fires
while the controller barely keeps up; the estimator never learns the new
truth.
**Falsifiable.** Log `EST` while the printer is in steady extrusion: it
should plateau at `sync_current_sps × MM_PER_STEP`, not at the
Klipper-side commanded extruder rate. If they agree → not the cause. If
`EST` lags actual extrusion, this is the dominant cause.

### B. Virtual position integration uses the same biased estimator
**Where.** `sync.c:184–188`:
```c
float mmu_mm_s = (float)lane_motion_sps(A) * MM_PER_STEP[idx];
float extruder_mm_s = extruder_est_sps * MM_PER_STEP[idx];
float net_delta = (extruder_mm_s - mmu_mm_s) * dt_s;
g_buf_pos += net_delta;
```
If `extruder_est_sps` is biased low (Cause A), `net_delta` is biased toward
`mmu > extruder` per tick, and `g_buf_pos` drifts toward the trailing side
*in the model* while the physical arm drifts toward advance. The mismatch is
**structural** — not random noise. Re-anchoring at zone transitions resets
`g_buf_pos` to ±threshold but does not correct the per-tick bias that
re-accumulates between transitions.
**Falsifiable.** Log mean `BP` in MID, run-by-run. If mean `BP` trends more
negative over a 30-min print while ADVANCE pin frequency rises, this matches.

### C. `BUF_MID` estimator starvation
**Where.** Outside the bleed at `sync.c:887–891`, the estimator updates
*only* on zone transitions (`buf_update`, `sync.c:555–577`). Healthy MID
print can run minutes without a transition. With Cause A defanging the only
in-MID update, the estimator is effectively frozen at its last
transition-derived value — typically computed from a `MID → ADVANCE`
travel, which is the noisiest moment for that calculation (transition-bound
arm velocity has 1× `BUF_HYST_MS` of debounce baked in).
**Falsifiable.** Log `EA:` (estimator age). If `EA:` regularly exceeds
30 s during normal feed at 1500 mm/min, the estimator is starved.

### D. Sigma-confidence timing is wrong shape for steady print
**Where.** `sync.c:189–191` and `sync.c:756–759`:
```c
if (g_buf.state == BUF_MID) {
    g_buf_pos_sigma_accum_mm += fabsf(net_delta);
}
…
g_buf_sigma_mm = sqrtf(g_buf_pos_sigma_accum_mm) * ENDSTOP_PER_UNIT_SIGMA_MM;
```
Sigma accumulates `|net_delta|` per tick, scaled by 0.025 mm. At 1500 mm/min
with steady tracking, per-tick `|net_delta|` ≈ 0.5–1 mm; sigma reaches the
1.5 mm cap in roughly `(1.5 / 0.025)² ≈ 3600` mm of integrated arm motion —
about 144 s at 1500 mm/min. Confidence falls smoothly toward 0 over that
window, which freezes the integral *before* the integral could earn its
keep, then trips `EV:BUF,EST_FALLBACK` even though no fault occurred.
Confidence model is too time-pessimistic for steady runs.
**Falsifiable.** Log `EC:` over 30-min print. If `EC` decays to zero
between transitions even on healthy prints → too pessimistic.

### E. Reserve geometry is not regime-aware
The target is fixed at `−threshold × pct − center_guard`. Under nominal
flow that is healthy headroom; under sustained high flow the same target
leaves the arm at the trailing limit of its margin, and any extruder spike
exceeds the controller's recovery rate before the next pin.
**Falsifiable.** A static shift of the target less negative under sustained
high flow would reduce ADVANCE pin frequency without other changes.

**Top-three picked for action.** **A**, **B**, **C** — they form a single
chain (the estimator is self-referential, that bias contaminates the virtual
position, and the only escape valve is starved). The user-proposed
correction layer (`corrected_bp = bias + scale × model_bp`) addresses the
**symptom** of A→B→C; this proposal layers a measurement step that will
reveal whether the chain is the actual cause and provides the data needed
to break it.

---

## 3. Candidate Solutions

Five candidates evaluated. All persistent tunables ship with defaults that
make the candidate behavior-identical to Phase 2.5 (default-OFF).

### Candidate 1 — Static virtual→physical correction layer (user's hypothesis)
**Mechanism.** At every controller-side read of `g_buf_pos`, replace with
`bp_eff = buf_pos_bias_mm + buf_pos_scale × g_buf_pos`. Keep `BP:` raw,
add `BPE:` corrected. The integration loop is **not** affected — only
control-law consumers (`sync_apply_scaling`, `reserve_error`,
`buf_near_target`, etc.).
**Tunables.** `buf_pos_bias_mm` (range −5..+5, default 0.0, persistent).
`buf_pos_scale` (range 0.5..2.0, default 1.0, persistent).
**Failure modes.**
- Operator misconfigures and chases wrong target.
- A scale ≠ 1 disagrees with anchoring at transitions, producing a sawtooth
  every time a switch fires.
**Mitigations.** Range clamps; surface `BPE:` separately so operator can
diff. Mechanical tuning is manual, brittle, won't survive lane swap or
mechanical wear.
**Expected impact.** Bias-only addresses **B** at steady state. No coverage
for **A** or **C**. Does not adapt to regime or wear.

### Candidate 2 — Auto-calibrating drift observer (recommended)
**Mechanism.** Ground truth exists at exactly one moment per zone change:
the switch fires. At each `MID → ADVANCE` and `MID → TRAILING` transition,
compute `pos_mm_residual = pre_snap_pos_mm − switch_pos_mm` *before* the
existing snap in `buf_anchor_virtual_position`. Maintain a per-direction
EWMA `bp_drift_ewma_mm` with time constant `buf_drift_ewma_tau_ms`
(default 60 000 ms). When (a) sample count ≥ `buf_drift_min_samples`,
(b) `|ewma| > buf_drift_apply_threshold_mm`, (c) confidence
≥ `buf_drift_apply_min_confidence`, (d) signs from MID→ADV and MID→TRL
agree (systematic, not symmetric noise) — apply
`bp_eff = g_buf_pos − clamp(bp_drift_ewma_mm, ±buf_drift_clamp_mm)` at all
controller-side reads of `g_buf_pos`. Integration loop untouched.
**Tunables.**
| Key | Range | Default | Persistent |
|---|---|---|---|
| `buf_drift_ewma_tau_ms` | 5 000 – 600 000 | 60 000 | yes |
| `buf_drift_min_samples` | 1 – 32 | 3 | yes |
| `buf_drift_apply_threshold_mm` | 0.0 – 5.0 | **0.0 (OFF)** | yes |
| `buf_drift_clamp_mm` | 0.0 – 5.0 | 2.0 | yes |
| `buf_drift_apply_min_confidence` | 0.0 – 1.0 | 0.5 | yes |
**Failure modes.**
- Single regime-change event biases EWMA. Mitigation: reset on
  `sync_disable`, settings load, sensor hot-swap (D4 idle path),
  `EV:SYNC,ADV_DWELL_STOP`, and any `EV:BUF,EST_FALLBACK` (Phase 2.5).
- Asymmetric residuals (MID→ADV residuals positive, MID→TRL residuals
  positive too — would mean mechanical asymmetry, not drift). Mitigation:
  the sign-agreement gate suppresses application; ewma is still recorded
  for telemetry.
- Apply over-correction. Mitigation: hard clamp at `buf_drift_clamp_mm`,
  identical to Phase 2.5's integral clamp pattern.
**Expected impact.** Directly measures and corrects **B**. Indirectly
calibrates against **A** because a low-biased estimator manifests as a
systematic negative residual at MID→ADVANCE. Does **not** address **C**
(estimator starvation) directly — that needs Candidate 4.

### Candidate 3 — Regime-aware reserve target (control law)
**Mechanism.** Shift `effective_target` toward 0 when sustained high flow
(`extruder_est_sps > assist_start_sps`) AND recent ADVANCE-pin density
exceeds threshold. Pre-emptive headroom under stress.
**Tunables.** `reserve_target_flow_shift_mm` (default 0.0 = OFF, range
0..3), `reserve_target_pin_density_window_ms` (default 60 000),
`reserve_target_pin_density_thr` (default 2 pins per window).
**Failure modes.** Pin events also raise target → arm may oscillate near
zero in MID; too aggressive starves trailing reserve under low flow.
**Expected impact.** Addresses **E**. Risk of regression on §4.5 parity if
the gate fails — must be feature-flagged.

### Candidate 4 — De-self-referencing the MID estimator (control law / observer)
**Mechanism.** Replace the bleed at `sync.c:887–891` so `extruder_est_sps`
is updated from a measurement that does *not* re-use `extruder_est_sps`.
Practical implementation: derive `arm_vel_estimate_mm_s` per tick from the
*per-tick* `g_buf_pos` slope (signed, low-pass filtered), and compute
`extruder_est_sps_meas = mmu_mm_s + arm_vel_estimate_mm_s` per tick. Blend
with low alpha. When `arm_vel_estimate ≈ 0` for > N seconds (no in-MID
information), freeze update.
**Tunables.** `est_mid_bleed_alpha` (default 0.0 = OFF, range 0..0.2),
`est_mid_recompute_period_ms` (default 500), `est_mid_freeze_ms` (default
5 000).
**Failure modes.** In a perfectly tracked MID, the slope is zero and the
update collapses to existing self-reference. Real-world tick-to-tick noise
can produce false signal. Largest blast radius — touches the estimator that
every other control term consumes.
**Expected impact.** Addresses **A** and **C** at root. Most invasive.
Defer to Phase 3.5 unless Phase 2.6 evidence shows the residual-observer
alone cannot converge.

### Candidate 5 — ADVANCE-risk preemption (safety gating)
**Mechanism.** Rolling 60-s pin counter. If count exceeds threshold, emit
`EV:SYNC,ADV_RISK_HIGH`; optionally raise `sync_current_sps` floor by
`adv_risk_boost_sps` for `adv_risk_boost_hold_ms`.
**Tunables.** `adv_risk_window_ms` (default 60 000), `adv_risk_threshold`
(default 4), `adv_risk_boost_sps` (default 0 = warn-only),
`adv_risk_boost_hold_ms` (default 30 000).
**Failure modes.** False-positive boost wastes feed budget; sustained
boost may interact badly with `SYNC_AUTO_STOP`. Mitigation: warn-only as
the shipped default; boost requires explicit operator opt-in.
**Expected impact.** Reduces ADVANCE-dwell duration without addressing the
root cause; bounds operational risk.

---

## 4. Chosen Plan — Hybrid (Candidate 2 primary + Candidate 5 secondary)

**Primary: Candidate 2 — drift observer, default-OFF.** Reason: it
**measures** the user's hypothesized `bias = bp_drift_ewma_mm` at the only
moment ground truth is available (switch crossings). Static Candidate 1 is
the degenerate special case (`min_samples=1`, `apply_threshold=0`,
`clamp=∞`); the auto-calibrating form is strictly more robust and gives
operators data instead of a tuning chore. Rollback is a single
`SET buf_drift_apply_threshold_mm:0`.

**Secondary, layered: Candidate 5 (warn-only) — `EV:SYNC,ADV_RISK_HIGH`.**
Reason: provides upstream warning so operators see clusters of
pin-events forming *before* `AD:` trips. Behavior boost stays default-OFF
this iteration.

**Rejected.** Candidate 1 (static): hand-tuned values brittle, no data
trail. Candidate 3 (regime target shift): risks §4.5 parity for marginal
gain; Candidate 5 covers the same risk surface with less control
authority. Candidate 4 (estimator overhaul): too invasive; queue for
Phase 3.5 if 2.6 evidence proves residual-observer is insufficient.

### 4.1 Implementation Plan (per file/module)

Each step labeled **[I]** = instrumentation only, no behavior change;
**[B]** = behavior change, gated default-OFF.

1. **[I]** `firmware/src/sync.c`
   - In `buf_update()`, before calling `buf_anchor_virtual_position`,
     compute `pos_mm_residual = g_buf_pos − switch_pos_mm` where
     `switch_pos_mm = ±buf_threshold_mm()` per direction.
   - Maintain `g_bp_drift_ewma_mm` (signed mm) and
     `g_bp_drift_sample_count` (uint16). Update only on `MID → ADVANCE`
     and `MID → TRAILING` (other transitions don't have ground truth at
     a switch boundary). Use exponential decay
     `α = 1 − exp(−dt_ms / buf_drift_ewma_tau_ms)`.
   - Maintain rolling pin counter: a circular buffer of 60 ms-buckets
     (or simpler: a count + window-start timestamp).
   - **No control change.** No new fields read by the controller.
2. **[I]** `firmware/include/sync.h`
   - Add accessors: `float sync_bp_drift_ewma_mm(void)`,
     `int sync_bp_drift_samples(void)`, `int sync_adv_pin_window_count(uint32_t now_ms)`,
     `float sync_bp_residual_last_mm(void)`.
3. **[I]** `firmware/src/protocol.c`
   - Append at tail (D7): `,BPR:%.2f,BPD:%.2f,BPN:%d,APX:%d`.
     - `BPR` = last `pos_mm_residual` (mm, signed, two decimals).
     - `BPD` = `bp_drift_ewma_mm` (mm, signed, two decimals).
     - `BPN` = sample count (int).
     - `APX` = ADVANCE-pin count in window (int).
   - Buffer remains 512; 4 fields × ~12 chars ≈ 48 bytes. Fits.
4. **[I]** `MANUAL.md` — document `BPR`, `BPD`, `BPN`, `APX` in the
   "Additional Diagnostic Status Fields" table.
5. **[I]** `BEHAVIOR.md` — add paragraph to "Velocity estimator" / "Zone
   bias" sections describing the residual observer's data path. State
   that behavior is unchanged at this step.
6. **[I]** Build green (`ninja -C build_local`), STATUS smoke test
   verifies all four new fields are present and `BPN` increments
   monotonically across simulated transitions.
7. **Operator capture** — ≥1 print session at 1500 mm/min, archive
   `STATUS` log. Review `BPR` distribution and `BPD` convergence
   characteristics. **Decision gate:** proceed to step 8 only if `BPD`
   converges to a non-trivial steady value (|BPD| > 0.3 mm consistently)
   — that is the empirical evidence that drift correction has
   measurable signal. If `BPD ≈ 0`, the observed pattern has a different
   root cause and we re-enter the candidate-evaluation step.
8. **[B, default-OFF]** `config.ini`, `config.ini.example`,
   `scripts/gen_config.py` — add the five tunables with defaults from
   §3 Candidate 2. Regenerate `tune.h`.
9. **[B, default-OFF]** `firmware/include/controller_shared.h` —
   `extern` declarations for the five new tunables.
10. **[B, default-OFF]** `firmware/src/main.c` — definitions.
11. **[B]** `firmware/src/settings_store.c` — bump
    `SETTINGS_VERSION 45u → 46u`; add five fields (one float, one int, one
    float, one float, one float); save/load/defaults wired. Per **D3**.
12. **[B, default-OFF]** `firmware/src/sync.c` —
    - Compute `bp_eff = g_buf_pos − bp_drift_correction_mm` where the
      correction is applied only when all gating predicates hold (see
      §3 Candidate 2).
    - Replace controller-side reads of `g_buf_pos` with `bp_eff` *only*
      in `sync_tick()` consumers (`reserve_error`, `buf_near_target`,
      `sync_apply_scaling`'s threshold compares). The integration step
      and re-anchoring continue to use raw `g_buf_pos`.
    - Reset state on `sync_disable`, `EV:SYNC,ADV_DWELL_STOP`,
      `EV:BUF,EST_FALLBACK`, settings load, sensor hot-swap.
13. **[B, default-OFF]** `firmware/src/protocol.c` — SET/GET keys:
    `BUF_DRIFT_TAU_MS`, `BUF_DRIFT_MIN_SAMPLES`,
    `BUF_DRIFT_APPLY_THR_MM`, `BUF_DRIFT_CLAMP_MM`,
    `BUF_DRIFT_APPLY_MIN_CF`. Append `,RDC:%d` (drift-correction activity
    scalar 0–100) to the status tail.
14. **[B, default-OFF, runtime-only]** `firmware/src/sync.c` +
    `protocol.c` — ADVANCE-risk warn-only:
    `EV:SYNC,ADV_RISK_HIGH` fires when `APX ≥ adv_risk_threshold` (rate-
    limited 1 / 30 s). Tunables `adv_risk_window_ms` (60 000),
    `adv_risk_threshold` (4) — runtime-only, **not** persisted (D3).
    The `adv_risk_boost_sps` knob is **declared but not implemented** in
    this phase (placeholder for Phase 2.6.x); ship as documentation only.
15. **§4.5 endstop-mode parity gate** — re-run with all new tunables
    at default (`apply_threshold=0`, `boost_sps=0`). Must pass before
    merge.
16. **Operator soak** with `apply_threshold=0.5`, `min_samples=4`,
    `clamp=2.0`. Compare against §6 acceptance criteria.
17. **[Docs]** Append Phase 2.6 to `SYNC_REFACTOR_PLAN.md` (preserve
    Phase 2.5 / Phase 3 sections). Update `MANUAL.md`, `BEHAVIOR.md`,
    `TASK.md`. Note `45u → 46u` settings bump in commit body.

### 4.2 Rollback Plan

| Symptom | Action |
|---|---|
| Drift correction misbehaves | `SET BUF_DRIFT_APPLY_THR_MM:0` (immediate, no rebuild). |
| ADV_RISK_HIGH spamming | `SET ADV_RISK_THRESHOLD:9999` (warn-only knob). |
| Settings won't load after upgrade | Expected; 45u→46u bump wipes cleanly with `EV:SETTINGS,VERSION_MISMATCH`. Document in commit body. |
| Full revert | `git revert <Phase 2.6 commit>` — single revert; depends only on Phase 2.5. |

---

## 5. Regression Impact Matrix

| Flow | Risk | Validation |
|---|---|---|
| Preload | None. Drift observer collects no samples until `sync_enabled`; pin counter only counts during sync. | Repeat existing preload test with apply_threshold=0; expect identical event sequence. |
| Load (`FL`, `LO`) | None. `tc_state ≠ TC_IDLE` halts observer state changes. | TC test parity with §4.5 P5. |
| Unload (`UL`, `UM`) | None. Same gating as load. | TC test parity. |
| Toolchange (MMU) | None. Observer state resets on `tc_state` transitions. | §4.5 P5. |
| Reload-follow | **D5 invariant.** No control-law change. New telemetry events permitted; do not gate behavior. | Confirm no new code path inside `TC_RELOAD_FOLLOW`. |
| Sync (`SY`) | Targeted flow. Default-OFF ⇒ behavior bit-identical to Phase 2.5. With apply on, additional bounded shift on controller-side reads of `g_buf_pos` (≤ `buf_drift_clamp_mm`). | §4.5 P3 within the same RMSE tolerances; soak test §6. |
| Persistence | Settings version bump 45u → 46u. | Round-trip SAVE/LOAD; verify clean wipe on first boot post-upgrade; document in commit. |
| Protocol/status | Additive only (D7). New fields `BPR`, `BPD`, `BPN`, `APX`, `RDC` at tail. New SET/GET keys. | §4.5 P1 ignores new tail fields. |

---

## 6. Telemetry & Acceptance Criteria

### 6.1 New Status Fields

| Field | Meaning | Units | Phase |
|---|---|---|---|
| `BPR` | Last per-transition residual (signed) | mm, two decimals | 2.6 |
| `BPD` | Drift EWMA (signed) | mm, two decimals | 2.6 |
| `BPN` | Drift sample count | int | 2.6 |
| `APX` | ADVANCE-pin count in `adv_risk_window_ms` | int | 2.6 |
| `RDC` | Drift-correction activity scalar (0 = inactive, 100 = at clamp) | int 0–100 | 2.6 |

### 6.2 New Events

| Event | Trigger | Rate-limit |
|---|---|---|
| `EV:SYNC,ADV_RISK_HIGH` | `APX ≥ adv_risk_threshold` | 1 / 30 s |
| `EV:BUF,DRIFT_RESET` | EWMA reset path fired (sync_disable, sensor swap, ADV_DWELL_STOP, EST_FALLBACK, settings load) | 1 per cause-event |

### 6.3 Pass / Fail Metrics — 30-min print at 1500 mm/min

**Default-OFF (parity gate):**
- §4.5 P1–P7 must all hold against the Phase 2.5 baseline.
- `BPR`, `BPD`, `BPN`, `APX`, `RDC` populated on every `STATUS` dump
  (instrumentation must work even when behavior is disabled).
- `RDC == 0` for the entire run.

**Apply-on (`buf_drift_apply_threshold_mm = 0.5`, `min_samples = 4`,
`clamp = 2.0`, `apply_min_confidence = 0.5`):**
- Max ADVANCE dwell (`AD:` peak) **< 1500 ms**; 95th-percentile
  `AD:` **< 500 ms**.
- ADVANCE-pin frequency: in any 60-s rolling window after a 5-min
  warmup, count **≤ 4**.
- |`BPD`| settles to a value **< 0.5 mm** by minute 5 and stays bounded
  for the remainder of the run; max excursion **< clamp**.
- `RE:` standard deviation **≤ 1.10 ×** Phase 2.5 baseline.
- Confidence (`EC:`): mean **≥ 60**, minimum (excluding the first 5 s of
  boot) **≥ 20**.
- Zero occurrences of `EV:SYNC,ADV_DWELL_STOP`,
  `EV:BUF,EST_FALLBACK`, `EV:SYNC,AUTO_STOP`.
- `EV:SYNC,ADV_RISK_HIGH` may fire **at most once** during the 30-min
  run; if it fires more, threshold tuning is required before declaring
  success.

**Confidence / fallback behavior expectations:**
- When `EC < est_fallback_cf_threshold`, drift apply freezes (consistent
  with Phase 2.5 integral freeze).
- When `EV:BUF,EST_FALLBACK` fires, drift EWMA resets to 0 and emits
  `EV:BUF,DRIFT_RESET`.

---

## 7. Rollout & Tuning Workflow

### 7.1 Safe rollout sequence

1. **Phase 2.6.0 — instrumentation only** (steps 1–6 above). No behavior
   change. Operator collects data for ≥ 2 print sessions.
2. **Phase 2.6.1 — drift apply, conservative** (steps 8–13, defaults
   shipped OFF). Operator opts in:
   `SET BUF_DRIFT_APPLY_THR_MM:0.5; SET BUF_DRIFT_MIN_SAMPLES:4`.
3. **Phase 2.6.2 — ADVANCE-risk warning** (step 14). Warn-only.
   Operator monitors for false positives; tunes
   `adv_risk_threshold` if needed.
4. **Phase 2.6.3 — (optional, if 2.6.1+2.6.2 insufficient)** lift the
   guardrail on the boost knob. Out of scope for the initial 2.6 ship.

### 7.2 Operator tuning procedure (in order)

1. Confirm Phase 2.5 defaults: `sync_reserve_integral_gain = 0.0`,
   `sync_overshoot_mid_extend = 0`. Baseline must match §4.5.
2. Build & flash Phase 2.6.0; run a 5-minute baseline print.
3. Inspect the captured `BPR` and `BPD`:
   - If mean `BPR` is consistently negative on `MID → ADVANCE`
     transitions and consistently positive on `MID → TRAILING`
     transitions ⇒ sign agreement ⇒ systematic drift, candidate for
     correction.
   - If signs disagree ⇒ symmetric noise, **do not enable apply**;
     escalate to investigation (Cause A or C).
4. If signs agree: `SET BUF_DRIFT_APPLY_THR_MM:0.5`,
   `SET BUF_DRIFT_MIN_SAMPLES:4`. Run another 5-minute print. Verify
   `RDC > 0` during steady feed and `|BPD|` stays bounded.
5. If `APX` regularly exceeds 2 in a 60-s window, set
   `SET ADV_RISK_THRESHOLD:3` and observe `EV:SYNC,ADV_RISK_HIGH`
   timing. **Do not** ship a non-zero boost in this iteration.
6. Stop conditions (any one ⇒ revert tunable, file regression report):
   - `EV:SYNC,ADV_DWELL_STOP` fires.
   - `|BPD| > clamp` for > 5 min sustained.
   - `RE` standard deviation increases > 1.50 × baseline.
   - Any new `EV:BUF,EST_FALLBACK` event.

### 7.3 Disable path

| Goal | Action |
|---|---|
| Disable drift correction | `SET BUF_DRIFT_APPLY_THR_MM:0` |
| Suppress risk warnings | `SET ADV_RISK_THRESHOLD:9999` |
| Reset EWMA | Any of: `sync_disable` cycle, settings `LOAD`, `BUF_SENSOR_TYPE` hot-swap (D4 idle path), `EV:SYNC,ADV_DWELL_STOP`. |
| Full revert | `git revert <2.6 commit>` (single revert, depends only on 2.5). |

---

## 8. Final Phase Name

**Phase 2.6 — Drift Observer & ADVANCE-Risk Telemetry.**

## 9. Why This Will Fix It (one paragraph)

Phase 2.5 added a confidence-gated *integrator* over reserve error, but the
integrator is a symptom-side correction: it tries to tune away drift it
cannot see. Phase 2.6 puts a sensor on the drift itself. Every zone
transition is the only moment the controller has ground truth (a real
switch fired at a known position), and the residual `pre_snap_pos_mm −
switch_pos_mm` is the literal mismatch between virtual and physical at
that instant. Accumulating that residual into a slow EWMA gives a
signed, bounded estimate of the systematic offset; gating its application
on sample count, confidence, and sign-agreement prevents single-event bias
from leaking into control. Default-OFF apply means the parity gate (§4.5)
holds for the instrumentation step alone — operators can prove the
hypothesis with data before the firmware acts on it. Layered on top, the
ADVANCE-risk window provides the upstream warning the maintainer asked for
without changing control law. If Phase 2.6 evidence shows the residual is
not the dominant cause (`BPD ≈ 0`), we have ruled out cause-chain B and
move on to Phase 3.5 (estimator overhaul, Candidate 4) with data instead
of a guess.

---

## 10. Implementation Checklist (execute in order)

- [ ] **2.6.0-1** `firmware/src/sync.c`: residual-at-transition + EWMA +
      pin-window state. **[I]**
- [ ] **2.6.0-2** `firmware/include/sync.h`: four new accessors. **[I]**
- [ ] **2.6.0-3** `firmware/src/protocol.c`: append `BPR/BPD/BPN/APX` at
      status tail. **[I]**
- [ ] **2.6.0-4** `MANUAL.md`: document the four fields. **[I]**
- [ ] **2.6.0-5** `BEHAVIOR.md`: residual observer paragraph
      (no behavior change yet). **[I]**
- [ ] **2.6.0-6** Build local + STATUS smoke test. Commit & push (per
      `feedback_commit_push.md`).
- [ ] **2.6.0-7** Operator capture ≥ 1 print session; review BPR
      distribution. **Decision gate.**
- [ ] **2.6.1-1** `config.ini` + `config.ini.example` +
      `scripts/gen_config.py`: five new tunables (defaults from §3
      Candidate 2). Regenerate `tune.h`. **[B default-OFF]**
- [ ] **2.6.1-2** `firmware/include/controller_shared.h`: five externs.
- [ ] **2.6.1-3** `firmware/src/main.c`: five definitions.
- [ ] **2.6.1-4** `firmware/src/settings_store.c`: bump 45u → 46u; add,
      save, load, defaults for the five fields. Document in commit body.
- [ ] **2.6.1-5** `firmware/src/sync.c`: drift correction at controller-
      side `g_buf_pos` reads; reset on disable / fallback / hot-swap /
      version-mismatch; freeze on confidence < apply_min_confidence.
- [ ] **2.6.1-6** `firmware/src/protocol.c`: SET/GET for five keys plus
      `RDC:` tail field.
- [ ] **2.6.1-7** §4.5 endstop-mode parity gate with defaults OFF.
      **Must pass before merge.**
- [ ] **2.6.1-8** Operator soak with `apply_threshold=0.5,
      min_samples=4, clamp=2.0`; compare against §6 acceptance.
- [ ] **2.6.2-1** `firmware/src/sync.c` + `protocol.c`: warn-only
      `EV:SYNC,ADV_RISK_HIGH`, runtime-only tunables. **[B default safe]**
- [ ] **2.6.2-2** Operator monitor for false-positive risk events;
      tune threshold if needed.
- [ ] **2.6.X-docs** Append Phase 2.6 to `SYNC_REFACTOR_PLAN.md`. Update
      `TASK.md`. Note settings version bump in commit body.

End of Phase 2.6 plan.
