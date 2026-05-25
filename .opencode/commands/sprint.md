---
name: sprint
description: Sprint operations (plan, start, end, status, cycle). Delegates to aa-orchestrator sprint.
---

# /sprint

The SCRUM ceremony entry point. Wraps a sprint cycle around the existing
story executor: plan a goal + 5 stories, run them, retro, groom backlog.

## Actions

- `/sprint plan` — invoke `@sprint-planner`, write `docs/sprints/NN-plan.md`. No execution.
- `/sprint start` — run the planned sprint. Halts after N stories complete or timeout.
- `/sprint end` — invoke `@retro`, write `docs/sprints/NN-retro.md`, mark sprint completed.
- `/sprint status` — show current sprint + velocity.
- `/sprint cycle` — chain plan → start → end → groom → plan-next until backlog empty.

## How to execute

```bash
aa-orchestrator sprint $ARGUMENTS
```

Fallback if `aa-orchestrator` not on PATH:

```bash
python3 "${AA_HOME:-$HOME/.local/share/autonomous-agents}/lib/orchestrator.py" sprint $ARGUMENTS
```
