---
description: Run the autonomous development pipeline (delegates to aa-orchestrator).
---

# /develop

Runs the autonomous-agents pipeline against the specs in `docs/specs/`.

The orchestration logic lives in `lib/orchestrator.py` — a deterministic state
machine. This slash command is a thin wrapper. **Do not interpret the pipeline
yourself in prose**: shell out to `aa-orchestrator` so retry loops, convergence
detection, independent test verification, and budget tracking are handled
deterministically.

## Arguments

The user may pass any of:
- `/develop` — full pipeline (spec parse → plan → run all stories)
- `/develop --spec docs/specs/auth.md` — restrict to one spec file
- `/develop --story STORY-id` — run a single story
- `/develop --from STORY-id` — resume starting at a specific story
- `/develop --dry-run` — parse and plan only, do not implement
- `/develop --force` — overwrite an existing plan (backed up to `.opencode/progress.backup.json`)

## How to execute

Try the following in order, picking the first that succeeds:

```bash
# 1. Globally-installed CLI (set up by install.sh — preferred)
aa-orchestrator develop $ARGUMENTS

# 2. Explicit global path via env var
python3 "${AA_HOME:-$HOME/.local/share/autonomous-agents}/lib/orchestrator.py" develop $ARGUMENTS

# 3. Project-local copy (only if user vendored lib/orchestrator.py)
python3 lib/orchestrator.py develop $ARGUMENTS
```

The Claude Code runner is selected automatically when `AA_RUNNER=claude` is set
or when only `claude` is on PATH. To force it explicitly:

```bash
AA_RUNNER=claude aa-orchestrator develop $ARGUMENTS
```

## Auto-continue on outer timeout

The orchestrator exits with code 3 when it approaches `OUTER_TIMEOUT_SEC`
(default 480s). When that happens, **immediately re-invoke** `aa-orchestrator
resume` without asking the user:

```bash
aa-orchestrator develop $ARGUMENTS
exit_code=$?
while [ "$exit_code" -eq 3 ]; do
  aa-orchestrator resume
  exit_code=$?
done
```

Loop until you see exit code 0 (success), 1 (error), 2 (blocked on unmet deps),
4 (production gate failure), or 5 (budget exceeded).

## What you should report back

After the pipeline finishes, run `aa-orchestrator status` and summarize:
- Total stories complete / failed / blocked
- Total cost (if tracked) — exact USD figure from progress.json
- Any FAIL/BLOCK reasons that need user action
