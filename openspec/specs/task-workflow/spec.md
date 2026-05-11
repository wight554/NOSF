# Task Workflow Specification

## Purpose

Capture the project workflow contract that was historically spread across
`AGENTS.md` and the gitignored `TASK.md`. `TASK.md` remains the active scratchpad
and handoff ledger, while this spec records the durable rules agents should
follow.

## Requirements

### Requirement: Agents shall start by loading project context

Agents SHALL read onboarding and current task context before changing files.

#### Scenario: A new agent session begins

- **WHEN** an agent starts work in the repository
- **THEN** it reads `AGENTS.md`
- **AND** it reads `TASK.md` when present
- **AND** it posts the configured session-start banner before implementation

### Requirement: Findings and plan shall precede implementation

Before touching code or durable docs, agents SHALL write relevant findings and a
file-level plan into `TASK.md`.

#### Scenario: A task requires repository edits

- **WHEN** the agent has completed initial research
- **THEN** it records what was read, what was learned, constraints, planned file
  changes, and risks in `TASK.md`
- **AND** implementation begins only after that plan exists

### Requirement: Completed work shall be recorded in TASK.md

Agents SHALL update `TASK.md` after each finished unit of work so later sessions
can resume without re-deriving context.

#### Scenario: A milestone or commit lands

- **WHEN** the agent completes a unit of work
- **THEN** `TASK.md` records the completed step, validation run, and commit short
  SHA when applicable

### Requirement: Durable behavior shall be promoted into OpenSpec

`TASK.md` SHALL NOT be the only long-term source for project behavior, design
decisions, or workflow rules.

#### Scenario: A task creates a durable behavior contract

- **WHEN** a decision or behavior will matter beyond the current session
- **THEN** the relevant `openspec/specs`, `openspec/changes`, or
  `openspec/design` artifact is created or updated
- **AND** `TASK.md` links or summarizes that durable artifact

### Requirement: Commits shall be small, attributed, and pushed

Finished units of work SHALL be committed with the required generated-by footer
and pushed promptly unless the user explicitly pauses the workflow.

#### Scenario: A docs-only OpenSpec conversion is complete

- **WHEN** validation passes for the edited docs/specs
- **THEN** the agent commits the scoped changes with an explanatory body
- **AND** the commit includes the required `Generated-By` footer
- **AND** the branch is pushed

### Requirement: Local AI configuration shall stay out of project commits

Local agent configuration SHALL remain global or ignored unless the project has
explicitly decided to commit a project-owned AI artifact.

#### Scenario: Global skills are installed for agent workflows

- **WHEN** the repository working tree is committed
- **THEN** `.agents/`, `.claude/`, `.codex/`, `.gemini/`, `.agent/`,
  `.github/skills`, and lockfiles listed by project policy are not included
- **AND** project-owned OpenSpec documentation remains committed in
  `openspec/`
