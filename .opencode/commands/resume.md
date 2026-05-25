---
name: resume
description: Resume autonomous development from last checkpoint. Delegates to lib/orchestrator.py.
---

# /resume

Resume the autonomous development pipeline from the last checkpoint stored in `.opencode/progress.json`. The orchestration logic lives in `lib/orchestrator.py` — this command is a thin wrapper.

## When to Use

- The pipeline was interrupted (crash, manual stop, ENV_BROKEN block)
- You want to retry failed or blocked stories
- You ran `/develop --dry-run` and now want to execute

## Arguments

- `/resume` — continue from current_story_id or next pending story
- `/resume --retry-failed` — re-queue all failed stories and continue
- `/resume --retry-blocked` — re-queue all blocked stories and continue
- `/resume --story STORY-id` — jump to a specific story (must not be in a terminal state)

## Execution

Run the orchestrator. Use whichever invocation works in this project (try in order):

```bash
# 1. Globally-installed CLI (preferred)
aa-orchestrator resume [args...]

# 2. Explicit global path
python3 "${AA_HOME:-$HOME/.local/share/autonomous-agents}/lib/orchestrator.py" resume [args...]

# 3. Project-local copy
python3 lib/orchestrator.py resume [args...]
```

Pick the first one that's available. Pass through every argument the user gave you.

**Auto-continue on outer timeout:** The orchestrator exits with code 3 when it approaches its outer timeout (`OUTER_TIMEOUT_SEC`, default 480s). When you see exit code 3, **immediately re-invoke `aa-orchestrator resume`** — do not ask the user. Loop until you get exit code 0, 1, or 2:

```bash
aa-orchestrator resume [args...]
exit_code=$?
while [ "$exit_code" -eq 3 ]; do
  aa-orchestrator resume
  exit_code=$?
done
```

## Re-verification on resume

The orchestrator re-runs the test suite when resuming a story whose status is `implemented` (interrupted between implementation and post-impl review). If tests fail post-resume, the story is reset to `review_pass` and re-implemented. This prevents resuming on top of stale or partially-applied changes.

Stories left in intermediate states (`in_progress`, `review_pass`, `test_written`) after a crash are automatically reset to `pending` on resume, so they re-run from their last checkpoint. No data is lost — completed phases are preserved in git and `progress.json` artifacts.

## Status check

To see current state without running anything:

```bash
python lib/orchestrator.py status
```

## Notes

- `/resume` is safe to run multiple times — orchestrator reads state before acting
- If the spec files changed since the plan was created, run `/develop --force` to re-parse
