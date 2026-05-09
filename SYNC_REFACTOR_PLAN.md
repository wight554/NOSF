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

Adds a transition-residual drift observer that measures `g_buf_pos − switch_pos_mm`
at `MID → ADVANCE` crossings (the ground-truth moment that matches the observed
advance-side drift), accumulates residuals into a slow EWMA (`BPD`), and
optionally applies a bounded correction (`bp_eff = g_buf_pos −
scaled_clamp(BPD, ±BUF_DRIFT_CLAMP)`) to all controller-side reads of the
virtual position. Correction ramps from the first explicit-enable sample to full
strength at `BUF_DRIFT_MIN_SMP`. Default-OFF (`BUF_DRIFT_THR_MM = 0.0`).

Layered on top: a rolling advance-pin density counter (`APX`) with warn-only
`EV:SYNC,ADV_RISK_HIGH` (rate-limited 1/30 s). Boost knob declared but not
implemented; ship as documentation placeholder.

**New status fields (additive tail per D7):** `BPR`, `BPD`, `BPN`, `APX`, `RDC`

**New events:** `EV:SYNC,ADV_RISK_HIGH`, `EV:BUF,DRIFT_RESET`

**New tunables (SET/GET):**

| Key | Default | Persisted | Description |
|---|---|---|---|
| `BUF_DRIFT_TAU_MS` | 60000 | yes | EWMA time constant (ms) |
| `BUF_DRIFT_MIN_SMP` | 3 | yes | Samples before full correction |
| `BUF_DRIFT_THR_MM` | 0.0 | yes | Apply threshold; 0=OFF |
| `BUF_DRIFT_CLAMP` | 2.0 | yes | Max correction magnitude (mm, runtime max 8.0) |
| `BUF_DRIFT_MIN_CF` | 0.5 | yes | Min confidence to apply |
| `ADV_RISK_WINDOW` | 60000 | runtime-only | Pin window (ms) |
| `ADV_RISK_THR` | 4 | runtime-only | Pin count threshold |

**Operator soak procedure:**
1. Run ≥1 print; observe `BPD` and `BPN` in STATUS logs.
2. If `|BPD|` converges to ≥ 0.3 mm consistently: `SET BUF_DRIFT_THR_MM:0.5`.
3. If `BPD ≈ 0` after ≥5 transitions: drift is not the root cause; re-evaluate.

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
