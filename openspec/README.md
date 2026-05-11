# NOSF OpenSpec

This directory is the project-owned place for current specs and future
spec-driven changes.

Tool-specific OpenSpec/OpsX skills live in global AI config directories; do not
commit `.claude/`, `.codex/`, `.gemini/`, `.agent/`, or `.github/skills` here.

## Current Layout

| Path | Purpose |
|---|---|
| `config.yaml` | Project context used by OpenSpec-aware agents. |
| `specs/` | Current and historical-phase behavioral contracts agents should read before changing an area. |

## Proposed Working Model

- Put active change proposals in `openspec/changes/<change-id>/`.
- Put current expected behavior in `openspec/specs/<area>/spec.md`.
- Prefer OpenSpec-native specs over raw phase notes for agent startup context.
- Do not recreate repo-root `TASK.md`; keep substantial active work in
  `openspec/changes/`.
- Do not keep migrated historical phase/task archives in-tree; use git history
  for old prose and implementation prompts.
- When a change ships, update the relevant spec and keep root docs focused on
  operator-facing usage.
