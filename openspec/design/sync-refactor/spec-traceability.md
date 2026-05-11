# Sync Refactor Spec Traceability

This file links normalized OpenSpec requirements to the historical notes that introduced or refined them.

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

- Read `openspec/specs/sync-refactor/spec.md` first for current expected behavior.
- Read `tasks.md` for completed implementation groups and remaining tracking chores.
- Read historical `SYNC_REFACTOR*.md` files only when you need original rationale, code-level implementation prompts, or detailed validation context.
