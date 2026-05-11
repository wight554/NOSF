# Sync Refactor Design History

This folder contains the implementation notes and phase plans that previously
lived at the repository root as `SYNC_REFACTOR*.md`.

For current expected behavior, start with
`../../specs/sync-refactor/spec.md`. The files in this folder are historical
source notes and should not be treated as the shortest path to current truth.

## Phase Map

| File | Scope | Status |
|---|---|---|
| `SYNC_REFACTOR_PLAN.md` | Main sync hardening plan: instrumentation, trailing bias, buffer abstraction, estimator confidence, telemetry pipeline. | Historical source of truth for Phases 0-2.7. |
| `SYNC_REFACTOR_PHASE_2_8.md` | Closed-loop live tuner design. | Implemented, but live writes are no longer the default workflow. |
| `SYNC_REFACTOR_PHASE_2_9.md` | Observe-only calibration workflow, state schemas, analyzer review patches. | Canonical calibration workflow foundation. |
| `SYNC_REFACTOR_PHASE_2_10.md` | Klipper UDS motion tracking and sidecar marker replacement. | Implemented host-only marker flow. |
| `SYNC_REFACTOR_PHASE_2_11.md` | Smarter bucket lock/unlock, residual statistics, schema 4. | Implemented tuner hysteresis. |
| `SYNC_REFACTOR_PHASE_2_12.md` | Analyzer rigor and relative tuner noise gate. | Implemented recommendation hardening. |
| `SYNC_REFACTOR_PHASE_2_13.md` | Acceptance-gate parity and mature-run consistency. | Implemented gate/recommendation parity. |
| `SYNC_REFACTOR_PHASE_2_14.md` | FAIL/WARN acceptance-gate semantics. | Implemented gate semantics split. |

## Current Design Baseline

- Firmware is host-detached after reviewed defaults are flashed.
- Calibration is observe-only by default; live writes are explicit debug modes.
- Klipper sidecar + UDS motion tracking is the preferred marker path.
- Bucket state schema is 4.
- LOCKED buckets use residual-aware unlock hysteresis.
- Analyzer recommendations use state-aware, precision-weighted contributors.
- Acceptance gate compares the same recommendation path used for patch emission.
- Gate failures mean recommendation unreliability; stale current config is a
  warning when the emitted patch can correct it.

## OpenSpec Alignment

- Current contract: `openspec/specs/sync-refactor/spec.md`
- Completed task ledger: `tasks.md`
- Requirement-to-history map: `spec-traceability.md`

## Follow-Up Ideas

- Move future Pi validation captures into `openspec/design/validation/` as short
  dated summaries rather than appending long logs to `TASK.md`.
- Add ADRs for durable policies: observe-only calibration, schema migration
  registry, acceptance-gate FAIL/WARN split, and global-first AI config.
- Convert future Phase 2.x work into `openspec/changes/<id>/proposal.md`,
  `design.md`, and `tasks.md` before implementation.
