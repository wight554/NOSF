# OpenSpec Changes

Use this directory for active, spec-driven work before implementation.

Suggested shape:

```text
openspec/changes/<change-id>/
  proposal.md  # what and why
  design.md    # how it works, invariants, risks
  tasks.md     # implementation and validation checklist
```

After the change ships, update the relevant spec and remove stale planning
artifacts. Do not recreate repo-root `TASK.md`.
