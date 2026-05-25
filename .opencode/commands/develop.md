---
name: develop
description: Autonomous development pipeline. Delegates to lib/orchestrator.py for deterministic state-machine execution.
---

# /develop

Runs the autonomous development pipeline. Reads project markdown specs and develops the product story by story until production grade.

The orchestration logic lives in `lib/orchestrator.py` — a deterministic Python state machine. This command is a thin wrapper. **Do not interpret the pipeline yourself in prose**: shell out to the script so retry loops, convergence detection, independent test verification, and concurrency are all handled deterministically.

## Arguments

- `/develop` — full pipeline (spec parse → plan → run all stories)
- `/develop --spec docs/specs/auth.md` — restrict to one spec file
- `/develop --story STORY-id` — run a single story
- `/develop --from STORY-id` — resume execution starting at a specific story
- `/develop --dry-run` — parse and plan only, do not implement
- `/develop --force` — overwrite an existing plan (backed up to `.opencode/progress.backup.json`)

## Execution

Run the orchestrator. Use whichever invocation works in this project (try in order):

```bash
# 1. Globally-installed CLI (set up by install.sh — preferred)
aa-orchestrator develop [args...]

# 2. Explicit global path via env var
python3 "${AA_HOME:-$HOME/.local/share/autonomous-agents}/lib/orchestrator.py" develop [args...]

# 3. Project-local copy (only if user vendored lib/orchestrator.py into this repo)
python3 lib/orchestrator.py develop [args...]
```

Pick the first one that's available. Pass through every argument the user gave you. Do not invent argument values.

**Auto-continue on outer timeout:** The orchestrator exits with code 3 when it approaches its outer timeout (`OUTER_TIMEOUT_SEC`, default 480s). This means more stories remain but the current invocation ran out of time. When you see exit code 3, **immediately re-invoke `aa-orchestrator resume`** — do not ask the user. Loop until you get exit code 0, 1, or 2:

```bash
# Initial run
aa-orchestrator develop [args...]
exit_code=$?
# Auto-continue loop
while [ "$exit_code" -eq 3 ]; do
  aa-orchestrator resume
  exit_code=$?
done
```

## Configuration (env vars)

- `OPENCODE_AGENT_CMD` — command prefix used to invoke agents (default: `opencode run --agent`)
- `TEST_CMD` — independent test command override (default: auto-detected from package.json / pyproject.toml / go.mod / Cargo.toml)
- `MAX_REVIEW_CYCLES`, `MAX_TEST_RETRIES`, `MAX_MAKE_RETRIES`, `AGENT_TIMEOUT_SEC` — pipeline tunables
- `OUTER_TIMEOUT_SEC` — wall-clock budget per invocation before clean exit with code 3 (default: 480)

These can also be set in `.opencode/config.json` under the `pipeline` key. Env vars take precedence.

## Output

The script logs each phase with timestamps. On completion:

- Exit 0 → all stories completed
- Exit 1 → spec/plan error or in-progress plan detected (suggest `resume`)
- Exit 2 → some stories blocked on unmet dependencies (e.g., upstream failed)
- Exit 3 → outer timeout reached, more stories remain — **auto-invoke `aa-orchestrator resume`**

After exit, the user can inspect `.opencode/progress.json` or run `python lib/orchestrator.py status` for a summary.

## What the orchestrator handles

- Phase 1: invokes `@spec` to parse markdown specs
- Phase 2: invokes `@planner` to write `.opencode/progress.json`
- Phase 3 (per story): `@check` + `@simplify` design review with proper CONVERGENCE semantics → `@test` with criterion-coverage validation → `@make` → `@guard` scope verification → independent test re-run → `@check` + `@simplify` post-impl review → `@commit`
- Failure handling: cascades blocked status to dependent stories, increments concurrency version on every write to `.opencode/progress.json`
- Resume: re-runs tests if a story was mid-implementation when interrupted

## Notes

- Do NOT replicate the orchestration logic in this file's prose. The script is the source of truth.
- If `python` is not available, install Python 3.10+ (the script uses standard library only — no pip install needed).
