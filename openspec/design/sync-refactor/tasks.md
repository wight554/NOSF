# Sync Refactor Task Ledger

This ledger maps the historical Phase 2.x implementation notes into the current OpenSpec design baseline. The detailed source notes remain in the sibling `SYNC_REFACTOR*.md` files.

## 1. Firmware Sync Hardening and Telemetry

- [x] 1.1 Instrument sync status with additive tail fields only.
- [x] 1.2 Add trailing-bias setpoint shift tunable and documentation.
- [x] 1.3 Add mid-zone creep tunables while preserving default behavior.
- [x] 1.4 Add variance-aware position blend and BP variance reference tunables.
- [x] 1.5 Restore telemetry capture path and analyzer foundation.

## 2. Live Tuner and Observe-Only Calibration

- [x] 2.1 Implement host-side live tuner and state persistence.
- [x] 2.2 Make observe-only calibration the default workflow.
- [x] 2.3 Add schema migrations and cumulative lock criteria.
- [x] 2.4 Emit review-only analyzer/tuner patches.
- [x] 2.5 Preserve explicit live-write/debug modes behind flags.

## 3. Klipper Motion Tracking

- [x] 3.1 Generate sidecar JSON from slicer G-code.
- [x] 3.2 Subscribe to Klipper motion state over UDS.
- [x] 3.3 Match motion position to sidecar segments.
- [x] 3.4 Synthesize `on_m118`-compatible marker strings.
- [x] 3.5 Preserve legacy marker fallback paths.

## 4. Bucket Locking and State Schema

- [x] 4.1 Add residual EWMA statistics to schema 4.
- [x] 4.2 Implement chained schema migration registry.
- [x] 4.3 Add three-channel unlock detection: catastrophic, streak, drift.
- [x] 4.4 Add minimum lock dwell and relative noise-gated locking.
- [x] 4.5 Expose verbose state-info diagnostics.

## 5. Analyzer Rigor

- [x] 5.1 Add LOCKED-bucket floor and safe/aggressive/force mode semantics.
- [x] 5.2 Replace dominant-cluster baseline with precision-weighted contributors.
- [x] 5.3 Compute variance reference from BP scatter instead of baseline fields.
- [x] 5.4 Preserve mid-creep timeout as DEFAULT/current until a valid signal exists.
- [x] 5.5 Emit `[nosf_contributors]` diagnostics.

## 6. Acceptance Gate

- [x] 6.1 Share recommendation path between patch emission and gate consistency.
- [x] 6.2 Classify comparable runs and skip immature runs with diagnostics.
- [x] 6.3 Use contributor mass and raw coverage diagnostics.
- [x] 6.4 Split gate outcomes into FAIL versus WARN.
- [x] 6.5 Keep stale current config as a warning when the patch can correct it.
- [x] 6.6 Add contributor-mass gray band and analyzer glob input support.

## 7. Tracking Improvements Proposed

- [ ] 7.1 Promote durable policy decisions into ADRs under `openspec/design/adr/`.
- [ ] 7.2 Add Pi validation summaries under `openspec/design/validation/` after accepted calibration patches.
- [ ] 7.3 Use `openspec/changes/<change-id>/proposal.md`, `design.md`, and `tasks.md` for future Phase 2.x work before implementation.
- [x] 7.4 Archive the long-form root `TASK.md` into `openspec/design/task-history/`, remove root `TASK.md`, and add `project-architecture` as a validated OpenSpec spec.
