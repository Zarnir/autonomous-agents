---
name: discover
description: Generate a SCRUM spec tree from a one-line product idea (delegates to aa-orchestrator discover).
---

# /discover

Invokes `@discover` to turn a one-line product idea into a complete
SCRUM-style spec tree. The output is validated by the deterministic parser
and retried once if validation fails.

## Arguments

- `/discover "<idea>"` — generate the spec, stop
- `/discover "<idea>" --target-dir /path` — write to a different project
- `/discover "<idea>" --then-develop` — generate, then run the pipeline

## How to execute

```bash
aa-orchestrator discover [args...]
```

Use `python3 "${AA_HOME:-$HOME/.local/share/autonomous-agents}/lib/orchestrator.py" discover` if `aa-orchestrator` is not on PATH.
