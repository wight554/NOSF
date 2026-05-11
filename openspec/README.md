# NOSF OpenSpec

This directory is the project-owned place for design history, implementation
notes, and future spec-driven changes.

Tool-specific OpenSpec/OpsX skills live in global AI config directories; do not
commit `.claude/`, `.codex/`, `.gemini/`, `.agent/`, or `.github/skills` here.

## Current Layout

| Path | Purpose |
|---|---|
| `config.yaml` | Project context used by OpenSpec-aware agents. |
| `specs/` | Current and historical-phase behavioral contracts agents should read before changing an area. |
| `design/sync-refactor/` | Long-running sync, calibration, tuner, and analyzer provenance notes. |
| `design/task-history/` | Archived long-form task ledgers promoted from the former repo-root `TASK.md`. |

## Proposed Working Model

- Put durable design decisions in `openspec/design/`.
- Put active change proposals in `openspec/changes/<change-id>/`.
- Put current expected behavior in `openspec/specs/<area>/spec.md`.
- Prefer OpenSpec-native specs over raw phase notes for agent startup context.
- Do not recreate repo-root `TASK.md`; keep substantial active work in
  `openspec/changes/` and durable history in `openspec/design/`.
- Archive long task ledgers under `openspec/design/task-history/`.
- When a change ships, archive the final design and important validation notes
  under `openspec/design/` and keep root docs focused on operator-facing usage.
