# Implementation Plan — Trailing-Biased Sync Hardening + Virtual Analog Buffer Abstraction

> **Status:** revised to incorporate maintainer Decision Addendum (see §0).
> Phases 0–2.5 are shippable without PSF hardware. All analog hardware-dependent
> validation is deferred to Phase 3+. Endstop-mode parity is the release gate.

## 0. Decision Addendum (Fixed Constraints)

These are confirmed by the maintainer and govern every phase below. Where any
section conflicts with this list, this list wins.

| # | Topic | Decision |
|---|---|---|
| D1 | PSF electrical contract | Treat as unknown until hardware lands. Implement a **generic** analog adapter with calibration hooks; **no PSF-specific electrical assumptions** baked into firmware. |
| D2 | Advance-dwell defaults | Assist start delay = **400 ms**; advance hard-stop = **6000 ms** (raised from 4000 ms — safer, less trigger-prone). Both runtime-tunable. |
| D3 | Settings version bump | Bump **only if** new persisted struct fields are added. Reusing existing analog fields or tune-only macros does **not** bump. |
| D4 | Hot-swap of `BUF_SENSOR_TYPE` | Permitted **only when fully idle**: `sync_enabled == false`, `tc_state == TC_IDLE`, `cut.state == CUT_IDLE`, `g_boot_stabilizing == false`. Otherwise return `ER:BUSY`. |
| D5 | Reload-follow control law | **Unchanged** in this iteration. Only confidence telemetry and warning events are added. No behavior delta in `TC_RELOAD_FOLLOW`. |
| D6 | Overshoot trim extension into `BUF_MID` | **Feature-flagged, default OFF.** Enabled only after A/B evidence from long-run and low-flow-tail soak logs. |
| D7 | Status field compatibility | **Additive at tail only.** No reordering, removal, or semantic change of existing fields. |

**Scope for this iteration**

- *Must include:* observability additions, advance-dwell hardening, source abstraction (virtual endstop + analog adapter), endstop-mode no-behavior-change guarantee.
- *Must exclude:* PSF hardware-specific tuning claims, confidence-driven reload-follow changes, any analog policy change that requires real sensor characterization.
- *Release gate:* endstop-mode parity. Analog path is simulation/bench-ready only until real hardware arrives.

---

## 1. Executive Summary

### Top 6 findings
1. **Sync core is implicitly typed by `BUF_SENSOR_TYPE`.** The control loop in `sync_tick()` (`firmware/src/sync.c:708`) and the limiter `sync_apply_scaling()` (`sync.c:215`) branch on the raw enum at multiple decision points. Any new sensor variant requires touching control logic, not just an adapter — the opposite of what the user wants.
2. **Estimator observability is asymmetric and entirely event-driven.** The dominant estimator update is the discrete arm-velocity calculation in `buf_update()` (`sync.c:489-555`). Continuous bleeds in `sync_tick()` (`sync.c:764-783`) only run after `SYNC_TRAILING_COLLAPSE_DELAY_MS`. While pinned, the estimator has no implicit confidence model, so the same numeric estimate has very different validity in `BUF_MID` vs `BUF_ADVANCE` vs `BUF_TRAILING`.
3. **Advance-side dwell has no positive-side timeout analogue.** `sync_tick()` only auto-stops on trailing pin (`sync.c:931-958`) or hard trailing wall (`sync.c:893-902`). When ADVANCE persists with `extruder_est_sps` low, the bleed-up at `sync.c:764-770` raises `extruder_est_sps` only toward `sync_current_sps`, which is itself bounded by the estimator — a circular ceiling that can stall forward catch-up if `zone_bias` saturates first. This is the exact extruder-overload risk noted in the prior incident.
4. **Reserve target geometry is asymmetric and under-instrumented.** `buf_target_reserve_mm()` (`sync.c:115-128`) places the target on the trailing side and applies a `SYNC_RESERVE_CENTER_GUARD_FRAC = 0.05` shift, but neither the achieved center, nor the dwell-time-in-band, nor the estimator-vs-measured drift are exposed as status fields. Tuning is forced to be log-archaeological.
5. **Damp/relaunch logic has been correct-by-patching and now embeds three implicit invariants.** `sync_is_positive_relaunch_damped()` (`sync.c:976-998`) clears damp on `BUF_ADVANCE` and on positive reserve error. The state machine for `sync_positive_relaunch_pending` is split between `sync_tick()` (set/clear at `sync.c:788-796`) and the predicate. This is brittle and hard to extend; an analog sensor with continuous oscillation could re-trigger this every frame.
6. **Long-run trailing centering drift goes undetected until late.** With Phase 1's discrete advance-dwell guard in place, the proportional/zone/slope terms still leave the reserve target as a fixed offset (`buf_target_reserve_mm()` at `sync.c:115-128`). Slow integration error in `buf_virtual_position_tick()` (`sync.c:149-176`) during long `BUF_MID` dwells, plus small extruder/MMU rate mismatches inside estimator tolerance, can settle the arm closer to the advance side over 30+ minute prints. The discrete `AD:` timer only registers once a pin event occurs, so warning lead-time before clusters of advance pins is near zero.

### Top 6 proposed improvements
1. **Introduce `buf_signal_t` as the single signal contract** (normalized position, confidence, freshness, source kind, fault flag). All control logic in `sync_tick()` and `sync_apply_scaling()` should consume this — never `BUF_SENSOR_TYPE`, `g_buf_pos`, or `g_buf.state` directly.
2. **Add an explicit advance-dwell guard** that promotes `extruder_est_sps` and forces a paced `sync_current_sps` ramp toward the global cap when ADVANCE persists, with a configurable timeout (`sync_advance_dwell_ms`, mirroring `SYNC_AUTO_STOP_MS`).
3. **Make the damp predicate stateless** — derive damp solely from the current signal (reserve error sign, dwell, recent slope). Remove `sync_positive_relaunch_pending` as a latched bool.
4. **Expand status with a stable, source-agnostic block** (`CF:`, `SR:`, `EA:`, `AD:`, `TR:`) so tuning is observable in real time and the same fields work for both endstop and analog sources.
5. **Phase analog drop-in via an adapter, not a parallel code path.** The PSF-class proportional sensor becomes one more `buf_source_t` implementation with its own calibration/normalization, but the control law is shared.
6. **Add a low-gain integral centering term plus a confidence-aware mid-zone estimator.** A bounded, anti-windup integral over `reserve_error_mm()` provides slow trailing-bias correction without changing the proportional control law; in parallel, the endstop-mode mid-zone estimator gains a physics-based sigma model so the controller can react to (and report) loss of position confidence rather than treating a stale integrator as ground truth. Both are gated off by default — opt-in via tunables. Detailed in **Phase 2.5**.

---

## 2. Code-Evidence Section

Inspected modules / files (read in full or in the relevant ranges):

| File | Purpose for this analysis |
|---|---|
| `firmware/src/sync.c` | Core control loop, estimator, virtual-position integration, damp logic, wall-time guards |
| `firmware/include/sync.h` | Public sync API exposed to protocol/toolchange |
| `firmware/include/controller_shared.h` | `buf_state_t`, `buf_tracker_t`, runtime globals (`BUF_*`, `SYNC_*`) |
| `firmware/src/protocol.c` | Status dump fields, SET/GET tunable surface |
| `firmware/src/toolchange.c` | `TC_RELOAD_FOLLOW` uses `extruder_est_sps`, `g_buf.state`, `sync_trailing_wall_time_ms()` |
| `firmware/src/main.c` | Tick wiring, `set_toolhead_filament`, ADC init for `PIN_BUF_ANALOG` |
| `firmware/src/settings_store.c` | `settings_t` layout (`buf_sensor_type`, `buf_neutral`, …), `SETTINGS_VERSION 43u` |
| `scripts/gen_config.py` | `CONF_*` macro emission from `config.ini` |
| `config.ini`, `config.ini.example` | Runtime tunables source of truth |
| `BEHAVIOR.md`, `MANUAL.md` | Public contract for status/event semantics |

### Mapping findings to code

**Finding 1 — sensor-type leaks into the control law:**
- `buf_state_raw()` (`sync.c:291-308`) — sensor branch is correct here, this is the input layer.
- `sync_apply_scaling()` (`sync.c:215-244`) — branches on `BUF_SENSOR_TYPE == 1` to switch between an analog ramp and the threshold/deadband taper. This is policy, not adaptation.
- `sync_tick()` (`sync.c:802-812`, `sync.c:862-866`, `sync.c:893-902`) — wall-time guards are gated on `BUF_SENSOR_TYPE == 0` and silently disable for analog. The hard-wall stop is the safety net the user is asking us to preserve, so we must restate it in source-agnostic form before changing the gate.
- `buf_anchor_virtual_position()` (`sync.c:137-147`) and `buf_virtual_position_tick()` (`sync.c:149-176`) — endstop-only integration. Correct location, but should live behind the adapter.

**Finding 2 — observability collapse while pinned:**
- `buf_update()` (`sync.c:489-555`) only updates the estimator on a *zone transition*; the inner `if (fabsf(travel_mm) > 0.001f && prev_dwell > BUF_HYST_MS)` is the gate.
- The bleed-up while pinned `BUF_ADVANCE` (`sync.c:764-770`) chases `sync_current_sps`, not the true extruder rate. The bleed-down on `BUF_TRAILING` (`sync.c:776-783`) is symmetric. Both treat `sync_current_sps` as ground truth, which is only true at saturation — fine as a worst-case but uninstrumented.
- No notion of "estimator stale" beyond `SYNC_EST_FRESH_MS = 3000u` (`sync.c:19`) used only inside `sync_bootstrap_sps()`.

**Finding 3 — advance dwell is unbounded:**
- `sync_on_transition()` (`sync.c:663-687`) does not maintain an "advance since" timestamp, only `sync_continuous_trailing_since_ms` (`sync.c:931-958`).
- The closest existing knob is `SYNC_AUTO_STOP_MS`; it does not fire when stuck high.
- `sync_apply_scaling()` (`sync.c:215`) does not boost when ADVANCE-pinned, so output is `extruder_est_sps + reserve_correction + zone_bias + slope_bias` (`sync.c:868`) — capped by `SYNC_MAX_SPS`. If the estimator is conservative and zone_bias hits `ZONE_BIAS_MAX_SPS = 600`, headroom is bounded ~2.4 mm/s above the (possibly stale) estimator, which can be insufficient to refill against an over-pulling extruder before the printer stalls.

**Finding 4 — reserve geometry / introspection:**
- `buf_target_reserve_mm()` (`sync.c:115-128`) and `buf_virtual_deadband_mm()` (`sync.c:130-135`) are the geometry. They are correct but unscoped per source kind.
- Status dump in `protocol.c:91-128` exposes `BP`, `RE`, `EST`, `DP`, `PR`, `AV`, `SC`. It does **not** expose: target reserve mm, deadband, confidence, advance-dwell ms, or trailing wall ms.

**Finding 5 — damp state machine fragility:**
- Set/clear in `sync_tick()` (`sync.c:788-796`) updates `sync_positive_relaunch_pending` and `sync_recent_negative_until_ms`.
- Predicate `sync_is_positive_relaunch_damped()` (`sync.c:976-998`) silently *mutates* the latch on read (clears on advance-pin, clears on positive RE). The status dump (`protocol.c:117`) calls this predicate, so reading status can change control state — a subtle invariant.

---

## 3. Detailed Implementation Plan (Phased)

Each phase is independently shippable, build-clean (`ninja -C build_local`), and revertible by reverting its commits without breaking earlier phases.

---

### Phase 0 — Instrumentation & Observability (no behavior change)

**Goal:** Make the next four phases measurable. Zero functional change to the controller loop.

**Modules touched**
- `firmware/src/protocol.c` — extend status dump.
- `firmware/include/sync.h` + `firmware/src/sync.c` — add read-only accessors.
- `MANUAL.md`, `BEHAVIOR.md` — document new fields.

**Data model changes**
- New file-static counters in `sync.c`:
  - `sync_advance_pin_since_ms` (advance-dwell timer, mirror of trailing one).
  - `sync_signal_age_ms` last-update stamp for whatever source produced `g_buf_pos`.
  - `sync_est_last_event_ms` (renamed accessor over existing `extruder_est_last_update_ms`).
- No `settings_t` fields. No version bump.

**Algorithm changes**
- None. The advance timer is *measured only*, never acted on in this phase.

**Status / protocol additions** (status string, `cmd_status` in `protocol.c`)
Per **D7**, all new fields are **appended at the tail** of the existing comma-separated string. No existing field changes position, name, or semantics.
- `RT:` — reserve target in mm (signed, two decimals).
- `RD:` — reserve deadband in mm.
- `AD:` — advance-pin dwell ms.
- `TD:` — trailing-pin dwell ms (already partially implicit in `g_buf.entered_ms`; expose explicitly).
- `TW:` — current trailing-wall time ms (reuse `sync_trailing_wall_time_ms`).
- `EA:` — estimator age ms since last meaningful update.

**Tunables**
- None added.

**Risks**
- Status string length: count current width (~ 350 bytes buffer at `protocol.c:91`). New fields add ~50 bytes — increase the buffer to 480 and verify on hardware. Existing parsers must accept new comma-separated trailing fields (they already accept unknown trailing fields per `BEHAVIOR.md`).

**Acceptance**
- `STATUS` returns all new fields in correct units.
- Backward compat: every previously documented field is still present, in the same position, with the same semantics.
- Doc sync: `MANUAL.md` "Status fields" table updated; `BEHAVIOR.md` "Velocity estimator" + "Buffered reserve target" sections reference the new instrumentation.
- Build green; no functional regressions in long-print, reload, toolchange, preload flows.

---

### Phase 1 — Control Logic Hardening for Trailing Bias and Advance Dwell

**Goal:** Eliminate the rare advance-dwell extruder-stall path; tighten trailing recovery without giving up reserve hold.

**Modules touched**
- `firmware/src/sync.c` (primary).
- `firmware/include/sync.h` (predicate signatures).
- `firmware/src/protocol.c` (SET/GET for new tunables, status `DP:`/`PR:` semantics unchanged).
- `firmware/src/settings_store.c` (persist new tunables). Per **D3**, this bumps `SETTINGS_VERSION` (43u → 44u) **because new persisted fields are added** (`sync_advance_dwell_stop_ms`, `sync_advance_ramp_delay_ms`). If any of these are decided to live as tune-only macros (no SAVE/LOAD), the bump is dropped.
- `scripts/gen_config.py`, `config.ini`, `config.ini.example` (defaults).

**Data model changes**
- Replace latched `sync_positive_relaunch_pending` with derivation in `sync_is_positive_relaunch_damped()` from: current `sync_reserve_error_mm()`, `g_buf.state`, time since last *measured* negative excursion (kept in `sync_recent_negative_until_ms` but read-only outside sync.c), and tail-assist flag. Keep public predicate signature stable.

**Algorithm changes**
1. **Advance-dwell guard (the headline fix).** In `sync_tick()`, after `sync_on_transition()`:
   - Maintain `sync_advance_pin_since_ms` (set on entry to `BUF_ADVANCE`, cleared otherwise).
   - When `BUF_ADVANCE` persists past `SYNC_ADVANCE_RAMP_DELAY_MS` (new, default **400 ms** per **D2**): force `sync_current_sps` to climb at `SYNC_RAMP_UP_SPS` toward `SYNC_MAX_SPS` regardless of the estimator-bound target. This is the *refill assist*; the estimator no longer caps refill while the buffer is empty.
   - When `BUF_ADVANCE` persists past `SYNC_ADVANCE_DWELL_STOP_MS` (new, default **6000 ms** per **D2**): emit `EV:SYNC,ADV_DWELL_STOP`, call `sync_disable(true)`. Conservative default chosen to be safer than no guard while staying clear of normal print transients.
   - Estimator promotion: while pinned in advance and `sync_current_sps` is high, raise `extruder_est_sps` toward the *delivered MMU rate plus arm velocity* once a `BUF_ADVANCE → BUF_MID` transition fires (existing path already handles this, but keep the explicit relearn alpha at min(`EST_ALPHA_MAX`, 0.65) on this transition).
2. **Trailing collapse — quieter near the target.** In `sync_apply_scaling()`, the `BUF_MID`-side taper currently halves taper effort and dynamically floors at `extruder_est_sps * 0.45`. When `|reserve_error_mm| < deadband` (i.e. inside the dead-band on the trailing-bias side), suppress trailing taper entirely — only apply it once the position is *outside* the deadband. This stops micro-oscillation around BP ≈ -4.5 from pulling speed down unnecessarily.
3. **Stateless damp.** Reimplement `sync_is_positive_relaunch_damped()` as: damp only when `(now_ms - sync_recent_negative_until_ms) is within an opening window of (SYNC_RECENT_NEGATIVE_HOLD_MS - 300 ms)`, **and** `g_buf.state != BUF_ADVANCE`, **and** `sync_reserve_error_mm() <= deadband/2`. Remove the read-side latch mutation. Keep the API.
4. **Reserve-overshoot interaction (gated, default OFF per D6).** `SYNC_OVERSHOOT_PCT` currently only acts in `BUF_TRAILING` (`sync.c:853-859`). Add an extension that also acts in `BUF_MID && reserve_error < -deadband` so brake authority is available before the arm pins. Gate it behind a new tunable `sync_overshoot_mid_extend` (default `0` = OFF). Will be enabled only after A/B evidence from long-run and low-flow-tail soak logs.

**Tunables to add**
| Key (config.ini) | Range | Default | Purpose |
|---|---|---|---|
| `sync_advance_dwell_stop_ms` | 0–30000 | **6000** | Hard-stop after sustained empty-side pin. 0 disables. (D2) |
| `sync_advance_ramp_delay_ms` | 0–5000 | 400 | Grace window before refill assist takes over. (D2) |
| `sync_overshoot_mid_extend` | 0/1 | **0** | Feature flag for change #4. Default OFF per D6. |

Wired through `gen_config.py` → `tune.h` → `controller_shared.h` extern → `protocol.c` SET/GET (`SYNC_ADV_STOP_MS`, `SYNC_ADV_RAMP_MS`) → `settings_store.c` (persist + load + bump version).

**Status / protocol impact**
- New event: `EV:SYNC,ADV_DWELL_STOP`.
- New SET/GET keys (above).
- `DP:` and `PR:` semantics unchanged (still booleans, but now derived statelessly).

**Risks**
- Advance assist can pump too aggressively if extruder is genuinely off. Mitigation: it acts only inside `auto_start_allowed && sync_enabled`, and `SYNC_MAX_SPS` already bounds it. The 4000 ms hard stop is the ultimate guardrail.
- Stateless damp may flicker; mitigated by the 300 ms opening guard.
- Settings version bump wipes user settings — call this out in commit message.

**Acceptance**
- Synthetic test: hold simulated `BUF_ADVANCE` for 7 s — observe assist kick in at 400 ms, hard stop at 6000 ms, `EV:SYNC,ADV_DWELL_STOP` emitted exactly once.
- Long-run regression: in a 30-min print, `AD:` peak < 2500 ms (well below the 6000 ms stop threshold).
- Trailing-bias steady state: reserve error standard deviation reduced vs Phase 0 baseline (record from `RE:` log).
- No regression in: preload, load, unload, toolchange (MMU and RELOAD), reload follow, persistence, autostop on trailing, hard trailing wall.
- **Endstop-mode parity gate (must pass before phase merges):** see §4.5.

---

### Phase 2 — Virtual Analog Buffer Abstraction (internal refactor, no policy change)

**Goal:** Introduce `buf_signal_t` and `buf_source_t` so `sync_tick()` is sensor-agnostic. **Zero observable behavior change** when source is dual-endstop (default). Analog mode preserves current behavior bit-for-bit.

**New header: `firmware/include/buf_signal.h`**
- Type `buf_signal_t` exposed in this header:
  - `float pos_norm` — normalized position, `-1.0` (full trailing) … `+1.0` (full advance).
  - `float pos_mm` — physical mm equivalent (positive = advance side).
  - `float confidence` — `0.0`–`1.0`. For endstop adapter: 1.0 at switch transitions, decays linearly with time-since-transition; for analog adapter: 1.0 when ADC variance is normal, drops on saturation/timeout.
  - `uint32_t age_ms` — ms since this signal was last refreshed by the source.
  - `buf_state_t zone` — quantized state for legacy consumers (`BUF_MID/ADVANCE/TRAILING/FAULT`).
  - `buf_source_kind_t kind` — `BUF_SRC_VIRTUAL_ENDSTOP`, `BUF_SRC_ANALOG`.
  - `bool fault` — adapter-reported fault.
- A `buf_source_t` vtable: `init`, `tick(now_ms, motion_ctx)`, `read(buf_signal_t *out)`, `force_state(buf_state_t)`, `name`.

**New file: `firmware/src/buf_source_endstop.c`**
- Wraps existing logic from `sync.c`: `buf_state_raw()` for endstops, `buf_read_stable()` debounce, `buf_anchor_virtual_position()`, `buf_virtual_position_tick()`.
- Confidence model: 1.0 for the first `BUF_HYST_MS` after a transition, then linearly decays toward 0.4 over the next 1500 ms (model error grows with integration time). Bumps back to 1.0 on any new transition.

**New file: `firmware/src/buf_source_analog.c`**
- Wraps `buf_analog_update()` and ADC selection for `PIN_BUF_ANALOG`.
- Uses `BUF_NEUTRAL`, `BUF_RANGE`, `BUF_THR`, `BUF_ANALOG_ALPHA`, `BUF_INVERT` exactly as today.
- Confidence model: drops to 0.0 if no ADC sample for `> 4 × SYNC_TICK_MS`, drops to 0.5 if normalized value is saturated (`fabs(pos_norm) ≥ 0.99` for > 250 ms). Otherwise 1.0.

**`firmware/src/sync.c` changes**
- Replace direct reads of `g_buf_pos`, `BUF_SENSOR_TYPE`, and `g_buf.state` inside the control loop with a single local `buf_signal_t` produced by the active source's `read()`.
- `sync_apply_scaling()`: collapse the two branches into one — always operate on `pos_norm` and `confidence`. The endstop-specific reserve geometry (`buf_target_reserve_mm()`) becomes the signal-domain reserve target; conversion to mm uses `buf_physical_half_travel_mm()` (kept as a helper, but consumed only via the adapter).
- `sync_tick()`: wall-time math (`sync_trailing_wall_*`) gated on `signal.kind == BUF_SRC_VIRTUAL_ENDSTOP || (kind == BUF_SRC_ANALOG && confidence >= analog_wall_min_confidence)`. The hard-wall stop is preserved exactly for endstop mode.
- Backward compat shim: keep `g_buf_pos` and `g_buf.state` as exported globals — populated each tick from `signal.pos_mm` and `signal.zone`. This preserves `protocol.c` status fields and `toolchange.c` reads (`g_buf.state` checks at `toolchange.c:431,439,457,483-487,499,530,544`). No changes required in `toolchange.c` for this phase.

**Selection logic**
- A source registry indexed by `BUF_SENSOR_TYPE`: `0 = endstop`, `1 = analog`. Selected at boot in `main.c` before `boot_stabilize_start()`.
- Hot-swap on `SET BUF_SENSOR:n` (per **D4**) requires **all** of the following idle conditions; otherwise return `ER:BUSY`:
  - `sync_enabled == false`
  - `g_tc_ctx.state == TC_IDLE`
  - `g_cut.state == CUT_IDLE`
  - `g_boot_stabilizing == false`
  Document this in `MANUAL.md`.

**Settings / config**
- No new tunables in this phase. No `settings_t` change. No version bump.

**Status / protocol impact**
- Per **D7**, fields are appended at the tail.
- New status field `SK:` — source kind (`E` or `A`).
- New status field `CF:` — confidence (0–100 integer).

**Risks**
- Refactor blast radius: `sync.c` is the busiest module. Mitigation: keep all existing functions in place; introduce the adapter as a thin wrapper that reads the *same* state. Submit as one self-contained PR with the adapter functions trivially equivalent to the inlined code.
- `g_buf.state` is read by `toolchange.c` and the protocol; we preserve it as a derived field, not a primary one.

**Acceptance**
- Endstop mode: `STATUS` produces identical numeric values for `BP`, `BUF`, `RE`, `EST`, `AV` as Phase 1 baseline within ±1 LSB across a full print.
- Analog mode: same bit-for-bit equivalence for `BP`, `BUF`, `RE`.
- New fields `SK:` and `CF:` populated correctly in both modes.
- `toolchange.c` and `protocol.c` unchanged (verify with `git diff --stat`).
- Build green; full regression suite (preload/load/unload/TC/RELOAD/sync/persistence) parity vs Phase 1.

---

### Phase 2.5 — Trailing-Side Centering Hardening + Confidence-Aware Mid Estimator

> **Purpose.** Phase 1 hardened the *advance* edge with a discrete dwell guard.
> Phase 2 unified the signal contract via `buf_signal_t`. Phase 2.5 closes the
> remaining loop on the *trailing* side: a slow centering correction so long
> runs do not drift toward advance, plus a physics-based confidence model for
> the endstop virtual estimator so the controller can act on — and surface —
> loss of position certainty. Endstop mode remains the production default and
> behavior-compatible (D5/D7); the new control term ships **off by default**.

**Scope guard.** Per **D1**, no PSF-specific assumptions are introduced. Per
**D5**, no change to `TC_RELOAD_FOLLOW`. Per **D6**, the MID overshoot
extension stays untouched here. Default-off tunables are the rollback path.

#### Findings

1. **Reserve targeting can settle near center/advance over long runs.**
   `buf_target_reserve_mm()` (`sync.c:115-128`) returns a fixed trailing-side
   offset (`SYNC_RESERVE_CENTER_GUARD_FRAC = 0.05`). The control law applies
   `zone_bias`, `slope_bias`, and proportional reserve correction, but there
   is no slow-feedback term that accumulates "how much time have we spent on
   the advance side over the last minute". Small rate mismatches inside the
   estimator's tolerance accumulate into reserve-position bias that the
   dead-band (`buf_virtual_deadband_mm()`) hides from the proportional path.

2. **Endstop-mid virtual estimation loses confidence between transitions.**
   While `g_buf.state == BUF_MID`, both endstops are open and
   `buf_virtual_position_tick()` (`sync.c:149-176`) is an open-loop step
   integrator. Re-anchoring only happens at zone transitions via
   `buf_anchor_virtual_position()` (`sync.c:137-147`). Phase 2's confidence
   model decays linearly with wall time — it does not see the actual
   integrated step count, so identical wall-clock dwells with very different
   motion histories report identical confidence. The control law ignores
   `confidence < 1.0` today.

3. **Together they hide advance-dwell risk until late.** Phase 1's
   `SYNC_ADVANCE_DWELL_STOP_MS = 6000` only starts on a pin event. Slow
   centering drift increases the *probability* of advance pinning under
   transient extruder spikes but never registers on `AD:` until the pin
   actually trips. Operationally this means a 25-minute run can look healthy
   on `AD:` and then enter a regime where back-to-back advance pins cluster
   with little upstream warning.

#### Control improvements (trailing-side centering)

Three candidate methods, evaluated:

| # | Method | Pros | Cons |
|---|---|---|---|
| 1 | Static reserve offset increase (raise `SYNC_RESERVE_CENTER_GUARD_FRAC` from 0.05 to ~0.10) | Trivial; instant rollback. | Uniform shift gives up trailing headroom on every run, including healthy ones. No adaptation. |
| 2 | Adaptive reserve target shift by dwell history (rolling 60 s buckets of zone-time, shift target proportional to advance-side excess) | Reactive and bounded. | New rolling-history state machine, non-trivial bootstrap and reset semantics, several new policy surfaces. |
| 3 | **Low-gain integral reserve centering with anti-windup** (small `I` over `reserve_error_mm()`, long time constant, hard-clamped, frozen at pin events) | Single scalar of control surface; bounded by clamp; standard PID hygiene already used elsewhere; rollback = set gain to 0. | New persisted field → settings version bump. Requires careful freeze conditions to avoid windup. |

**Selected: Method 3** — low-gain integral with anti-windup. Rationale: it
adds the smallest possible control surface (one extra additive bias on the
reserve target), the integrator is bounded and self-resetting, and the
rollback path is a single `SET sync_reserve_integral_gain:0`. Method 1 is
too coarse and gives up trailing headroom unconditionally. Method 2's
hidden rolling-history state is harder to reason about under fault.

**Anti-windup, clamping, saturation rules:**

- *Integration enabled* only when **all** of: `g_buf.state == BUF_MID`,
  estimator `confidence ≥ 0.7`, `sync_enabled == true`, and
  `tc_state == TC_IDLE`.
- *Frozen (no integration)* when `g_buf.state ∈ {BUF_ADVANCE, BUF_TRAILING}`
  to prevent windup against pin events; frozen during any non-idle
  toolchange/cutter/stabilization state.
- *Output clamp:* `|integral_term_mm| ≤ sync_reserve_integral_clamp_mm`
  (default 0.6 mm — well below `buf_virtual_deadband_mm()` so the integral
  cannot dominate the proportional term).
- *Reset to 0* on `sync_disable()`, `sync_reset_estimator()`, `BUF_SENSOR_TYPE`
  hot-swap (D4 idle path), settings `LOAD`, and any `EV:SYNC,ADV_DWELL_STOP`
  (slate is wiped after any abnormal event).
- *Bootstrap:* zero on cold start; not restored from settings (the integral
  *value* is runtime-only — only the *gain/clamp tunables* are persisted).

#### Virtual estimation improvements (endstop-mode confidence)

*Confidence-aware MID estimator design:*
- Augment `buf_source_endstop.c` (introduced in Phase 2) with a per-tick
  uncertainty `pos_mm_sigma` alongside `pos_mm`. Initial sigma at zone
  transition = 0.05 mm. Sigma grows as
  `sqrt(integrated_step_count) × per_step_sigma`, where `per_step_sigma` is
  derived from `BUF_NEUTRAL_SLOPE` plus a fixed jitter term.
- The Phase 2 wall-clock linear decay is replaced with this physics-based
  growth: identical wall-clock dwells with very different motion histories
  now report different confidences.
- `confidence` becomes a function of sigma:
  `confidence = clamp(1.0 - sigma / est_sigma_hard_cap_mm, 0.0, 1.0)`
  with `est_sigma_hard_cap_mm` default 1.5 mm.

*Transition correction rules at switch crossings:*
- On `MID → ADVANCE` or `MID → TRAILING`: snap `pos_mm` to the corresponding
  switch threshold position; reset sigma to 0.05 mm. Compute and (in
  observability) log `pos_mm_residual = pre_snap_pos_mm − switch_pos_mm`
  as the integrated-error sample. Do not auto-recalibrate from this in v1.
- On `ADVANCE → MID` or `TRAILING → MID`: snap to threshold + `BUF_HYST_MM`
  toward center; reset sigma. (Maintains current Phase 0/1/2 anchoring
  semantics in `buf_anchor_virtual_position()` — this phase only refines the
  *uncertainty* tracked alongside `pos_mm`.)
- Persistent residual bias (rolling mean of `pos_mm_residual`) is surfaced on
  status only; no auto-action this iteration (deferred — see open questions).

*Confidence decay and staleness model:*
- `confidence < est_low_cf_warn_threshold` (default 0.5) for > 1000 ms emits
  `EV:BUF,EST_LOW_CF` (rate-limited, 1 per 5 s).
- `confidence < est_fallback_cf_threshold` (default 0.2) freezes the integral
  centering term (it does not decay — frozen value held).
- `confidence == 0.0` (sigma exceeded `est_sigma_hard_cap_mm`) is treated as
  "no usable position" — controller falls back to last-known *zone* only,
  exactly as today.

*Reset / fallback rules on faults and mode transitions:*
- On `signal.fault == true` (from any source adapter): zero integral, set
  sigma to `est_sigma_hard_cap_mm`, emit `EV:BUF,EST_FALLBACK`.
- On `sync_disable()` or `tc_state != TC_IDLE`: freeze integration, hold
  sigma at current value (no reset of `pos_mm`).
- On `BUF_SENSOR_TYPE` hot-swap (D4 idle conditions only): full reset.
- On boot: sigma initialized to `est_sigma_hard_cap_mm` until the first zone
  transition (signals "we don't know yet").

#### Tunables

| Key (config.ini) | Range | Default | Persistent? | Why default is conservative |
|---|---|---|---|---|
| `sync_reserve_integral_gain` | 0.0 – 0.05 (mm bias per mm·s of error) | **0.0** | persistent | Default 0.0 disables the new control term entirely — ships behavior-identical to Phase 2. Opt-in via SET; bench validation drives any non-zero ramp. |
| `sync_reserve_integral_clamp_mm` | 0.0 – 2.0 mm | **0.6** | persistent | Bounded well below `buf_virtual_deadband_mm()` so the integral can never dominate the proportional path. |
| `sync_reserve_integral_decay_ms` | 0 – 60000 ms | **0** (no decay while frozen) | persistent | Hold the integrator value when frozen; do not relax toward 0 prematurely. Conservative against transient pin events. |
| `est_sigma_hard_cap_mm` | 0.5 – 5.0 mm | **1.5** | persistent | Approximates physical buffer half-travel; sigma beyond this is meaningless. |
| `est_low_cf_warn_threshold` | 0.0 – 1.0 | **0.5** | runtime-only | Telemetry only; no need to persist. |
| `est_fallback_cf_threshold` | 0.0 – 0.5 | **0.2** | runtime-only | As above. |

The four persistent tunables are added to `settings_t`. Per **D3**, this
**bumps `SETTINGS_VERSION`** by one (44u → 45u if Phase 1 has merged;
otherwise 43u → 44u). Runtime-only tunables do not trigger a bump.

#### Observability

Per **D7**, all status fields are appended at the tail; no existing field is
moved or renamed.

| Field | Meaning | Units |
|---|---|---|
| `RI:` | Reserve integral term (signed) | mm, two decimals |
| `RC:` | Effective integral gain scalar (0 = frozen/disabled, 100 = full) | integer 0–100 |
| `ES:` | Estimator sigma | mm, two decimals |
| `EC:` | Estimator confidence (independent surface from Phase 2's `CF:` for source confidence) | integer 0–100 |

New events (additive):

- `EV:SYNC,ADV_DWELL_WARN` — emitted when `RI:` exceeds 50% of
  `sync_reserve_integral_clamp_mm` toward the advance side, OR when the
  rolling 60-s advance-pin count exceeds a threshold. Distinct from Phase 1's
  `ADV_DWELL_STOP` — this is the *upstream* warning the maintainer asked for.
- `EV:BUF,EST_LOW_CF` — confidence below `est_low_cf_warn_threshold` for
  > 1000 ms, rate-limited 1 per 5 s.
- `EV:BUF,EST_FALLBACK` — confidence dropped to 0 due to sigma overrun.

#### Acceptance criteria

3000 mm continuous-feed long-run trailing-bias scenario at nominal mm/s:

- *No monotonic drift toward advance in the final third.* Compute `mean(BP)`
  for the first 1000 mm, middle 1000 mm, and final 1000 mm. The final-third
  mean must not exceed the middle-third mean by more than **0.3 mm** in the
  advance direction.
- *Advance dwell below threshold under nominal feed.* `AD:` peak across the
  run < **1500 ms** (well below the 6000 ms hard stop); 95th-percentile
  `AD:` < **500 ms**.
- *Reserve variance not worse than baseline.* `stddev(RE)` over the run
  ≤ **1.05 ×** Phase 2 baseline. The integral term must not introduce
  oscillation.
- *No added false auto-stop events.* Zero occurrences of
  `EV:SYNC,ADV_DWELL_STOP`, `EV:SYNC,AUTO_STOP`, or `EV:BUF,EST_FALLBACK`
  during the run.
- *Estimator confidence health.* `mean(EC)` ≥ **70**; minimum (excluding the
  first 5 s of boot) ≥ **30**.

Both default-OFF (gain = 0.0) and bench-validated non-zero gain runs must
satisfy these criteria; the default-OFF run additionally must match Phase 2
on `BP:`/`RE:`/`EST:` within ±1 LSB to preserve the §4.5 parity gate.

#### Regression impact

| Flow | Impact |
|---|---|
| Preload | None. Integral frozen until `sync_enabled == true`; confidence model is cosmetic in idle. |
| Load (`FL`, `LO`) | None. `tc_state != TC_IDLE` freezes integral. |
| Unload (`UL`, `UM`) | None. Same gating as load. |
| Toolchange (MMU) | None. `tc_state` transition resets integral and sigma. |
| Sync (`SY`) | Targeted flow. With default `sync_reserve_integral_gain = 0.0`, behavior is identical to Phase 2. With non-zero gain, an additional bounded bias (≤ `sync_reserve_integral_clamp_mm`) is added to the reserve target. |
| Reload (`RELOAD`) | Control law unchanged per **D5**. New telemetry events may fire during follow but do not gate behavior. |
| Persistence | New persisted fields → settings version bump (44u → 45u, or 43u → 44u depending on Phase 1 status). Existing migration wipes cleanly. |
| Protocol / docs | Additive only (D7). New `RI:`, `RC:`, `ES:`, `EC:` fields; new SET/GET keys; new events. `MANUAL.md`, `BEHAVIOR.md` updated. |

**Phase 2.5 Status: DONE**

---

### Phase 2.6 — Drift Observer & ADVANCE-Risk Telemetry

> **Status: DONE** — settings version bump 45u → 46u. Rollback: `SET BUF_DRIFT_THR_MM:0`.

This phase adds a measurement layer to quantify the structural mismatch between the
virtual position model and physical reality. It introduces a transition-anchored
residual observer, default-OFF correction, and an additive ADVANCE-risk channel
for upstream warning.

#### Root-Cause Analysis
The system identifies a "structural drift" where the model reports "near target"
while the physical arm sits closer to the advance switch. The cause-chain is
identified as:
1. **Estimator self-reference:** In `BUF_MID`, the estimator blends toward the
   commanded MMU rate, which is itself derived from the estimator. This
   circularity prevents learning the true extruder rate if no transitions occur.
2. **Contaminated virtual position:** The low-biased estimator causes the virtual
   position to drift toward trailing in the model while the physical arm drifts
   toward advance.
3. **Starvation:** In a healthy print, transitions are rare, leaving the
   estimator frozen at potentially noisy values for long periods.

#### Chosen Implementation (Drift Observer)
Ground truth exists at the moment a switch fires. At each `MID → ADVANCE`
transition, the system now:
- Measures `pos_mm_residual = pre_snap_pos_mm − switch_pos_mm`.
- Accumulates residuals into a slow EWMA (`BPD`) with a 60s time constant.
- Optionally applies a bounded correction: `bp_eff = g_buf_pos − scaled_clamp(BPD, ±BUF_DRIFT_CLAMP)`.
- Ramps correction from the first sample to full strength at `BUF_DRIFT_MIN_SMP`.

#### Advanced Stability Fixes (Slow Speed & Drift Robustness)
Following real-world soak runs at slow speeds (300 mm/min), the following
refinements were added:
- **Estimator Stall Escape:** If the model position pins at a wall while the
  physical arm is still in `MID`, the estimator now "bleeds" toward a target rate
  slightly offset from the current MMU rate. This breaks circular stalls caused
  by drift correction masking model-physical divergence.
- **Ramping Bias (Confidence Bias):** When uncertainty is high (confidence < 1.0),
  the effective position is shifted toward **ADVANCE**. This creates a gentle
  feed pressure that ensures the arm gravitates toward the safe **TRAILING**
  switch during open-loop drift.
- **Uncertainty Speed Probe:** A direct speed boost (up to ~150 mm/min) is added
  to the target rate based on uncertainty, ensuring the system actively "probes"
  for a physical wall when the model is stale.
- **Adaptive Bootstrap Floor:** The bootstrap speed used when hitting the
  `ADVANCE` switch is now derived from the learned `baseline_sps`, preventing
  violent rate drops during slow prints.

#### ADVANCE-Risk Telemetry
Layered on top is a rolling advance-pin density counter (`APX`). If the pin count
exceeds `ADV_RISK_THR` (default 4) within `ADV_RISK_WINDOW` (default 60s), the
system emits `EV:SYNC,ADV_RISK_HIGH`. This provides operators with upstream
warning before a hard stop occurs.

**New status fields (additive tail per D7):** `BPR`, `BPD`, `BPN`, `APX`, `RDC`

**New events:** `EV:SYNC,ADV_RISK_HIGH`, `EV:BUF,DRIFT_RESET`

**New tunables (SET/GET):**

| Key | Default | Persisted | Description |
|---|---|---|---|
| `BUF_DRIFT_TAU_MS` | 60000 | yes | EWMA time constant (ms) |
| `BUF_DRIFT_MIN_SMP` | 3 | yes | Samples before full correction |
| `BUF_DRIFT_THR_MM` | 2.0 | yes | Apply threshold; 0=OFF |
| `BUF_DRIFT_CLAMP` | 3.0 | yes | Max correction magnitude (mm, runtime max 8.0) |
| `BUF_DRIFT_MIN_CF` | 0.5 | yes | Min confidence to apply |
| `ADV_RISK_WINDOW` | 60000 | runtime-only | Pin window (ms) |
| `ADV_RISK_THR` | 4 | runtime-only | Pin count threshold |

#### Operator Tuning Procedure
1. Observe `BPR` and `BPD` in logs. Consistently negative `BPR` on `MID → ADVANCE`
   transitions confirms systematic advance-side drift.
2. Enable correction: `SET BUF_DRIFT_THR_MM:0.5; SET BUF_DRIFT_MIN_SAMPLES:3`.
3. Monitor `RDC` (activity scalar) and verify `|BPD|` stays bounded and ADVANCE
   pins are reduced.

---

### Phase 2.7 — Trailing-Seeking Adaptive Sync, Mid-Zone Creep, Variance-Aware Position, Telemetry Pipeline & Automated Tuning

> **Status:** PROPOSED. Builds on Phase 2.6.x (drift observer + confidence
> bias). Depends on Phase 2.5 estimator-confidence surface (`EC:`, `ES:`) and
> Phase 2 `buf_signal_t` adapter.
>
> **Why now.** Soak runs at slow extrusion (≤ 600 mm/min) still settle the
> arm closer to the ADVANCE switch than to the TRAILING side. Phase 2.6
> proved the bias is real and measurable (`BPD` converges to ~−7.8 mm on a
> 300 mm/min run); the drift observer corrects the *control surface* but the
> *setpoint* still aims near center, and the open-loop integrator in `MID`
> has no time-pressure mechanism to actively seek the trailing wall during
> long mid-dwells. Phase 2.7 closes those gaps and ships an end-to-end
> telemetry/tuning pipeline so any further default change is data-driven
> rather than hand-fitted.
>
> **Scope guard.** Per **D5**, no change to `TC_RELOAD_FOLLOW`. Per **D6**,
> no MID-overshoot extension turn-on. Per **D7**, status fields appended at
> tail only. Every behavioral lever ships default-OFF (gain = 0 / disabled
> tunable) so endstop-mode parity (§4.5) is preserved by default.

#### 2.7.0 — Findings (post-2.6.x)

1. **Setpoint is fixed-fraction of half-travel.** `buf_target_reserve_mm()`
   (`sync.c:145-158`) returns `−SYNC_RESERVE_PCT/100 × threshold − center_guard`.
   At the user's `SYNC_RESERVE_PCT=50` and `BUF_HALF_TRAVEL=7.8` mm this is
   roughly −4.3 mm — geometrically center-trailing. After 2.6.x correction
   the controller does drive `g_buf_pos` toward this point, but the
   *physical* arm still settles 8–12 mm forward of it (advance side). The
   correction can only mask, not eliminate, the bias because the setpoint
   is not chosen with safety margin in mind.

2. **No mid-dwell time pressure.** The control loop has no mechanism that
   says "we have spent 8 s in MID with no transition; nudge MMU faster
   until we hit TRAILING and confirm position". The σ-confidence model
   (Phase 2.5) penalises stale estimates, and the Phase 2.6 confidence-bias
   shifts `bp_eff` toward advance, but neither term increases the
   *commanded* feed rate; both just bias the perceived position.

3. **No active wall-seek.** Once the system is in steady state at a
   slow-and-steady extrusion rate, the estimator settles, the integrator
   stops moving, and the arm holds wherever the residual drift left it.
   Phase 2.6 instrumentation does not see this as a fault because no pin
   event fires. The arm needs to *touch trailing* periodically to refresh
   ground truth; today it does not.

4. **Tuning is log-archaeology.** Every parameter shift in 2.5/2.6 was
   driven by parsing tail strings out of a manual run. There is no
   reproducible capture-and-analyse loop. Any further default change risks
   over-fitting to one operator's print profile.

#### 2.7.1 — Sub-phase overview

| Sub-phase | Title | Modules | Default-on? | Settings bump |
|---|---|---|---|---|
| 2.7.0 | Trailing-bias setpoint shift (extends `SYNC_RESERVE_PCT`) | `sync.c`, `tune.h`, `config.ini` | OFF (legacy `SYNC_RESERVE_PCT` path retained) | yes (one new field) |
| 2.7.1 | Mid-zone creep (active wall-seek) | `sync.c`, `tune.h`, `config.ini` | OFF (rate = 0) | yes (three new fields) |
| 2.7.2 | Variance-aware position blend (extends Phase 2.5 σ surface) | `sync.c`, `tune.h`, `config.ini` | OFF (blend = 0) | yes (two new fields) |
| 2.7.3 | Telemetry pipeline (firmware `MARK:` + host logger + g-code marker re-add) | `protocol.c`, `sync.c`, `scripts/` | host opt-in | no |
| 2.7.4 | Automated tuner (offline regression on captured CSVs) | `scripts/` | manual run | no |
| 2.7.5 | Optional firmware PID (Kp/Kd in addition to existing Ki) | `sync.c`, `tune.h`, `config.ini` | OFF (Kp = Kd = 0) | yes (two new fields) |

**Single combined settings bump:** 46u → 47u, applied once in 2.7.0 and
extended in 2.7.1/2.7.2/2.7.5 by appending fields to `settings_t` in the
same version. If sub-phases ship across separate releases, each field
addition bumps the version and the previous version is wiped clean.

---

### Sub-phase 2.7.0 — Trailing-Bias Setpoint Shift

**Goal.** Move the proportional setpoint from "center-trailing" toward "near
trailing endstop minus a small safety margin", *without* removing the
existing `SYNC_RESERVE_PCT` knob (which would invalidate every operator's
saved tune).

**Approach.** Extend `buf_target_reserve_mm()` with an additive bias term
`SYNC_TRAILING_BIAS_FRAC ∈ [0.0, 0.7]` that further shifts the target
toward the trailing endstop. Default 0.0 — no behavior change. Operator
ramps it up after observing 2.6.x soak data.

**Code (sync.c)**

```c
/* New tunable, declared in controller_shared.h, defined in tune.h via gen_config */
extern float SYNC_TRAILING_BIAS_FRAC;       /* 0.0..0.7; default 0.0 */

/* sync.c — buf_target_reserve_mm(), replaces existing body */
static float buf_target_reserve_mm(void) {
    float threshold = buf_threshold_mm();
    float physical_half = buf_physical_half_travel_mm();
    float pct = (float)SYNC_RESERVE_PCT / 100.0f;
    float bias = clamp_f(SYNC_TRAILING_BIAS_FRAC, 0.0f, 0.7f);
    float center_guard_mm = threshold * SYNC_RESERVE_CENTER_GUARD_FRAC;

    /* Legacy contribution (Phase 0/1 behaviour) */
    float target = -(threshold * pct);
    if (pct > 0.0f) target -= center_guard_mm;

    /* New trailing-bias contribution: additional offset toward trailing wall */
    target -= bias * threshold;

    /* Safety margin so we never sit at the wall — leave at least 0.5 mm */
    float min_target = -physical_half + 0.5f;
    if (target < min_target) target = min_target;
    if (target > threshold) target = threshold;
    return target;
}
```

**Anti-windup interaction.** When `g_buf_pos < raw_target − 0.25 × threshold`
(arm physically near trailing wall), pause `sync_reserve_integral_mm`
accumulation in the existing block at `sync.c:964-983`. Prevents the
2.5-era integrator from continuing to wind up while the arm is already
sitting at the desired position.

```c
/* sync.c — replace existing integral_active gate */
bool integral_active = (s == BUF_MID)
    && (SYNC_RESERVE_INTEGRAL_GAIN > 0.0f)
    && (g_buf_signal.confidence >= 0.7f)
    && (g_buf_pos > raw_target - buf_threshold_mm() * 0.25f);  /* NEW */
```

**Tunable (config.ini)**

```ini
# sync_trailing_bias_frac: 0.0   # additional setpoint shift toward trailing.
                                 # 0.0 = legacy behavior (SYNC_RESERVE_PCT only).
                                 # 0.4 typical for slow-extrusion soak; 0.7 max.
```

**Settings (settings_store.c)** add `float sync_trailing_bias_frac` to
`settings_t`, persist with `clamp_f(..., 0.0f, 0.7f)`. Bump
`SETTINGS_VERSION` 46u → 47u.

**Status (protocol.c)** new tail field `TB:` — current bias fraction × 100
as integer 0..70.

**SET/GET surface**: `SET:TRAIL_BIAS_FRAC:<float>`, `GET:TRAIL_BIAS_FRAC`.

**Acceptance**
- `SET:TRAIL_BIAS_FRAC:0.0` → identical numeric `RT:` to Phase 2.6.x baseline.
- `SET:TRAIL_BIAS_FRAC:0.4` at 300 mm/min, 5 min soak: mean `BP:` shifts 1.5 mm or more in trailing direction vs. baseline; ADVANCE-pin count does not increase.
- No new `EV:SYNC,ADV_DWELL_STOP` events at any bias value ≤ 0.6.

---

### Sub-phase 2.7.1 — Mid-Zone Creep (Active Wall-Seek)

**Goal.** When the arm has been in MID for more than `MID_CREEP_TIMEOUT_MS`
without a transition, additively increase MMU rate at a bounded ramp until
either (a) a TRAILING transition occurs (success: refresh ground truth and
reset creep), or (b) creep cap is hit (no progress: hold cap until next
transition or extruder demand changes). Fail-safe: any ADVANCE transition
resets creep to zero immediately.

**Approach.** Pure additive correction inside `sync_tick()` — does not
modify the estimator, does not modify `g_buf_pos`. Bound by
`MID_CREEP_CAP_FRAC × extruder_est_sps` so creep is zero when extruder is
idle (no false push during pause-extrude G-code).

**Code (sync.c)**

```c
/* File-static state */
static int g_mid_creep_sps = 0;
static uint32_t g_mid_creep_last_advance_ms = 0;

/* Reset hooks */
/* Inside sync_disable():           g_mid_creep_sps = 0; */
/* Inside sync_on_transition() when entering BUF_ADVANCE: */
/*   g_mid_creep_sps = 0; g_mid_creep_last_advance_ms = now_ms; */
/* Inside sync_on_transition() when entering BUF_TRAILING: */
/*   g_mid_creep_sps = 0;  // success — wall reached, reset */

/* sync_tick() addition — placed AFTER existing target_sps calculation,
 * BEFORE sync_apply_scaling() ramp limit. */
static void mid_creep_update(buf_state_t s, lane_t *A, uint32_t now_ms) {
    if (MID_CREEP_RATE_SPS_PER_S <= 0 || MID_CREEP_TIMEOUT_MS <= 0) {
        g_mid_creep_sps = 0;
        return;
    }
    if (s != BUF_MID) {
        g_mid_creep_sps = 0;
        return;
    }
    if (extruder_est_sps < 1.0f) {       /* extruder idle → no creep */
        g_mid_creep_sps = 0;
        return;
    }
    /* Cooldown after recent ADVANCE — don't immediately re-creep */
    if (g_mid_creep_last_advance_ms != 0 &&
        (now_ms - g_mid_creep_last_advance_ms) < (uint32_t)(MID_CREEP_TIMEOUT_MS * 2)) {
        g_mid_creep_sps = 0;
        return;
    }
    uint32_t dwell = now_ms - g_buf.entered_ms;
    if (dwell <= (uint32_t)MID_CREEP_TIMEOUT_MS) {
        g_mid_creep_sps = 0;
        return;
    }
    float dt_s = (float)SYNC_TICK_MS / 1000.0f;
    float step_sps = (float)MID_CREEP_RATE_SPS_PER_S * dt_s;
    int cap_sps = (int)((float)MID_CREEP_CAP_FRAC / 100.0f * extruder_est_sps);
    g_mid_creep_sps += (int)step_sps;
    if (g_mid_creep_sps > cap_sps) g_mid_creep_sps = cap_sps;
}

/* Apply the creep additively to the chosen target — placed at sync.c:~1154
 * just before/after sync_apply_scaling() depending on whether we want it
 * pre-taper (recommended: pre-taper, so creep is itself subject to the
 * trailing-side floor). */
mid_creep_update(s, A, now_ms);
target_sps += g_mid_creep_sps;
```

**Tunables**

| Key | Range | Default | Purpose |
|---|---|---|---|
| `mid_creep_timeout_ms` | 0–60000 | **0** (OFF) | Mid-dwell wait before creep activates. 0 disables. Recommended: 4000. |
| `mid_creep_rate_sps_per_s` | 0–200 | **0** | Creep ramp slope; zero disables. Recommended: 5–10. |
| `mid_creep_cap_frac` | 0–25 | **10** | Hard cap on creep as percentage of `extruder_est_sps`. |

All persisted (`settings_t` extension; same 47u bump as 2.7.0).

**Status (protocol.c)** new tail field `MC:` — current creep additive in
sps. Operators can watch the value rise during a long MID, fall to 0 on
TRAILING hit.

**Event** `EV:SYNC,MID_CREEP_CAP` rate-limited 1 per 5 s when creep
saturates (signals operator to either raise cap or accept the fact that
extruder demand exceeds expectation).

**Acceptance**
- With `mid_creep_rate_sps_per_s = 0`: zero behaviour change vs 2.7.0.
- Synthetic test at 300 mm/min steady extrusion with creep enabled (5 sps/s, 10%, 4000 ms): TRAILING hit observed within 30 s of sync start; `BPN` increments cleanly; no ADVANCE pin in 5-min run.
- Pause-extrude test (G-code with 30 s of zero E-moves): creep does not engage (extruder_est decays → cap is 0).
- ADVANCE recovery test (force ADVANCE with simulated runout): creep resets to 0 within one tick of transition; cooldown holds for 8 s.

---

### Sub-phase 2.7.2 — Variance-Aware Position Blend

**Goal.** When estimator confidence is low (high σ from Phase 2.5), blend
`g_buf_pos` toward `reserve_target` so the controller stops trusting a
stale integrator as ground truth. Replaces the ad-hoc Phase 2.6
"confidence bias" (`bp_eff += (1 − conf) × thr × 0.8`) with an explicit
Bayesian-prior pull whose strength is settable.

**Approach.** Compute a *trust* scalar from the existing `g_buf_sigma_mm`
(maintained in 2.5/2.6), then blend `g_buf_pos` toward `effective_target`
proportionally to `(1 − trust)`. Different from 2.6's bias because (a) the
target of the pull is the *setpoint*, not "advance side"; (b) it operates
on `g_buf_pos` itself, not on `bp_eff`, so the integrator and the drift
correction both see the regularised value; (c) it is gated by a tunable
strength so it can be turned off entirely.

**Code (sync.c)** — add inside `sync_tick()` immediately *before* the
2.6.x drift-correction block (`sync.c:919`):

```c
/* Phase 2.7.2: variance-aware position blend (default OFF) */
if (BUF_VARIANCE_BLEND_FRAC > 0.0f && g_buf_sigma_mm > 0.0f) {
    float sigma_ref = (BUF_VARIANCE_BLEND_REF_MM > 0.05f)
                      ? BUF_VARIANCE_BLEND_REF_MM : 1.0f;
    float distrust = clamp_f(g_buf_sigma_mm / sigma_ref, 0.0f, 1.0f);
    float blend = distrust * BUF_VARIANCE_BLEND_FRAC;
    g_buf_pos = (1.0f - blend) * g_buf_pos + blend * raw_target;
}
```

Note: this *mutates* `g_buf_pos`. Acceptable because (a) σ resets at every
zone transition, so the blend cannot cumulatively migrate the integrator;
(b) the pull target is the *setpoint*, which is the correct prior in the
absence of fresh information.

**Interaction with Phase 2.6 confidence-bias.** Replace the existing
`uncertainty_shift_mm` block at `sync.c:952-957` with:

```c
/* When 2.7.2 blend is active (BLEND_FRAC > 0), 2.6's confidence-bias is
 * redundant. Gate it off in that case. */
if (BUF_VARIANCE_BLEND_FRAC <= 0.0f) {
    float uncertainty_shift_mm = (1.0f - g_buf_signal.confidence) * (thr * 0.8f);
    bp_eff += uncertainty_shift_mm;
}
```

**Tunables**

| Key | Range | Default | Purpose |
|---|---|---|---|
| `buf_variance_blend_frac` | 0.0–0.9 | **0.0** (OFF) | Max blend fraction at full distrust. |
| `buf_variance_blend_ref_mm` | 0.5–5.0 | **1.0** | σ value at which distrust scalar saturates. |

Both persisted; settings bump shared with 2.7.0/2.7.1.

**Status** — new tail fields `VB:` (current blend scalar × 100 as int) and
`BPV:` (post-blend `g_buf_pos`, mm × 100 as signed int — distinct from
`BP:` which now reports the *unblended* raw value to preserve §4.5
parity).

**Acceptance**
- `buf_variance_blend_frac = 0.0`: zero deviation from 2.6.x baseline (parity).
- With blend = 0.5 and synthetic σ injection: `BPV:` tracks `g_buf_pos` toward `RT:` proportionally; integrator does not run away during forced 30 s mid-dwell.
- Default-OFF runs preserve the §4.5 parity gate.

---

### Sub-phase 2.7.3 — Telemetry Pipeline (firmware `MARK:` + host logger + re-added g-code marker)

**Goal.** Capture per-layer / per-feature buffer behaviour as a CSV the
host can analyse offline. Replaces ad-hoc tail-string parsing with a
single reproducible pipeline.

**Architecture**

```
slicer.gcode
    │
    ├── scripts/gcode_marker.py  (re-added; preprocesses gcode, injects
    │                             M118 NOSF_TUNE:<feature>:V<vfil>:W<w>:H<h>)
    ▼
slicer.gcode.tuned
    │  printed via Klipper
    ▼
M118 lines  ──►  Klipper macro `NOSF_MARK` (in macros.cfg)
                                   │
                                   ▼
                       nosf_cmd.py MARK:<tag>
                                   │   (USB CDC, asynchronous)
                                   ▼
                       firmware: g_marker_seq++; g_marker_tag = "<tag>"
                                   │   (next STATUS dump carries them)
                                   ▼
                       /dev/ttyACM0  ──►  scripts/nosf_logger.py
                                                  │
                                                  ▼
                                       /var/log/nosf/run_YYYYmmdd-HHMMSS.csv
```

**Firmware changes (very small)**

`firmware/include/protocol.h` — declare `MARK:` as a recognised command.

`firmware/src/protocol.c` — new handler:

```c
} else if (strncmp(cmd, "MARK:", 5) == 0) {
    const char *tag = cmd + 5;
    size_t n = strlen(tag);
    if (n >= sizeof(g_marker_tag)) n = sizeof(g_marker_tag) - 1;
    memcpy(g_marker_tag, tag, n);
    g_marker_tag[n] = '\0';
    g_marker_seq++;
    cmd_reply("OK", "MARK");
}
```

New globals in `controller_shared.h` / `controller_shared.c`:

```c
extern char     g_marker_tag[32];
extern uint16_t g_marker_seq;
```

Status emission (additive tail per **D7**) — append `MK:<seq>:<tag>` to
status string. Tag is short (≤ 31 chars), so payload growth is bounded
within the existing 480-byte status buffer.

No new tunables. No settings bump.

**Host: re-add `scripts/gcode_marker.py`** — restore from commit
`272c2d4:scripts/gcode_marker.py`, with two adjustments:

1. Drop M118 prefix change to `NOSF_TUNE:` exactly as in original (Klipper
   parses M118 messages reliably; the host listener uses regex match).
2. Add CLI flag `--every-layer` to additionally emit a marker on every
   `;LAYER:n` boundary (most slicers emit this comment).

**Host: new `scripts/nosf_logger.py`**

```python
#!/usr/bin/env python3
"""nosf_logger.py — async CSV capture of NOSF status + M118 markers.

Usage: nosf_logger.py --port /dev/ttyACM0 --out /var/log/nosf/run.csv
The logger:
  - opens a serial reader at 115200,
  - polls STATUS at 10 Hz (sends `STATUS\n` and parses tail fields),
  - listens for any `M118 NOSF_TUNE:...` lines on the same TTY (Klipper
    echoes them via the host UART by configuration; alternatively the
    user can pass --moonraker-url to subscribe to gcode_response events),
  - writes one CSV row per status sample, tagging with the most-recent
    marker seq/tag.

Dependencies: pyserial, optional websockets for moonraker mode.
"""
import argparse, csv, re, sys, time, threading, queue
import serial

STATUS_RE = re.compile(r'(?P<key>[A-Z]+):(?P<val>-?\d+(?:\.\d+)?|[A-Z]+)')
MARK_RE   = re.compile(r'MK:(?P<seq>\d+):(?P<tag>[^,]*)')
M118_RE   = re.compile(r'NOSF_TUNE:(?P<feature>[^:]+):V(?P<vfil>[^:]+):W(?P<w>[^:]+):H(?P<h>[^:]+)')

CSV_FIELDS = [
    'ts_ms','lane','zone','bp_mm','sigma_mm','est_sps','current_sps',
    'reserve_err_mm','rt_mm','ri_mm','ec','cf','bpd_mm','bpn',
    'apx','adv_dwell_ms','tb','mc','vb','bpv_mm',
    'marker_seq','marker_tag','feature','v_fil','width','height',
]

def parse_status(line):
    m = dict(STATUS_RE.findall(line))
    mk = MARK_RE.search(line)
    if mk:
        m['MK_SEQ'] = mk.group('seq')
        m['MK_TAG'] = mk.group('tag')
    return m

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', required=True)
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--out',  required=True)
    ap.add_argument('--rate-hz', type=float, default=10.0)
    args = ap.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=0.05)
    writer = csv.DictWriter(open(args.out, 'w', newline=''), fieldnames=CSV_FIELDS)
    writer.writeheader()

    last_feature = {'feature':'','v_fil':'','width':'','height':''}
    interval = 1.0 / args.rate_hz
    next_t = time.monotonic()
    while True:
        # 1. Drain incoming lines (STATUS replies + spontaneous events + M118 echoes)
        while True:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if not line: break
            m118 = M118_RE.search(line)
            if m118:
                last_feature = m118.groupdict()
                continue
            if line.startswith('STATUS'):
                fields = parse_status(line)
                row = build_row(fields, last_feature)
                writer.writerow(row)
        # 2. Send the next STATUS poll on schedule
        now = time.monotonic()
        if now >= next_t:
            ser.write(b'STATUS\n')
            next_t = now + interval

if __name__ == '__main__':
    main()
```

Helper `build_row(...)` maps the parsed status dictionary (and `last_feature`)
into the CSV column order. Implementation is mechanical; full source ships
in 2.7.3.

**Klipper macro (`KLIPPER.md` example block)**

```ini
# printer.cfg
[gcode_macro NOSF_MARK]
gcode:
    {% set tag = params.TAG|default('NA') %}
    RUN_SHELL_COMMAND CMD=nosf_mark PARAMS={tag}

[gcode_shell_command nosf_mark]
command: /usr/local/bin/python3 /home/pi/nosf/scripts/nosf_cmd.py MARK:%s
timeout: 2.0

# Hook into M118 if desired:
[gcode_macro M118]
rename_existing: M118.1
gcode:
    M118.1 {rawparams}
    {% if 'NOSF_TUNE:' in rawparams %}
        NOSF_MARK TAG={rawparams}
    {% endif %}
```

**Acceptance**
- `MARK:foo` returns `OK:MARK`; subsequent `STATUS` lines carry `MK:N:foo`.
- A 5-min print with `gcode_marker.py` preprocess produces a CSV with at least one row per sliced feature; `marker_tag` column is non-empty for ≥ 95 % of rows.
- Logger reconnects cleanly after USB disconnect (sleeps 1 s, retries).

---

### Sub-phase 2.7.4 — Automated Tuner (offline regression)

**Goal.** Take the CSVs produced by 2.7.3 and emit a `config.ini` patch
that improves `BASELINE_SPS`, `SYNC_TRAILING_BIAS_FRAC`, and (optionally)
per-feature feedforward gains. **No online ML, no RL.** Pure offline
ordinary-least-squares regression on aggregated buckets.

**Reasoning** (also recorded in §7 below):
- Online RL/online ML is unsafe under FDM (sparse rewards, long episodes,
  hours of plant time per gradient step, no rollback if a policy update
  causes a print failure).
- The actual learning surface here is small: ~6 features × ~10 v_fil bins
  = ~60 buckets per print. OLS converges in milliseconds.
- A Pi 4 runs `pandas + scikit-learn + lightgbm` comfortably. The output
  is a flat `config.ini` patch the operator reviews, applies, reflashes.

**Code (`scripts/nosf_analyze.py`, ~150 LoC)**

```python
#!/usr/bin/env python3
"""nosf_analyze.py — offline regression on nosf_logger.py CSVs.

Reads one or more CSVs, buckets rows by (feature, v_fil_bin), fits
per-bucket statistics, and emits a config.ini patch with suggested
BASELINE_SPS, SYNC_TRAILING_BIAS_FRAC, and optionally per-feature
feedforward coefficients.

Usage: nosf_analyze.py --in run1.csv run2.csv --out config.patch.ini
       [--mode safe|aggressive] [--feedforward]
"""
import argparse, configparser, csv, math, sys
from collections import defaultdict
import statistics as stats

SAFETY_K = {'safe': 1.5, 'aggressive': 1.0}     # σ-multiplier on baseline reserve

def bin_v_fil(v): return int(round(v / 5.0)) * 5  # 5 mm/s buckets

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inputs', nargs='+', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--mode', choices=['safe','aggressive'], default='safe')
    ap.add_argument('--feedforward', action='store_true')
    args = ap.parse_args()

    rows = []
    for path in args.inputs:
        with open(path) as fh:
            rows.extend(list(csv.DictReader(fh)))

    # 1. Bucket by (feature, v_fil_bin)
    buckets = defaultdict(list)
    for r in rows:
        try:
            v = float(r.get('v_fil') or 0.0)
            est = float(r.get('est_sps') or 0.0)
            zone = r.get('zone','')
            if v > 0 and est > 0 and zone == 'MID':
                buckets[(r.get('feature',''), bin_v_fil(v))].append(est)
        except ValueError:
            continue

    # 2. Per-bucket statistics
    summary = {}
    for key, vs in buckets.items():
        if len(vs) < 30: continue              # sparse bucket → skip
        summary[key] = {
            'n': len(vs),
            'mean': stats.mean(vs),
            'stdev': stats.stdev(vs),
            'p10': stats.quantiles(vs, n=10)[0],
        }

    # 3. Global recommendation: baseline = max bucket mean − k·stdev
    means = [s['mean'] for s in summary.values()]
    if not means:
        print('not enough data; need at least one populated bucket', file=sys.stderr)
        sys.exit(1)
    suggested_baseline = max(means)
    safety = SAFETY_K[args.mode]
    suggested_baseline -= safety * stats.stdev(means)
    suggested_baseline = max(0.0, suggested_baseline)

    # 4. Trailing bias: derived from observed mean(BP) vs RT
    bp_means = [float(r.get('bp_mm') or 0.0) for r in rows
                if r.get('zone','')=='MID']
    rt_means = [float(r.get('rt_mm') or 0.0) for r in rows
                if r.get('zone','')=='MID']
    if bp_means and rt_means:
        observed_offset = stats.mean(bp_means) - stats.mean(rt_means)
        # If arm sits advance-of-target, raise bias to push it back.
        bias_delta = max(0.0, observed_offset / 7.8)   # threshold ≈ 7.8 mm
        bias_delta = min(0.4, bias_delta)              # cap suggested change
    else:
        bias_delta = 0.0

    # 5. Emit config.ini patch
    cp = configparser.ConfigParser()
    cp['nosf'] = {
        '_comment': '# generated by nosf_analyze.py',
        'baseline_rate_sps_suggestion': f'{suggested_baseline:.0f}',
        'sync_trailing_bias_frac_delta': f'{bias_delta:.3f}',
    }
    if args.feedforward:
        for (feat, v), s in sorted(summary.items()):
            cp['nosf'][f'ff_{feat}_v{v}'] = f"{s['mean']:.0f}"
    with open(args.out, 'w') as fh:
        fh.write('# nosf_analyze.py output\n')
        cp.write(fh)

if __name__ == '__main__':
    main()
```

**Workflow**

```
$ python3 scripts/gcode_marker.py print.gcode --output print.tuned.gcode
$ # print print.tuned.gcode while running:
$ python3 scripts/nosf_logger.py --port /dev/ttyACM0 --out run1.csv
$ python3 scripts/nosf_analyze.py --in run1.csv --out cfg.patch.ini --mode safe
$ # review cfg.patch.ini, manually merge into config.ini, then:
$ python3 scripts/gen_config.py
$ ninja -C build_local && ./scripts/flash_nosf.sh
```

Iterate. After 3–5 print cycles a steady state emerges and the patch
file's deltas converge to noise, signalling tune is complete.

**Stretch (deferred to 2.7.4-b)**: lightgbm regressor on
`(feature, v_fil, layer_h, width)` → `optimal_baseline_sps`. Embed the
fitted decision-tree leaves as a small lookup table compiled into
`tune.h`. Out of scope for first release — flat OLS first, validate, then
fancier model.

**Acceptance**
- Three soak runs on the same hardware/file produce config patches whose suggested `baseline_rate_sps` differ by less than 5 %.
- Patch suggestions never raise `baseline_rate_sps` above the observed maximum extrusion rate (safety check).
- Patch suggestions never drop `baseline_rate_sps` below 50 % of `extruder_est_sps` mean.

---

### Sub-phase 2.7.5 — Optional Firmware PID (deferred default-OFF)

**Goal.** Add proportional and derivative terms to the existing
`sync_reserve_integral_mm` block, completing a true PID. Default Kp = Kd =
0 so behaviour is identical to 2.5/2.6.

**Code (sync.c)** — replaces the existing 2.5 integral block at
`sync.c:964-984`:

```c
/* Phase 2.7.5: full PID over reserve_error.  Default Kp=Kd=0 → degenerate
 * to existing 2.5 integral-only behaviour. */
static float g_pid_prev_error_mm = 0.0f;
static uint32_t g_pid_prev_ms = 0;

float reserve_error_mm = bp_eff - raw_target;       /* signed: + = advance side */
float dt_s = (float)SYNC_TICK_MS / 1000.0f;

/* Integral term (existing) */
bool integral_active = (s == BUF_MID)
    && (SYNC_RESERVE_INTEGRAL_GAIN > 0.0f)
    && (g_buf_signal.confidence >= 0.7f);
if (integral_active) {
    sync_reserve_integral_mm -= SYNC_RESERVE_INTEGRAL_GAIN * reserve_error_mm * dt_s;
    sync_reserve_integral_mm = clamp_f(sync_reserve_integral_mm,
        -SYNC_RESERVE_INTEGRAL_CLAMP_MM, +SYNC_RESERVE_INTEGRAL_CLAMP_MM);
}

/* New: Proportional and Derivative on measurement (not error) — avoids
 * derivative kick when setpoint shifts via TRAIL_BIAS_FRAC SET. */
float p_mm = -SYNC_PID_KP * reserve_error_mm;       /* mm of target bias */
float d_mm = 0.0f;
if (g_pid_prev_ms != 0 && SYNC_PID_KD > 0.0f) {
    float d_pos = bp_eff - g_pid_prev_error_mm;     /* derivative on measurement */
    d_mm = -SYNC_PID_KD * d_pos / dt_s;
}
g_pid_prev_error_mm = bp_eff;
g_pid_prev_ms = now_ms;

float effective_target = raw_target + sync_reserve_integral_mm + p_mm + d_mm;
```

**Tunables**

| Key | Range | Default | Purpose |
|---|---|---|---|
| `sync_pid_kp` | 0.0–0.5 | **0.0** | P gain on reserve_error_mm. |
| `sync_pid_kd` | 0.0–0.1 | **0.0** | D gain on `bp_eff` derivative. |

Persisted; settings bump shared with 2.7.0 (or later increment if shipped
separately).

**Status** new tail fields `KP:` / `KD:` for current values × 1000 as int.

**Acceptance**
- `Kp = Kd = 0`: numerically identical `effective_target` as 2.5 baseline (parity).
- Bench A/B with `Kp = 0.1, Kd = 0.005`: reserve_err RMSE reduced ≥ 10 % vs Phase 2.6.x baseline; no oscillation visible in `BP:` plot.
- §4.5 parity gate maintained at default values.

**Decision gate.** Sub-phase 2.7.5 is conditional. If 2.7.0 + 2.7.1 +
2.7.2 + 2.7.4-driven baseline tune already meet all release acceptance
criteria, **skip 2.7.5**. PID adds two tunables; if simpler bias + creep
+ σ-blend already meets spec, do not ship the complexity.

---

### Algorithm Choice Summary

**Hybrid: firmware PID-when-needed + host-side regression. No online ML.
No RL.**

- Stabilisation (μs latency, real-time): firmware. Existing I-term + new
  trailing-bias setpoint + creep + σ-blend is sufficient to bound steady-
  state error. Optional Kp/Kd available if A/B evidence demands.
- Anticipation (per-feature feedforward, ms latency): firmware reads
  `g_marker_tag` → optional `feedforward_table.h` lookup (deferred to
  2.7.4-b). The marker contract is in place from 2.7.3 onwards.
- Parameter selection (offline, batch): Pi runs `nosf_analyze.py`
  ordinary-least-squares regression over collected CSVs → emits
  `config.ini` patch the operator reviews and merges. Sample-efficient
  (3–5 print runs), interpretable, trivially safe (just baseline shift).

Why not RL: sparse reward, long episodes, hours of plant time per gradient
step, no rollback if a policy update causes a print failure. Why not
online supervised ML: same data needed as offline OLS, with extra failure
modes (online drift, evaluation-during-training).

The mathematics of the chosen control law (worst case, 2.7.5 enabled):

```
e_k          = bp_eff_k − raw_target_k                     (signed mm)
P            = −Kp · e_k
I            = clamp(I_{k−1} − Ki · e_k · dt, ±I_max)      (frozen on pin)
D            = −Kd · (bp_eff_k − bp_eff_{k−1}) / dt        (on measurement)
target_eff_k = raw_target + I + P + D
target_sps_k = sync_apply_scaling(base_sps, target_eff_k, bp_eff_k)
target_sps_k += g_mid_creep_sps                            (additive, capped)
```

`raw_target` is a function of `SYNC_RESERVE_PCT` and the new
`SYNC_TRAILING_BIAS_FRAC`, both runtime-tunable.

---

### Documentation Sync (mandatory per repo rule #6)

Every sub-phase ships with the corresponding doc updates in the same
commit:

**`MANUAL.md`**
- Add `SET:` / `GET:` rows for every new tunable (`TRAIL_BIAS_FRAC`,
  `MID_CREEP_TIMEOUT_MS`, `MID_CREEP_RATE_SPS_PER_S`, `MID_CREEP_CAP_FRAC`,
  `BUF_VARIANCE_BLEND_FRAC`, `BUF_VARIANCE_BLEND_REF_MM`, `SYNC_PID_KP`,
  `SYNC_PID_KD`).
- Add the new `MARK:<tag>` command with example.
- Document new status fields: `TB:`, `MC:`, `VB:`, `BPV:`, `MK:`, `KP:`, `KD:`.
- Document new events: `EV:SYNC,MID_CREEP_CAP`.
- Add a "Trailing-Bias Tuning Quickstart" section (8–12 lines) summarising
  the recommended SET sequence for slow-extrusion soak workflow.

**`BEHAVIOR.md`**
- New section "Trailing-Bias Setpoint and Mid-Creep" describing the
  control law from §"Algorithm Choice Summary" above.
- New section "Variance-Aware Position" describing the σ → blend → `BPV:`
  pipeline; clarifies relationship with Phase 2.6 confidence-bias (which
  is now gated off when blend is active).
- Update "Velocity estimator" section to note the σ surface is now
  consumed by both the integral gate (2.5) and the blend (2.7.2).
- Update "Buffered reserve target" to describe `SYNC_TRAILING_BIAS_FRAC`
  as an additive shift.

**`KLIPPER.md`**
- New "G-code Tuning Marker Workflow" section (~30 lines) covering:
  preprocessing with `gcode_marker.py`, the `[gcode_macro NOSF_MARK]`
  example, the `nosf_logger.py` invocation, log file rotation guidance.
- Cross-reference with `MANUAL.md` for the firmware `MARK:` command.

**`CONTEXT.md`**
- Note the new file-statics in `sync.c` (`g_mid_creep_sps`,
  `g_pid_prev_error_mm`).
- Note the new globals (`g_marker_tag`, `g_marker_seq`).
- Settings version table updated to 47u.

**`config.ini.example`**
- New "Phase 2.7" comment block with all new tunables, default values,
  and one-line guidance per key (matches existing Phase 2.5 / 2.6 block
  style).

**`CHANGELOG`-equivalent (commit-body convention)** — every sub-phase
commit body includes a bullet list of doc files touched. Reviewers verify
that any code-side rename appears in the matching doc.

**`README.md`** — one-paragraph mention of `gcode_marker.py` and
`nosf_logger.py` in the "Tools" section.

---

### Automated Tuning Workflow (operator-facing, end-to-end)

The pipeline below is the user-facing contract for 2.7.3 + 2.7.4 combined.
It is reproducible across machines and produces a deterministic
`config.ini` patch each run.

**One-time setup (per host)**

```bash
# Install host deps
sudo apt install -y python3-pyserial python3-pandas
pip3 install --user lightgbm scikit-learn      # optional, only for 2.7.4-b

# Install scripts
ln -s ~/nosf/scripts/nosf_logger.py   /usr/local/bin/nosf_logger
ln -s ~/nosf/scripts/nosf_analyze.py  /usr/local/bin/nosf_analyze
ln -s ~/nosf/scripts/gcode_marker.py  /usr/local/bin/nosf_mark_gcode

# Klipper config: register the macros from the KLIPPER.md snippet above.
sudo systemctl restart klipper
```

**Per-print loop**

```bash
# 1. Slice as normal in your slicer; export to print.gcode.
# 2. Preprocess with marker injection.
nosf_mark_gcode print.gcode --output print.tuned.gcode --every-layer

# 3. Start logging in parallel with the print.
mkdir -p ~/nosf-logs
RUN=$(date +%Y%m%d-%H%M%S)
nosf_logger --port /dev/ttyACM0 --out ~/nosf-logs/run-${RUN}.csv &
LOGGER_PID=$!

# 4. Print print.tuned.gcode via Klipper; logger captures CSV in real time.

# 5. After print finishes:
kill $LOGGER_PID

# 6. Analyse.
nosf_analyze \
    --in ~/nosf-logs/run-${RUN}.csv \
    --out ~/nosf-logs/patch-${RUN}.ini \
    --mode safe

# 7. Review the patch (every line is human-readable):
cat ~/nosf-logs/patch-${RUN}.ini

# 8. If accepted, merge into config.ini manually, regenerate, reflash:
$EDITOR config.ini   # apply the suggested deltas
python3 scripts/gen_config.py
ninja -C build_local
./scripts/flash_nosf.sh
```

**Soak-validation loop** (after a tune is accepted)

```bash
# Repeat steps 1–6 above with --mode safe.
# If three consecutive runs produce nosf_analyze patches whose suggested
# deltas are below the noise floor (|baseline_rate_sps_suggestion - current|
# < 30 sps and |sync_trailing_bias_frac_delta| < 0.02), the tune is
# considered converged and may be committed to the repo's config.ini.
```

**Failure-mode handling**

- *Logger desyncs from firmware*: nosf_logger.py uses `time.monotonic()`
  for its row timestamps, not firmware ticks; clock drift between host
  and firmware is bounded by the 100 ms STATUS poll cadence and is
  acceptable for offline regression.
- *Klipper M118 not echoing on the same TTY*: the `--moonraker-url` flag
  (deferred to 2.7.3-b) subscribes to gcode_response events directly via
  websocket, removing the dependency on TTY echo configuration.
- *Marker injection breaking print*: gcode_marker.py emits only `M118`
  comments; if a slicer's post-processor rewrites them, fall back to
  emitting `; NOSF_TUNE:...` plain-comment markers and have nosf_logger
  scan TTY for the underlying gcode_response stream.

---

### Step-by-step Milestones (checklist)

```
[ ] 2.7.0 trailing-bias setpoint shift          sync.c, settings_store.c, tune.h, config.ini  → bump 47u
[ ]   docs sync: MANUAL.md, BEHAVIOR.md, config.ini.example
[ ] 2.7.1 mid-zone creep                        sync.c, settings_store.c, tune.h, config.ini
[ ]   docs sync: MANUAL.md, BEHAVIOR.md, config.ini.example
[ ] 2.7.2 variance-aware position blend         sync.c, settings_store.c, tune.h, config.ini
[ ]   docs sync: MANUAL.md, BEHAVIOR.md
[ ] 2.7.3a firmware MARK: command               protocol.c, controller_shared.[ch], sync.c
[ ] 2.7.3b re-add scripts/gcode_marker.py       restored from 272c2d4 with --every-layer flag
[ ] 2.7.3c new scripts/nosf_logger.py           CSV capture, async serial reader
[ ]   docs sync: MANUAL.md (MARK:), KLIPPER.md (workflow), README.md (tools)
[ ] 2.7.4 new scripts/nosf_analyze.py           offline regression, config.ini patch emitter
[ ]   docs sync: KLIPPER.md (workflow), README.md
[ ] 2.7.5 optional firmware PID (gated OFF)     sync.c, settings_store.c, tune.h, config.ini
[ ]   docs sync: MANUAL.md, BEHAVIOR.md, config.ini.example
[ ] 4.5 parity gate run on 2.7.0 + 2.7.1 + 2.7.2 + 2.7.5 default-OFF
[ ] long-run soak (≥ 30 min, slow extrusion) with all features ON
[ ] commit + push per repo rule #3 after each milestone
```

Each milestone is a single PR (one commit per sub-phase). All sub-phases
are revertible by reverting their commit. Settings version 47u is the
only destructive step — call it out in commit body and `MANUAL.md`.

---

### Phase 2.7 Acceptance (release gate)

A 30-minute slow-extrusion soak (target: sustained 300–600 mm/min) on
representative hardware must satisfy all of:

- *Trailing-side residency.* Mean `BPV:` (post-blend position) ≤ −0.4 ×
  `buf_threshold_mm()` for the final 10 minutes of the run. (Equivalent to
  ≥ 60 % of half-travel toward trailing.)
- *Wall-seek confirmation.* `BPN` increments at least once per 5 minutes
  during steady extrusion (proves the system finds and re-anchors at the
  trailing wall periodically).
- *No advance-pin clusters.* `APX` peak ≤ 2 within any 60-s window.
- *No new auto-stops.* Zero occurrences of `EV:SYNC,ADV_DWELL_STOP`,
  `EV:SYNC,AUTO_STOP`, `EV:BUF,EST_FALLBACK`.
- *Estimator health.* Mean `EC:` ≥ 70; minimum (excluding boot) ≥ 30.
- *Reserve variance.* `stddev(RE)` ≤ 1.10 × Phase 2.6 baseline.
- *Tuner convergence.* Three consecutive `nosf_analyze.py` runs on
  separate prints produce suggested-delta values within noise floor (see
  "Soak-validation loop" above).

Default-OFF parity (Phase 2.6 baseline equivalence) **must** hold when
all 2.7 tunables are at their default values. The §4.5 parity gate is the
release blocker.

---

### Phase 2.7 Open Questions (deferred)

- **Q-2.7-A.** Should `gcode_marker.py` emit Klipper `SET_GCODE_VARIABLE`
  calls in addition to `M118` for tighter integration with Klipper macros
  that consume slicer feature names? Decide after first end-to-end soak.
- **Q-2.7-B.** Should the embedded feedforward table (2.7.4-b) be a flat
  lookup or a polynomial fit? Lookup is simpler; polynomial is smaller in
  flash. Decide after first OLS fit shows whether per-feature variance
  warrants nonlinearity.
- **Q-2.7-C.** Online learning vs offline regression long-term: revisit
  after Phase 2.7 ships and a dataset of ≥ 100 print runs is available.
  Until then, offline only.
- **Q-2.7-D.** Should `MID_CREEP_RATE_SPS_PER_S` adapt to recent
  advance-pin density (i.e. throttle creep when `APX` is high)? Possible
  follow-up; not in 2.7.1.
- **Q-2.7-E.** Should `BUF_VARIANCE_BLEND_FRAC` autotune from observed
  σ-vs-pin-event covariance? Defer — operator-set in 2.7.2.

---

### Phase 3 — Generic Analog Drop-In Compatibility Layer (deferred validation, PSF-ready)

> **Deferred validation phase.** Per **D1**, no PSF-specific electrical
> assumptions are encoded. This phase is firmware-side only and ships in
> simulation/bench-ready form; on-printer validation waits for real hardware.
> The maintainer's gate is endstop-mode parity (§4.5), which must already be
> green before this phase begins.

**Goal:** Make any proportional buffer sensor (PSF-class or otherwise) usable as a first-class source — calibration, auto-zero, fault detection — with **no changes** to `sync_tick()`.

**Pre-flight (read-only)**
- The PSF reference (https://github.com/kashine6/Proportional-Sync-Feedback-Sensor) is treated as **one possible target** for the generic interface, not a hardcoded contract. Re-read the sensor repo only when hardware lands. Until then, parameterization stays generic via `BUF_NEUTRAL`/`BUF_RANGE`/`BUF_INVERT`/`BUF_THR`/`BUF_ANALOG_ALPHA` (already present).

**Modules touched**
- `firmware/src/buf_source_analog.c` — extend with calibration helpers (no new file needed).
- `firmware/src/protocol.c` — new commands: `BUF_CAL:NEUTRAL`, `BUF_CAL:RANGE`, `BUF_CAL:RESET`. These set `BUF_NEUTRAL`/`BUF_RANGE` from the current ADC reading. **No reload-follow code touched** (D5).
- `firmware/src/settings_store.c` — per **D3**, no version bump if calibration only writes existing analog fields (`buf_neutral`, `buf_range`). Bump is required only if optional `buf_analog_min_norm`/`buf_analog_max_norm` fields are added (see Data Model).
- `firmware/include/config.h` — confirm `PIN_BUF_ANALOG` is mapped (already is at line 34).
- `MANUAL.md`, `KLIPPER.md` — sensor wiring + calibration procedure.

**Data model**
- Default: **no `settings_t` change** (D3 — reuse existing analog fields).
- Optional, behind a `BUF_CAL_AUTO` toggle (default OFF):
  - `buf_analog_min_norm`, `buf_analog_max_norm` — observed extrema during calibration runs, used by the analog adapter to refine `BUF_RANGE` automatically. Adding these triggers the version bump (44u → 45u). Maintainer to decide if/when to land them — keep them out of v1 of this phase.

**Algorithm changes (analog adapter only)**
- Slew-rate fault: if `|pos_norm[t] − pos_norm[t-1]| > 0.5` for two consecutive samples while `confidence == 1.0`, emit `EV:BUF,SLEW_FAULT`, drop confidence to 0.0 for 250 ms.
- Open-load fault: if ADC reads stuck within ±2 LSB of rail (0 or 4095) for > 500 ms, drop confidence to 0.0 and set `signal.fault = true`. `sync_tick()` reads confidence 0.0 → behaves like a stale signal, which already disables wall-time guards and prefers conservative rates.
- Calibration command flow:
  1. User commands `SET BUF_SENSOR:1`, then `BUF_CAL:NEUTRAL` while arm rests at center.
  2. User commands `BUF_CAL:RANGE:ADV` at full advance, then `BUF_CAL:RANGE:TRL` at full trailing. Range is set to `min(|adv-neutral|, |neutral-trl|) × 1.05`.
  3. Persisted via existing `SAVE` flow.

**Tunables**
| Key | Default | Purpose |
|---|---|---|
| `analog_wall_min_confidence` | 0.6 | Minimum confidence for analog wall-time guards to engage. |
| `analog_slew_fault_thresh` | 0.5 | Norm-units per sync tick. |

**Status / protocol impact**
- Per **D7**, only additive (tail-appended) changes — no existing field is altered.
- New events: `EV:BUF,SLEW_FAULT`, `EV:BUF,OPEN_LOAD`, `EV:BUF,CAL_OK`.
- New commands: `BUF_CAL:NEUTRAL`, `BUF_CAL:RANGE:ADV`, `BUF_CAL:RANGE:TRL`, `BUF_CAL:RESET`.
- Status `CF:` already in Phase 2 surfaces real-time confidence.
- **Reload-follow telemetry only (D5):** add a low-confidence warning event `EV:RELOAD,LOW_CF` when `kind == ANALOG && confidence < analog_wall_min_confidence` while `tc_state == TC_RELOAD_FOLLOW`. Behavior is unchanged — only the warning is emitted.

**Risks**
- PSF electrical/mechanical contract is unverified at planning time. Adding a hardware abstraction layer here would over-engineer for unknown silicon. Keep the analog adapter parameterized by the existing `BUF_*` knobs; refine after first real-hardware run.
- Calibration commands are new; they must reject if `sync_enabled` to avoid moving the arm under control.

**Acceptance**
- With endstop hardware connected (default): `BUF_SENSOR_TYPE=0`, behavior identical to Phase 2 (endstop parity gate, §4.5, holds).
- Bench rig with potentiometer simulating a generic proportional sensor: calibration → reserve hold within ±0.05 norm of target across a 30 s ramped extruder load; slew/open-load events fire when pin disconnected.
- All existing flows unaffected when `BUF_SENSOR_TYPE=0`.
- **Real-hardware validation deferred** until PSF (or equivalent) lands. No on-printer claim is made about PSF performance from this phase alone.

---

### Phase 4 — Validation and Rollout

**Goal:** Convert the new headroom into confidence; document; ship.

**Activities**
1. **Bench validation matrix** (see §5).
2. **Regression checklist** (see §6).
3. **Documentation sync** — `MANUAL.md`, `BEHAVIOR.md`, `KLIPPER.md`, `config.ini.example`.
4. **Observability soak** — run Phase-1-instrumented firmware for at least one full long print, log `STATUS` once per second, review `AD:`, `RE:`, `EA:` distributions before declaring victory.
5. **Rollback plan** — every phase is a single-revert. The settings version bump in Phase 1 is the only destructive step; document the user-facing settings reset.

**Acceptance**
- All phases' acceptance criteria continue to pass.
- One real long print on user hardware completes without `EV:SYNC,ADV_DWELL_STOP` or any `EV:BUF,*FAULT` events.
- No user-visible status field has changed semantics relative to Phase 0 baseline (all changes are additive).

---

### 4.5 Endstop-Mode Parity Gate (release-blocking)

This is the explicit pass/fail gate the maintainer requires before any analog
policy change is enabled. Phases 1 and 2 must satisfy **all** of the
following with `BUF_SENSOR_TYPE=0` (production default), or the phase does
not merge:

**P1. Bit-for-bit field parity (status snapshot)**
- For a fixed scripted scenario (boot → load → 5-min feed → reload → unload), capture `STATUS` at 1 Hz on a Phase 0 baseline build and on the candidate build.
- Required: every existing field (`LN`, `TC`, `L1T`, `L2T`, `I1`, `O1`, `I2`, `O2`, `TH`, `YS`, `BUF`, `MM`, `BL`, `BP`, `SM`, `BI`, `AP`, `CU`, `RELOAD`, `EST`, `RE`, `DP`, `PR`, `AV`, `SC`, `SA`, `GC`, `TP`, `TS`, `PW`, `RS`, `SS`) matches within tolerance: integers exact, floats within ±1 LSB at the documented precision. New tail fields are ignored by the diff.

**P2. Event-stream parity**
- Same scenario: every emitted `EV:` line on the baseline must appear on the candidate, in the same order, with the same payload, **except** for new additive events (`EV:SYNC,ADV_DWELL_STOP`, etc.) which must not fire in this scripted scenario.

**P3. Estimator parity**
- `EST` series RMSE between baseline and candidate over the 5-min feed window: ≤ 1.0 mm/min.
- `BP` series RMSE: ≤ 0.10 mm.
- `RE` series RMSE: ≤ 0.10 mm.

**P4. Timing parity**
- Per-tick `buf_sensor_tick + sync_tick` budget on RP2040: ≤ 110% of baseline (measured by GPIO-toggle scope or `time_us_64()` instrumented build).
- `SYNC_TICK_MS` cadence unchanged.

**P5. Flow parity**
- `preload`, `LO`, `FL`, `UL`, `UM`, `MV`, `TC` (MMU), `RELOAD` (WAIT_Y → APPROACH → FOLLOW → LOADED) — all complete with the same step counts and event sequence as the baseline.

**P6. Persistence parity**
- `SAVE` → power-cycle → `LOAD` round-trip preserves all settings on both baseline and candidate. Settings version bump (44u in Phase 1, if applicable) wipes cleanly with `EV:SETTINGS,VERSION_MISMATCH` (or current equivalent) and reloads defaults.

**P7. No analog policy active during gate**
- `sync_overshoot_mid_extend = 0` (D6 default).
- `BUF_SENSOR_TYPE = 0` enforced for the gate run.
- Reload-follow code path identical to baseline (D5).
- `sync_reserve_integral_gain = 0.0` enforced for the gate run (Phase 2.5 default; ensures parity with Phase 0/1/2 baseline).

If P1–P7 all hold, the phase is releasable. If any fails, the regression must
be fixed before merge — do not paper over with adapter-side compensation.

---

## 5. Test Plan Matrix

| Scenario | Endstop mode | Analog mode (sim) | Analog mode (real) | Pass criteria |
|---|---|---|---|---|
| Cold boot, mid-buffer | Phase 0+ | Phase 2+ | Phase 3+ | `SK:E`/`SK:A` correct, `BUF:MID` settles within 5 s |
| Cold boot, advance pinned | Phase 0+ | Phase 2+ | Phase 3+ | `BUF_STAB:DONE`, no spurious `ADV_DWELL_STOP` |
| Cold boot, trailing pinned | Phase 0+ | Phase 2+ | Phase 3+ | Negative-sync stabilize fires after `POST_PRINT_STAB_DELAY_MS` |
| Long continuous feed (≥30 min) | Phase 0+ | Phase 2+ | Phase 3+ | `AD:` peak < 1500 ms, `RE:` band centered near `RT:`, no auto-stop. Phase 2.5+ with `sync_reserve_integral_gain > 0`: also no monotonic advance drift in final third; `EC:` mean ≥ 70 |
| Low-flow tail | Phase 0+ | Phase 2+ | Phase 3+ | Trailing recovery damps without auto-stop, baseline tracks |
| Toolchange (MMU) | Phase 0+ | Phase 2+ | Phase 3+ | Identical event sequence vs Phase 0 |
| RELOAD approach + follow | Phase 0+ | Phase 2+ | Phase 3+ | `extruder_est_sps`-driven follow rates unchanged at the same buffer state |
| Startup transient (advance → mid) | Phase 1+ | Phase 2+ | Phase 3+ | Refill assist visible in `SC:` log; no extruder stall |
| Forced advance dwell (extruder offline) | Phase 1+ | Phase 2+ | Phase 3+ | `EV:SYNC,ADV_DWELL_STOP` exactly once, sync disables cleanly |
| Forced trailing dwell (clog sim) | Phase 0+ | Phase 2+ | Phase 3+ | Existing `AUTO_STOP` fires at `SYNC_AUTO_STOP_MS` |
| Hard trailing wall | Phase 0+ | n/a | Phase 3+ | Wall-time guard fires identically (endstop); confidence-gated for analog |
| Damp interaction stress (rapid neg/pos osc) | Phase 1+ | Phase 2+ | Phase 3+ | `DP:` toggles cleanly without flicker, no read-side mutation |
| Settings version migration | Phase 1+ | — | — | Old settings invalidated cleanly, defaults loaded, user notified |
| Hot-swap `BUF_SENSOR_TYPE` while idle | Phase 2+ | Phase 2+ | Phase 3+ | Accepted only when idle; `ER:BUSY` otherwise |
| Analog calibration flow | — | Phase 3+ | Phase 3+ | NEUTRAL/RANGE persisted, `EV:BUF,CAL_OK` emitted |
| 3000 mm long-run trailing-bias soak | Phase 2.5+ | Phase 2.5+ | Phase 3+ | Final-third `BP:` mean within +0.3 mm of middle-third mean; `AD:` peak < 1500 ms; `stddev(RE)` ≤ 1.05× baseline; no `ADV_DWELL_STOP` |
| Forced low-confidence MID dwell (sim long mid-zone with synthetic step jitter) | Phase 2.5+ | Phase 2.5+ | Phase 3+ | `EV:BUF,EST_LOW_CF` fires; `RC:` shows integration freeze; `RI:` does not drift while frozen |
| Estimator fault injection (force sigma > cap) | Phase 2.5+ | Phase 2.5+ | Phase 3+ | `EV:BUF,EST_FALLBACK` fires exactly once; integral resets to 0; controller continues on last-known zone |

---

## 6. Regression Checklist

**Behavior parity (must remain green every phase)**
- Preload: `IN` rising on idle lane triggers autopreload exactly once (`main.c::autopreload_tick`).
- Load (`FL`, `LO`): same step counts, same `EV:LOAD:*` events.
- Unload (`UL`, `UM`): same retract distances, same OUT/IN clearing semantics.
- Toolchange MMU: `TC:` event order unchanged (`UNLOAD_*` → `SWAP` → `LOAD_*`).
- RELOAD: `WAIT_Y → APPROACH → FOLLOW → LOADED` event sequence and timeouts unchanged.
- Sync auto-start on `BUF_ADVANCE`, auto-stop on trailing dwell — both still time-bounded.
- Persistence: `SAVE/LOAD/RESET` round-trips all listed fields; version mismatch wipes cleanly.
- Protocol: every documented command and status field still functions; new fields are additive only.

**Performance / loop timing**
- Main loop period unchanged (sleep is `100 µs` at `main.c:451`). Adapters add < 5 µs/tick — verify with a one-shot timestamp counter around `buf_sensor_tick`.
- USB CDC throughput: status string growth (Phase 0) must not exceed 480 bytes per dump.
- ADC sampling rate (analog mode): unchanged; still 4 samples averaged per tick at `SYNC_TICK_MS = 20 ms`.

**Phase 2.5 gates**
- `sync_reserve_integral_gain = 0.0` ships as default — verify with fresh `LOAD` produces a 0.0 value.
- Enabling the integral term requires explicit `SET sync_reserve_integral_gain:<value>`; `RC:` reflects the runtime gain scalar.
- Rollback path is a single `SET sync_reserve_integral_gain:0` — no rebuild required.
- Settings version bump (44u → 45u, or 43u → 44u) documented in commit body alongside Phase 1's bump if the two ship together.
- `EC:` and `ES:` populated on every status dump in endstop mode; `EV:BUF,EST_FALLBACK` only ever fires on real fault, never on cold start (boot-time sigma is at cap but the event is gated on a transition from low-to-zero).

**Documentation sync**
- `MANUAL.md` — add new SET/GET keys, new status fields, new events.
- `BEHAVIOR.md` — replace "if BUF_SENSOR_TYPE == 0" prose with adapter-agnostic description; document confidence model and advance-dwell guard.
- `KLIPPER.md` — analog calibration procedure (Phase 3).
- `config.ini` and `config.ini.example` — new tunables with comments.
- `CONTEXT.md` — add `buf_signal_t` structure and adapter selection invariant.
- `CHANGELOG`-equivalent — note settings version bump in Phase 1 commit body.

---

## 7. Open Questions

The original seven open questions are **resolved** by the Decision Addendum
(§0). They are retained here as a traceability table so a future reviewer can
see what was decided and why.

| # | Original question | Resolution |
|---|---|---|
| 1 | PSF sensor electrical contract | **D1** — treat as unknown; generic adapter only. Confirm later when hardware lands. |
| 2 | Advance-dwell hard stop default | **D2** — 6000 ms (start delay 400 ms). |
| 3 | Settings version bump tolerance | **D3** — bump only when persisted struct fields are added; Phase 1 bumps to 44u for the two new fields, Phase 3 stays on 44u unless optional auto-cal fields are added. |
| 4 | Hot-swap of `BUF_SENSOR_TYPE` | **D4** — only when sync/TC/cutter/stabilization are all idle. |
| 5 | Confidence in `TC_RELOAD_FOLLOW` | **D5** — telemetry only this iteration; no control-law change. |
| 6 | `SYNC_OVERSHOOT_PCT` extension into `BUF_MID` | **D6** — feature-flagged, default OFF; A/B before enabling. |
| 7 | Status string back-compat | **D7** — additive-at-tail only; no reorder/rename/semantic change. |
| 8 | Long-run trailing centering drift | **D2.5-A** — low-gain integral centering with anti-windup, default OFF (`sync_reserve_integral_gain = 0.0`); opt-in via SET. Clamp at 0.6 mm so it cannot dominate the proportional term. |
| 9 | Mid-zone estimator confidence model | **D2.5-B** — physics-based sigma growth replaces wall-clock decay. Fault triggers explicit `EST_FALLBACK` event; controller falls back to last-known zone. Reload-follow control law unchanged (D5). |

**Remaining (deferred to hardware-arrival)**

- **R1** PSF on-bench verification — voltage range, polarity, impedance, mechanical neutral position, ADC noise floor. Block on hardware.
- **R2** Auto-calibration storage — should `buf_analog_min_norm`/`buf_analog_max_norm` land in `settings_t` (45u bump) or stay session-only? Decide after first PSF bench session.
- **R3** Confidence-driven reload-follow degradation (D5 future-work) — once PSF data is available, evaluate whether a conservative speed cap under low confidence is warranted in `TC_RELOAD_FOLLOW`. Out of scope for this iteration.
- **R4** Optimal non-zero value for `sync_reserve_integral_gain` — needs 30-minute+ soak runs on representative hardware to characterize. Until then ship as 0.0; bench data drives any default change in a follow-up.
- **R5** Auto-recalibration from rolling `pos_mm_residual` mean — surfaced in observability in Phase 2.5 but not auto-acted-on. Decide after one print season's worth of residual logs.
