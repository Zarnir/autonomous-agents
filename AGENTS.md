# AGENTS.md — autonomous-agents source repo

This is the **source repo** for the autonomous-agents pipeline tool. It is not a target project. The "product" is a set of OpenCode agent definitions + a deterministic Python orchestrator that get installed globally and then bootstrap other projects.

## What lives where

- `lib/orchestrator.py` — deterministic state machine (~1200 lines). All pipeline phases, agent invocation, retry/convergence, and progress.json mutations. **Python 3.10+ stdlib only** — no pip, no requirements.txt, no venv.
- `lib/spec_parser.py` — regex-based markdown parser for the canonical spec format. No LLM calls.
- `.opencode/agents/*.md` — 9 OpenCode agent definitions with YAML frontmatter (`permission` allow/deny blocks control sandboxing).
- `.opencode/commands/develop.md`, `resume.md` — slash commands that are thin wrappers shelling out to `aa-orchestrator`.
- `.opencode/config.json` — reference pipeline config. Copied to target projects by `init.sh`.
- `templates/` — files that `init.sh` copies into target projects. `templates/root/AGENTS.md` and `templates/root/CLAUDE.md` are **for target projects**, not this repo.
- `docs/specs/` — spec authoring guide, example, migration playbook. Also shipped to target projects.
- `install.sh` — deploys agents to `~/.config/opencode/agents/`, orchestrator to `~/.local/share/autonomous-agents/`, CLI shim to `~/.local/bin/aa-orchestrator`.
- `init.sh` — bootstraps a target project with config, commands, spec templates, and planning-tool integration files.

## Developer commands

```bash
# Deploy changes globally (after editing agents or orchestrator)
bash install.sh --update

# Clean reinstall
bash install.sh

# Validate specs in a target project (from that project's directory)
aa-orchestrator validate

# Run the pipeline in a target project
aa-orchestrator develop --dry-run

# Check pipeline status
aa-orchestrator status
```

There is no test suite, linter, or typecheck for this repo. There is no build step.

## Key constraints

- **Python stdlib only.** Do not add pip dependencies. The orchestrator must run with `python3` alone.
- **Agent frontmatter is OpenCode-specific.** The `permission` blocks use allow/deny glob patterns understood by OpenCode's runtime. Edits here directly affect sandboxing — `@make`'s deny list prevents it from writing to `.env`, `.git`, secrets, and test paths; `@test`'s deny list prevents it from touching production code.
- **`templates/root/AGENTS.md` and `CLAUDE.md` are not for this repo.** They are templates shipped to target projects. Do not add this-repo-specific instructions there.
- **`install.sh --update` is required after changes.** Agent definitions and the orchestrator are deployed globally. Source edits are invisible to running pipelines until installed.
- **No CI, no `.gitignore` at root.** Only `.opencode/.gitignore` exists (ignores its own node_modules). There is no `.github/`, no pre-commit config, no branch protection.
- **`.opencode/node_modules/`** is for the `@opencode-ai/plugin` SDK. It's gitignored and unrelated to the orchestrator.

## Architecture notes

- The orchestrator invokes agents as subprocesses via `$OPENCODE_AGENT_CMD` (default: `opencode run --agent`). It never loads an LLM itself.
- `spec_parser.py` is imported by `orchestrator.py` via `sys.path.insert` — they live in the same directory.
- `progress.json` uses optimistic concurrency (integer `version` field incremented on every write). The orchestrator retries on conflict.
- Agent timeout, retry counts, and review cycles are configurable via `.opencode/config.json` (loaded at startup) or env vars (take precedence).
- Per-agent timeouts are configurable via `agent_timeouts` in config.json (e.g., `"make": 900, "guard": 300`).
- The pipeline uses an outer wall-clock timeout (`outer_timeout_sec`, default 480s). On timeout, the orchestrator persists state and exits with code 3. Slash commands auto-invoke `resume` on code 3, creating a chain of short-lived invocations.
- Signal handling: SIGINT/SIGTERM trigger graceful shutdown — state is persisted before exit.
- `data["status"]` transitions: `pending` → `in_progress` (at run_loop start) → `completed` or `blocked`.
- Resume automatically resets stories stuck in intermediate states (`in_progress`, `review_pass`, `test_written`) back to `pending`.
- The pipeline is sequential within a wave — no parallel story execution.
- `@spec` and `@planner` agents are legacy fallback (`--spec-llm-fallback`). The default path uses `spec_parser.py` deterministically.

## Editing agents

Agent `.md` files have two parts: YAML frontmatter (parsed by OpenCode) and a prose prompt (sent to the LLM as system instructions). Key frontmatter fields:

- `description` — used by OpenCode for agent selection/dispatch
- `mode: all` — required because the orchestrator invokes agents via `opencode run --agent <name>` (direct CLI invocation). Do NOT use `mode: subagent` — that blocks direct invocation and causes empty stdout failures.
- `permission.edit`, `permission.write` — `ask` lets the agent request; `deny` blocks entirely
- `permission.bash` — ordered glob allow/deny list; **last match wins** (so `"*": deny` at the bottom is a catch-all deny)
- `permission.webfetch`, `permission.websearch` — typically `deny` for pipeline agents
