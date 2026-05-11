# Architecture Decision Records

Use ADRs for durable decisions that future agents should not re-litigate.

Suggested filename:

```text
YYYY-MM-DD-short-decision.md
```

Template:

```markdown
# <Decision>

Date: YYYY-MM-DD
Status: Accepted | Superseded by <ADR>

## Context

What problem or constraint forced the decision?

## Decision

What did we choose?

## Consequences

What does this enable, prevent, or require?
```

Good ADR candidates from current history:

- Observe-only calibration is canonical; live writes are debug-only.
- Bucket state migrations use a chained registry and must preserve production DBs.
- Analyzer acceptance gate separates recommendation FAIL from stale-config WARN.
- Tool-specific AI skills are global; project-specific specs live in `openspec/`.

