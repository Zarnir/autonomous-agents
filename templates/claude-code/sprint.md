---
description: Sprint operations (plan, start, end, status, cycle).
---

# /sprint

The SCRUM ceremony entry point. Wraps a sprint cycle around the story executor.

## Actions

- `/sprint plan` — `@sprint-planner` writes a plan doc with goal + selected stories.
- `/sprint start` — execute the planned sprint stories.
- `/sprint end` — `@retro` writes the retrospective; sprint marked completed.
- `/sprint status` — show active sprint + velocity rolling avg.
- `/sprint cycle` — automated plan → start → end → groom loop until backlog is empty.

## How to execute

```bash
aa-orchestrator sprint $ARGUMENTS
```

For a typical run:

```bash
# One sprint at a time (manual control)
aa-orchestrator sprint plan
aa-orchestrator sprint start
aa-orchestrator sprint end

# Or fully autonomous
aa-orchestrator sprint cycle
```

After each sprint:
- `docs/sprints/NN-plan.md` — what was planned and why
- `docs/sprints/NN-retro.md` — what happened, what to improve
- `docs/sprints/NN-grooming.md` — backlog health report (advisory)
