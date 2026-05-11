# Task Workflow Specification

## Purpose
Workflow contract (supersedes AGENTS.md and former TASK.md) behavioral requirements.

## Requirements

### Requirement: Load Context First
Agents MUST read `AGENTS.md`, `openspec/README.md`, and relevant specs before starting work.

#### Scenario: Session Start
- **WHEN** agent starts session
- **THEN** onboarding docs are read
- **AND** session-start banner is posted

### Requirement: OpenSpec Changes
Agents SHALL record findings and a file-level plan in `openspec/changes/<id>/` before implementation.

#### Scenario: Edit Required
- **WHEN** task requires code modification
- **THEN** findings, risks, and plan recorded in change artifact

### Requirement: Record Completion
The implementer SHALL update the change task list and target spec after durable work is complete.

#### Scenario: Milestone Complete
- **WHEN** durable unit is implemented and validated
- **THEN** completed step and commit SHA are recorded

### Requirement: NO root TASK.md
Handoff and scratch notes SHALL belong in `openspec/changes/` while active.

#### Scenario: Developer Handoff
- **WHEN** task is partially complete
- **THEN** active work state is tracked in change artifact
- **AND** root TASK.md remains absent

### Requirement: Small Commits
The agent MUST commit and push small, attributed units of work promptly.

#### Scenario: Durable Unit Complete
- **WHEN** unit passes validation
- **THEN** unit is committed and pushed immediately

### Requirement: NO Local AI Config in Commits
The repository SHALL keep `.agents/`, `.claude/`, `.gemini/` etc. OUT of the commits.

#### Scenario: Accidental Config Creation
- **WHEN** AI tool creates local config directory
- **THEN** directory is excluded from commits

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
