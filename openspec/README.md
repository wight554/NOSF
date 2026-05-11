# NOSF OpenSpec

This directory is the project-owned place for design history, implementation
notes, and future spec-driven changes.

Tool-specific OpenSpec/OpsX skills live in global AI config directories; do not
commit `.claude/`, `.codex/`, `.gemini/`, `.agent/`, or `.github/skills` here.

## Current Layout

| Path | Purpose |
|---|---|
| `config.yaml` | Project context used by OpenSpec-aware agents. |
| `design/sync-refactor/` | Long-running sync, calibration, tuner, and analyzer design history. |

## Proposed Working Model

- Put durable design decisions in `openspec/design/`.
- Put active change proposals in `openspec/changes/<change-id>/`.
- Keep day-to-day scratch notes in `TASK.md` only until they become durable
  enough to promote into OpenSpec.
- When a change ships, archive the final design and important validation notes
  under `openspec/design/` and keep root docs focused on operator-facing usage.

