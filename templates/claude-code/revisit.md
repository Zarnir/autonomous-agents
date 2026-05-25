---
description: Reopen a completed/failed/blocked story for another pass.
---

# /revisit

Reopens a terminal story (completed, failed, or blocked) and resets it to
`pending` so the pipeline runs it again. Prior artifacts are archived under
`story.artifacts.previous[]` for comparison.

## Arguments

- `/revisit STORY-id` — reopen this story with no reason given
- `/revisit STORY-id --reason "wrong UI"` — record why
- `/revisit STORY-id --cascade-dependents` — also reopen direct dependents

## How to execute

```bash
aa-orchestrator revisit $ARGUMENTS
```

After the revisit, the story is back in `pending`. Run `/develop` to re-execute,
or `/develop --story STORY-id` to run only that story.
