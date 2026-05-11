# Task Workflow Specification

## Purpose
Workflow contract (ex-AGENTS.md + TASK.md). NO root TASK.md.

## Requirements

### REQ: Load Context First
Agents MUST read `AGENTS.md`, `openspec/README.md`, and relevant specs before work.
- **SCEN: Session Start**: read docs -> post banner.

### REQ: OpenSpec Changes
Record findings + file-level plan in `openspec/changes/<id>/` before code.
- **SCEN: Edit Required**: research -> record learned/risks/plan in change.

### REQ: Record Completion
Update change task list + spec after durable work.
- **SCEN: Milestone Complete**: record step + validation + SHA.

### REQ: NO root TASK.md
Handoff/scratch belongs in `openspec/changes/`. Fold to specs when durable.

### REQ: Small Commits
Commit + push small attributed units promptly.
- **SCEN: Doc Fix**: validation pass -> commit + footer -> push.

### REQ: NO Local AI Config in Commits
Keep `.agents/`, `.claude/`, `.gemini/` etc. OUT of project.

## Historical Phase Ledger (Sync Refactor)
- **Phase 0-1**: Sync Foundation.
- **Phase 2.0-2.7**: PSF/Analog, Estimator, Dwell.
- **Phase 2.8**: Live Tuner (Buckets, EWMA).
- **Phase 2.9**: Cal Workflow (Observe-only, patch).
- **Phase 2.10**: Klipper Tracking (Sidecar, UDS).
- **Phase 2.11**: Bucket Locking (Hysteresis, 3-channel unlock).
- **Phase 2.12**: Analyzer Rigor (Safe mode, weighted recs).
- **Phase 2.13**: Gate Parity (Consistency reduction).
- **Phase 2.14**: Gate Semantics (FAIL/WARN, mass floor).
