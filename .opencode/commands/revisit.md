---
name: revisit
description: Reopen a completed/failed/blocked story for another pass (delegates to aa-orchestrator revisit).
---

# /revisit

Reopens a terminal story and resets it to `pending`. Prior artifacts are
archived for comparison.

## Arguments

- `/revisit STORY-id` — reopen this story
- `/revisit STORY-id --reason "<why>"` — record a reason
- `/revisit STORY-id --cascade-dependents` — also reopen direct dependents

## How to execute

```bash
aa-orchestrator revisit $ARGUMENTS
```

Use `python3 "${AA_HOME:-$HOME/.local/share/autonomous-agents}/lib/orchestrator.py" revisit` if `aa-orchestrator` is not on PATH.
