# Design Index

Design documents here are durable engineering context. They are more
implementation-oriented than user docs such as `MANUAL.md` or `KLIPPER.md`.

Current behavioral contracts belong in `openspec/specs/`; design files here
explain why the contracts exist and how they evolved.

## Areas

| Area | Contents |
|---|---|
| `adr/` | Short durable decision records for policies agents should preserve. |
| `sync-refactor/` | Phased sync-control, calibration, live tuner, Klipper tracking, bucket locking, and analyzer-gate history. |
| `task-history/` | Archived long-form task ledgers promoted from the former repo-root `TASK.md`. |
| `validation/` | Compact Pi soak and accepted-patch summaries. |

## Suggested Additions

These would make future work easier to track:

- Promote accepted phase decisions from long design documents into ADRs when
  they start guiding future work.
- Add one validation summary per meaningful Pi soak instead of keeping long
  terminal logs in ad hoc scratch files.
- Use `openspec/changes/` for active OpenSpec proposals before implementation.
