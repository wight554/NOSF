# Design Index

Design documents here are durable engineering context. They are more stable
than `TASK.md` and more implementation-oriented than user docs such as
`MANUAL.md` or `KLIPPER.md`.

## Areas

| Area | Contents |
|---|---|
| `adr/` | Short durable decision records for policies agents should preserve. |
| `sync-refactor/` | Phased sync-control, calibration, live tuner, Klipper tracking, bucket locking, and analyzer-gate history. |
| `validation/` | Compact Pi soak and accepted-patch summaries. |

## Suggested Additions

These would make future work easier to track:

- Promote accepted phase decisions from long design documents into ADRs when
  they start guiding future work.
- Add one validation summary per meaningful Pi soak instead of pasting long
  terminal logs into `TASK.md`.
- Use `openspec/changes/` for active OpenSpec proposals before implementation.
