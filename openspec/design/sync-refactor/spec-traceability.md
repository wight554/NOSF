# Sync Refactor Spec Traceability

This file links normalized OpenSpec requirements to the historical notes that introduced or refined them.

## Spec-to-History Map

| OpenSpec spec | Historical source notes |
|---|---|
| `sync-refactor` | Aggregate current contract distilled from all sync refactor phases |
| `sync-refactor-foundation` | `SYNC_REFACTOR_PLAN.md` |
| `live-tuner` | `SYNC_REFACTOR_PHASE_2_8.md` |
| `calibration-workflow` | `SYNC_REFACTOR_PHASE_2_9.md` |
| `klipper-motion-tracking` | `SYNC_REFACTOR_PHASE_2_10.md` |
| `bucket-locking` | `SYNC_REFACTOR_PHASE_2_11.md` |
| `analyzer-rigor` | `SYNC_REFACTOR_PHASE_2_12.md` |
| `acceptance-gate-parity` | `SYNC_REFACTOR_PHASE_2_13.md` |
| `acceptance-gate-semantics` | `SYNC_REFACTOR_PHASE_2_14.md` and follow-up gate fixes |
| `task-workflow` | `AGENTS.md`, former `TASK.md`, `openspec/design/task-history/` |

## Aggregate Sync Requirement Map

| OpenSpec requirement | Historical source notes |
|---|---|
| Firmware sync behavior remains standalone after calibration | `SYNC_REFACTOR_PLAN.md`, `SYNC_REFACTOR_PHASE_2_9.md` |
| Calibration is observe-only by default | `SYNC_REFACTOR_PHASE_2_9.md` |
| Klipper marker tracking uses sidecar and UDS motion events | `SYNC_REFACTOR_PHASE_2_10.md` |
| Bucket state is durable and migratable | `SYNC_REFACTOR_PHASE_2_9.md`, `SYNC_REFACTOR_PHASE_2_11.md` |
| Bucket lock and unlock decisions resist chatter | `SYNC_REFACTOR_PHASE_2_11.md` |
| Tuner lock noise gate is relative to learned rate | `SYNC_REFACTOR_PHASE_2_12.md` |
| Analyzer recommendations use qualifying state-aware contributors | `SYNC_REFACTOR_PHASE_2_12.md` |
| Analyzer acceptance gate compares recommendation paths | `SYNC_REFACTOR_PHASE_2_13.md` |
| Acceptance gate distinguishes FAIL from WARN | `SYNC_REFACTOR_PHASE_2_14.md` and follow-up contributor-mass gray-band fix |

## How to Use This Area

- Read `openspec/specs/sync-refactor/spec.md` first for aggregate current expected behavior.
- Read the phase-level OpenSpec spec when working on a specific subsystem or
  historical phase behavior.
- Read `tasks.md` for completed implementation groups and remaining tracking chores.
- Read historical `SYNC_REFACTOR*.md` files only when you need original
  rationale, code-level implementation prompts, or detailed validation context.
