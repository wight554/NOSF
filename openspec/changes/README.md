# OpenSpec Changes

Use this directory for active, spec-driven work before implementation.

Suggested shape:

```text
openspec/changes/<change-id>/
  proposal.md  # what and why
  design.md    # how it works, invariants, risks
  tasks.md     # implementation and validation checklist
```

After the change ships, keep only durable design decisions and validation
summaries under `openspec/design/`; implementation scratch can stay in `TASK.md`
or disappear with the branch.

