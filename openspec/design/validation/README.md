# Validation Summaries

Use this directory for compact, durable summaries of real Pi soaks and accepted
calibration patches.

Suggested filename:

```text
YYYY-MM-DD-phase-or-change-summary.md
```

Template:

```markdown
# <Validation Name>

Date: YYYY-MM-DD
Hardware: <printer / board / filament>
Code: <commit SHA>
Config: <important tunables>

## Runs

| Run | Model | Duration | CSV | State snapshot |
|---|---|---:|---|---|

## Analyzer Result

- Acceptance gate:
- Baseline:
- Bias:
- Variance blend:
- Warnings:

## Decision

What was applied, rejected, or deferred?
```

