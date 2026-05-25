---
description: Resume an interrupted autonomous-agents run (delegates to aa-orchestrator resume).
---

# /resume

Continues a previously-interrupted `develop` run. Loads
`.opencode/progress.json`, resets any stories stuck in intermediate states, and
picks up where the pipeline left off.

## Arguments

- `/resume` — continue from current state
- `/resume --retry-failed` — reset `failed` stories back to `pending` and try again
- `/resume --retry-blocked` — reset `blocked` stories (dependents of failures) and try again
- `/resume --story STORY-id` — run a single story

## How to execute

```bash
aa-orchestrator resume $ARGUMENTS
exit_code=$?
while [ "$exit_code" -eq 3 ]; do
  aa-orchestrator resume
  exit_code=$?
done
```

If `aa-orchestrator` is not on PATH, fall back to:

```bash
python3 "${AA_HOME:-$HOME/.local/share/autonomous-agents}/lib/orchestrator.py" resume $ARGUMENTS
```

The Claude Code runner is selected automatically when `AA_RUNNER=claude` is set
or when only `claude` is on PATH.

## What you should report back

After resume finishes, run `aa-orchestrator status` and summarize what
changed: which stories newly completed, which are still blocked or failed, and
the current total cost if tracked.
