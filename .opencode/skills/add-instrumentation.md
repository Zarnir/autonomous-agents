---
id: add-instrumentation
description: Add minimum useful logging/metrics/tracing to investigate behavior. Mark for removal once investigation completes.
inputs:
  - name: target_path
    type: string
    description: Where to add instrumentation
  - name: what_to_measure
    type: string
    description: What signal we need
output_contract:
  - Smallest diff that adds the named instrumentation
  - TODO comment marking each addition for removal after investigation
  - Ends with `VERDICT: ADD_INSTRUMENTATION_DONE`
requires:
  edit: true
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *", "git diff *", "git status"]
  bash_deny: []
applicable_agents: [engineer, make]
---

# Skill: add-instrumentation

You add the minimum logging/metrics needed to investigate a question. Every
addition must be marked for removal after the investigation.

## Process

1. Read the target file.
2. Add the smallest useful log/metric/trace.
3. Mark each addition with a `# TODO(remove-after-investigation): ...` comment.

## Output format

```
## What I added
<file:line — what signal>

## How to remove
<one-line guidance for cleanup>

VERDICT: ADD_INSTRUMENTATION_DONE
```

## Rules

- Never add logs in hot paths without explicit user approval.
- Never log secrets or PII.
- Mark every addition with a removal TODO.
