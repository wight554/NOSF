# Task Workflow Specification

## Purpose

Capture the project workflow contract that was historically spread across
`AGENTS.md` and the former gitignored `TASK.md`. The repository no longer uses a
root `TASK.md`; OpenSpec specs, changes, design notes, and task-history archives
are the durable workflow surface.

## Requirements

### Requirement: Agents shall start by loading OpenSpec context

Agents SHALL read onboarding and relevant OpenSpec context before changing
files.

#### Scenario: A new agent session begins

- **WHEN** an agent starts work in the repository
- **THEN** it reads `AGENTS.md`
- **AND** it reads `openspec/README.md`
- **AND** it reads relevant specs under `openspec/specs/`
- **AND** it posts the configured session-start banner before implementation

### Requirement: Findings and plan shall be recorded in OpenSpec artifacts

Before touching code or durable docs for substantial work, agents SHALL record
relevant findings and a file-level plan in the matching OpenSpec change or
design artifact.

#### Scenario: A task requires repository edits

- **WHEN** the agent has completed initial research
- **THEN** it records what was read, what was learned, constraints, planned file
  changes, and risks in `openspec/changes/<change-id>/` or the relevant
  `openspec/design/` note
- **AND** implementation begins only after that plan exists

### Requirement: Completed work shall be recorded in OpenSpec

Agents SHALL update the relevant OpenSpec task list, design note, validation
summary, or task-history archive after each finished unit of durable work.

#### Scenario: A milestone or commit lands

- **WHEN** the agent completes a unit of work
- **THEN** an OpenSpec artifact records the completed step, validation run, and
  commit short SHA when applicable

### Requirement: Root TASK.md shall not be recreated

The repository SHALL NOT use repo-root `TASK.md` as an active or ignored
handoff file.

#### Scenario: An agent needs scratch or handoff notes

- **WHEN** a task creates notes that future agents need
- **THEN** those notes are written to `openspec/changes/`,
  `openspec/design/`, or `openspec/design/task-history/`
- **AND** no repo-root `TASK.md` is created

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
