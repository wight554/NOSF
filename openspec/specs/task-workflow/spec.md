# Task Workflow Specification

## Purpose
Workflow contract (supersedes AGENTS.md and former TASK.md). NO root TASK.md.

## Requirements

### Requirement: Load Context First
Agents MUST read `AGENTS.md`, `openspec/README.md`, and relevant specs before work.
- **Scenario: Session Start**: Read onboarding docs -> post session-start banner.

### Requirement: OpenSpec Changes
Record findings and a file-level plan in `openspec/changes/<id>/` before implementation.
- **Scenario: Edit Required**: Research -> record learned findings, risks, and plan in a change artifact.

### Requirement: Record Completion
Update the change task list and target spec after durable work is complete.
- **Scenario: Milestone Complete**: Record completed step, validation run, and commit SHA.

### Requirement: NO root TASK.md
Handoff and scratch notes belong in `openspec/changes/` while active. Fold into specs when durable.

### Requirement: Small Commits
Commit and push small, attributed units of work promptly.
- **Scenario: Durable Unit Complete**: Validation pass -> commit with explanatory body and footer -> push.

### Requirement: NO Local AI Config in Commits
Keep `.agents/`, `.claude/`, `.gemini/` etc. OUT of the project repository.

## Historical Phase Ledger (Sync Refactor)
- **Phase 0-1**: Sync Foundation and Adapter Logic.
- **Phase 2.0-2.7**: PSF/Analog Adapter, Estimator, and Dwell Guards.
- **Phase 2.8**: Live Tuner Foundation (Buckets, EWMA).
- **Phase 2.9**: Calibration Workflow (Observe-only, patch emission).
- **Phase 2.10**: Klipper Motion Tracking (Sidecar synthesis, UDS).
- **Phase 2.11**: Bucket Locking (Hysteresis, 3-channel unlock).
- **Phase 2.12**: Analyzer Rigor (Safe mode, precision-weighted recommendations).
- **Phase 2.13**: Acceptance Gate Parity (Consistency reduction).
- **Phase 2.14**: Gate Semantics (FAIL/WARN separation, denominator floor).
