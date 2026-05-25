# autonomous-agents

**Tell it what to build. It writes the tests, writes the code, runs the tests, commits — story by story, sprint by sprint, until it's done.**

Works with [Claude Code](https://claude.com/claude-code) or [OpenCode](https://opencode.ai/). You bring the idea + the API key; it brings the planning, coding, testing, and bookkeeping.

> **Inspired by** [davidroman0O/881e98a86608475cb3c0c5f49922fb33](https://gist.github.com/davidroman0O/881e98a86608475cb3c0c5f49922fb33), hardened with state-machine safety and multi-layer review against the silent-failure modes a multi-agent code review surfaced.

---

## Table of Contents

**Get started fast**
1. [TL;DR — 5 commands, top to bottom](#tldr--5-commands-top-to-bottom)
2. [What this actually does (plain English)](#what-this-actually-does-plain-english)
3. [What you need before installing](#what-you-need-before-installing)
4. [Install](#install)
5. [Use it on a new project](#use-it-on-a-new-project)

**Run it**

6. [Two ways to run the pipeline](#two-ways-to-run-the-pipeline)
7. [All commands — quick reference](#all-commands--quick-reference)
8. [Common scenarios — copy-paste flows](#common-scenarios--copy-paste-flows)
9. [What you'll see when it runs](#what-youll-see-when-it-runs)
10. [What it produces when it's done](#what-it-produces-when-its-done)

**When things go wrong**

11. [Known gotchas](#known-gotchas)
12. [Troubleshooting checklist](#troubleshooting-checklist)
13. [Frequently asked](#frequently-asked)

**Customize & extend**

14. [Configuration](#configuration)
15. [Writing your own spec (the format)](#writing-your-own-spec-the-format)
16. [How it's built (for the curious)](#how-its-built-for-the-curious)

**Maintenance**

17. [Update](#update)
18. [Uninstall](#uninstall)
19. [License](#license)
20. [Contributing](#contributing)

---

## TL;DR — 5 commands, top to bottom

```bash
# 1. Install once on your machine
git clone <this-repo> ~/code/autonomous-agents
cd ~/code/autonomous-agents && bash install.sh

# 2. Bootstrap a new project (asks you 3 clarifying questions)
aa-orchestrator new your-project --interactive
cd your-project

# 3. Let it build the whole thing autonomously
aa-orchestrator sprint cycle
```

That's it. Step 3 will plan sprints, execute stories with TDD, write retrospectives, generate release notes, and stop when your backlog is empty (or hits 10 sprints, whichever comes first).

If you'd rather drive from inside Claude Code or OpenCode: `cd your-project && claude`, then in the chat type `/sprint cycle`. Same outcome.

---

## What this actually does (plain English)

You write a one-line product idea like *"a todo app with email login"* (or skip step 1 and write a detailed spec by hand). The system:

1. **Plans it** — splits your idea into epics → stories → tasks with acceptance criteria
2. **Reviews the plan** — two LLM reviewers (`@check` for bugs/security, `@simplify` for unnecessary complexity) must agree before any code is written
3. **Writes failing tests first** — every acceptance criterion gets at least one test (real TDD, not vibes)
4. **Implements the code** — only inside the files the spec explicitly listed
5. **Re-runs the tests independently** — never trusts the agent that wrote the code
6. **Commits** — one git commit per story, on its own branch, with a real commit hash
7. **Cleans up** — merges the branch back, deletes the worktree, moves on to the next story

After all stories in a sprint finish, it writes a retrospective, release notes, and any architecture decisions worth recording. Then it plans the next sprint and keeps going.

You can stop, inspect, edit, resume, or kill it at any point. Every step writes to disk first; nothing is lost on Ctrl-C.

### What it's NOT

- **Not bug-free in production.** 899 tests cover the orchestration logic with 100% line coverage, but they don't cover real LLM behavior, rate limits, or your specific spec
- **Not a chatbot.** It runs autonomously. You don't supervise each line — you supervise specs and outputs
- **Not parallel.** Stories run one at a time
- **Not multi-service.** One git repo, one project, one `progress.json`
- **No auto-deploy.** It commits and tags; pushing/deploying is up to you
- **No human-in-the-loop UI.** Everything is markdown files + CLI + exit codes

If your use case is "manage 50 microservices across 3 clouds with PR review queues," this isn't it. If it's "I have a product idea or a backlog and I want it built," this is it.

---

## What you need before installing

- **Python 3.10 or newer** — check with `python3 --version`
- **Git** — any recent version
- **One LLM runner** installed and on `PATH`:
  - [Claude Code](https://claude.com/claude-code) — verify with `claude --version`, OR
  - [OpenCode](https://opencode.ai/) — verify with `opencode --version`
- **An API key** for whichever runner you picked (Anthropic for Claude Code, etc.)
- **About 5 minutes** for the first project to plan + execute its first sprint (depends on spec size)

---

## Install

```bash
git clone <this-repo> ~/code/autonomous-agents
cd ~/code/autonomous-agents
bash install.sh
```

That puts:
- `~/.local/bin/aa-orchestrator` — the CLI command
- `~/.local/share/autonomous-agents/` — the Python library + templates
- `~/.config/opencode/agents/` — the 19 agent definitions

Verify:

```bash
which aa-orchestrator       # should print ~/.local/bin/aa-orchestrator
aa-orchestrator --help
aa-orchestrator setup        # one-time machine check (Python, git, runner, API key, PATH)
```

If `aa-orchestrator setup` says everything is green, you're ready.

If `aa-orchestrator` isn't on `PATH`, add `~/.local/bin` to your shell rc:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc   # or ~/.bashrc
source ~/.zshrc
```

---

## Use it on a new project

### Option 1 — One-line idea, fully autonomous

```bash
aa-orchestrator new your-project --interactive
```

It will:
1. Create the directory + run `git init` + run `init.sh` (sets up `.opencode/`, slash commands, config)
2. Ask you 3 questions: who's the primary user, what scale, any tech-stack preferences
3. Ask for your one-line product idea
4. Generate the spec from your idea
5. Validate the spec
6. Stop and let you review/edit

Then:

```bash
cd your-project

# Known gotcha — see "Known gotchas" below
# (Older installs only — current init.sh copies agents automatically)
# mkdir -p .opencode && ln -s ~/.config/opencode/agents .opencode/agents

# Run the whole thing autonomously
aa-orchestrator sprint cycle
```

### Option 2 — You write the spec by hand

```bash
mkdir your-project && cd your-project
bash ~/.local/share/autonomous-agents/init.sh --runner claude
# (Older installs only — current init.sh copies agents automatically)
# mkdir -p .opencode && ln -s ~/.config/opencode/agents .opencode/agents

# Now write your spec in docs/specs/epics/01-feature.md
# (open docs/specs/EXAMPLE.md and copy the structure)
$EDITOR docs/specs/epics/01-feature.md

# Validate it
aa-orchestrator validate

# Run
aa-orchestrator sprint cycle
```

### Option 3 — Let Claude Code / Cursor / Gemini write the spec for you

After the `init.sh` step above, open the project in your AI IDE. It'll auto-discover `CLAUDE.md` / `AGENTS.md` and know the spec format. Tell it: *"Write a spec for X under docs/specs/epics/."*

Then run `aa-orchestrator sprint cycle` like in Option 2.

---

## Two ways to run the pipeline

### A) From the terminal (most common)

```bash
aa-orchestrator sprint cycle              # autonomous — plan + execute all sprints
aa-orchestrator develop                    # single sprint only, no chaining
aa-orchestrator resume                     # continue after a stop
aa-orchestrator status                     # what's happening, cost so far
```

These are normal shell commands. They run in your terminal, write logs to stdout, and exit with a status code when done. They do NOT open Claude Code or OpenCode — they invoke the LLM runner as a headless subprocess for each agent call.

### B) From inside Claude Code or OpenCode (chat session)

You open the chat tool yourself in the project directory:

```bash
cd your-project
claude          # or: opencode
```

Then inside the chat, type a slash command:

```
/sprint cycle
```

That's exactly equivalent to `aa-orchestrator sprint cycle` in the terminal. The only difference: the chat stays open so you can ask follow-up questions ("explain what STORY-x did", "review this code") while the pipeline runs.

### The 5 available slash commands

These are installed into your project by `init.sh`:

| Slash command | Same as | What it does |
|---|---|---|
| `/sprint cycle` | `aa-orchestrator sprint cycle` | Plan + execute every sprint until backlog is empty |
| `/develop` | `aa-orchestrator develop` | One sprint, no chaining |
| `/resume` | `aa-orchestrator resume` | Continue after interruption |
| `/discover "idea"` | `aa-orchestrator discover "..."` | Generate a spec from a one-line idea |
| `/revisit STORY-X` | `aa-orchestrator revisit STORY-X` | Redo a completed/failed story |

### Quick decision: terminal or chat?

| Situation | Use |
|---|---|
| Background / unattended / CI | Terminal |
| You want to chat alongside | Chat (`claude` / `opencode` + slash) |
| Quick check (`status`, `validate`) | Terminal |
| Pair with the agent on a tricky story | Chat |

Both paths reach the same orchestrator. Pick whichever fits the moment.

---

## All commands — quick reference

Every `aa-orchestrator` command, grouped by what you're trying to do. Bookmark this.

### Setup & bootstrap (use once per machine / per project)

| Command | What it does | When to use it |
|---|---|---|
| `aa-orchestrator setup` | One-time machine check: Python, git, runner CLI, `ANTHROPIC_API_KEY`, `PATH` | After `install.sh`, before your first project. Catches "fresh-machine" problems before they cost you a pipeline run |
| `aa-orchestrator new <name> --interactive` | Make the directory, run `git init`, run `init.sh`, ask 3 clarifying questions, generate a spec | Starting a brand-new project |
| `aa-orchestrator new <name>` (no `--interactive`) | Same as above but no clarifying questions and no auto-discover | When you already have a clear idea and just want the scaffolding |

### Pre-flight checks (cheap, no LLM cost — run these before any pipeline run)

| Command | What it does | When to use it |
|---|---|---|
| `aa-orchestrator validate` | Schema-check `docs/specs/` for parse errors, dup IDs, dep cycles | After editing any spec file, before running |
| `aa-orchestrator health-check` | Verify runner CLI on PATH, config parses, agents/skills load, git state OK | Fresh machine, after editing agents/skills/config |
| `aa-orchestrator status` | Print current pipeline state, cost, open RFCs, gate failures | Anytime — especially in a second terminal while the pipeline runs |

### Spec generation

| Command | What it does | When to use it |
|---|---|---|
| `aa-orchestrator discover "<one-line idea>"` | `@discover` writes a full SCRUM spec tree under `docs/specs/epics/` | You have an idea, not a written spec |
| `aa-orchestrator discover --interactive "..."` | Same, but first asks for primary user / scale / tech-stack constraints | When you want to steer the generated spec toward a specific stack |

### Main pipeline (the autonomous bit)

| Command | What it does | When to use it |
|---|---|---|
| `aa-orchestrator sprint cycle` | **Autonomous mode.** Plan → execute → retro → release → repeat until backlog is empty | The "set it and forget it" command — 90% of users live here |
| `aa-orchestrator sprint cycle --interactive` | Same, but pauses between sprints and asks "continue? show status? stop?" | When you want autonomous-but-supervised |
| `aa-orchestrator develop` | Run one batch of stories (one sprint's worth, no chaining) | When you want to ship just the next chunk and stop |
| `aa-orchestrator develop --story STORY-X` | Run just one specific story | Testing the pipeline against a single story; rerunning one in isolation |
| `aa-orchestrator develop --dry-run` | Build the plan, don't execute | Inspect what `_try_local_planner` would produce |
| `aa-orchestrator develop --force` | Wipe old `progress.json` (backed up) and start fresh | After major spec changes when you want a clean slate |

### Sprint controls (when you want manual control)

| Command | What it does | When to use it |
|---|---|---|
| `aa-orchestrator sprint plan` | `@sprint-planner` picks next batch + writes `docs/sprints/NN-plan.md`. **Doesn't execute** | You want to review the plan before any code runs |
| `aa-orchestrator sprint start` | Execute the latest planned sprint | After reviewing the plan, run it |
| `aa-orchestrator sprint end` | Close the current sprint: `@retro` writes retrospective + `@release` writes release notes | Manually closing a sprint (rare — `sprint cycle` handles this) |
| `aa-orchestrator sprint status` | Velocity rolling avg + current sprint summary | Mid-cycle check-in |

### Recovery (when something stopped)

| Command | What it does | When to use it |
|---|---|---|
| `aa-orchestrator resume` | Pick up where the last run left off (uses `progress.json`) | After Ctrl-C, timeout (exit 3), gate failure (exit 4), budget cap (exit 5) |
| `aa-orchestrator resume --retry-failed` | Reset all `failed` stories to `pending` then resume | Stories failed transiently (rate limit, flaky network) |
| `aa-orchestrator resume --retry-blocked` | Reset all `blocked` stories to `pending` then resume | You manually fixed an upstream dep that was blocking them |
| `aa-orchestrator resume --story STORY-X` | Resume targeting just one specific story | Surgical re-run after fixing one thing |

### When something needs human attention

| Command | What it does | When to use it |
|---|---|---|
| `aa-orchestrator wizard` | Detects current state, prints the suggested next command, offers to run it | You're lost. Forgot where you left off. Confused. **Start here when in doubt** |
| `aa-orchestrator refine STORY-X` | `@architect` splits a too-large story into 2-4 smaller ones | A story keeps failing or has too many acceptance criteria |
| `aa-orchestrator revisit STORY-X --reason "..."` | Reopen a `completed` / `failed` / `blocked` story (archives prior artifacts) | Found a bug post-shipping; want to redo a story without losing history |
| `aa-orchestrator revisit STORY-X --cascade-dependents` | Same, but also reopens every story that depends on this one | When a fix invalidates everything built on top of it |
| `aa-orchestrator rfc` | Process all open RFC files under `docs/rfc/` via `@architect` | Watcher flagged anomalies (exit code 6) and you want them auto-resolved |
| `aa-orchestrator adr "<question>"` | `@architect` writes an Architecture Decision Record | Recording a design choice (Postgres vs SQLite, monolith vs services, etc.) |

### Advanced / debugging

| Command | What it does | When to use it |
|---|---|---|
| `aa-orchestrator agent <name> --skill <id> "<prompt>"` | Ad-hoc one-shot agent invocation outside the pipeline | Debugging a specific agent, testing a skill, asking an agent a one-off question |

---

## Common scenarios — copy-paste flows

### Scenario 1: Brand new project, fully autonomous

```bash
aa-orchestrator setup                             # one-time machine check
aa-orchestrator new your-project --interactive    # bootstrap + spec generation
cd your-project
# (Older installs only — current init.sh copies agents automatically)
# mkdir -p .opencode && ln -s ~/.config/opencode/agents .opencode/agents
aa-orchestrator sprint cycle                      # let it run until done
```

### Scenario 2: I have a clear PRD, want to write the spec myself

```bash
mkdir your-project && cd your-project
bash ~/.local/share/autonomous-agents/init.sh --runner claude
# (Older installs only — current init.sh copies agents automatically)
# mkdir -p .opencode && ln -s ~/.config/opencode/agents .opencode/agents

$EDITOR docs/specs/epics/01-feature.md            # write your spec
aa-orchestrator validate                          # schema check
aa-orchestrator sprint plan                       # review the plan first
$EDITOR docs/sprints/01-plan.md                   # tweak if needed
aa-orchestrator sprint start                      # execute when you're happy
```

### Scenario 3: I want to use it from inside Claude Code chat

```bash
aa-orchestrator new your-project --interactive
cd your-project
# (Older installs only — current init.sh copies agents automatically)
# mkdir -p .opencode && ln -s ~/.config/opencode/agents .opencode/agents
claude                                            # open Claude Code in this dir
```
Then in the chat:
```
/sprint cycle
```

### Scenario 4: I'm lost / forgot where I left off

```bash
cd your-project
aa-orchestrator wizard                            # tells you what to do next
```

### Scenario 5: The pipeline stopped halfway

```bash
aa-orchestrator status                            # see where + why it stopped
aa-orchestrator resume                            # continue from last phase
```

### Scenario 6: A story failed and I think I know why

```bash
aa-orchestrator status                            # see which story failed
$EDITOR docs/specs/epics/01-feature.md            # tweak the AC
aa-orchestrator revisit STORY-X --reason "AC was vague"
aa-orchestrator resume
```

### Scenario 7: A story is too big and keeps failing

```bash
aa-orchestrator refine STORY-X                    # @architect splits it
aa-orchestrator validate                          # re-validate spec
aa-orchestrator sprint cycle                      # continue
```

### Scenario 8: Monitor cost while it runs

```bash
# Terminal 1 — start the pipeline
aa-orchestrator sprint cycle

# Terminal 2 — watch progress + spend
watch -n 30 aa-orchestrator status
```

### Scenario 9: Cost ran away mid-pipeline

```bash
# Pipeline halted with exit code 5 (budget exceeded)
aa-orchestrator status                            # confirm cost vs cap
$EDITOR .opencode/config.json                     # raise pipeline.max_budget_usd
aa-orchestrator resume                            # continue
```

### Scenario 10: Reset and start over completely

```bash
aa-orchestrator develop --force                   # backs up old progress.json, restarts
```

### Scenario 11: Record a design decision

```bash
aa-orchestrator adr "Should we use Postgres or SQLite for the audit log?"
# Writes docs/adr/NNNN-postgres-vs-sqlite-audit-log.md
# Future agent runs auto-prepend accepted ADRs to every prompt
```

### Scenario 12: Found a bug after shipping — redo a story

```bash
aa-orchestrator revisit STORY-login-email --reason "missed empty-password edge case"
$EDITOR docs/specs/epics/01-auth.md               # add the missing AC
aa-orchestrator resume                            # re-runs the story with the new AC
```

### Scenario 13: One-off debugging agent call

```bash
aa-orchestrator agent engineer --skill fix-bug "the /api/login route returns 500 when password is empty"
```

### Scenario 14: Production gate failed (exit code 4)

```bash
aa-orchestrator status                            # see which gate failed
git status                                        # often "uncommitted files"
git stash || git commit -am "wip"                 # clean the tree
aa-orchestrator sprint cycle                      # continue
```

### Scenario 15: Watcher flagged an RFC (exit code 6)

```bash
ls docs/rfc/                                      # see what was flagged
cat docs/rfc/0001-*.md                            # read the issue
aa-orchestrator rfc                               # @architect proposes resolutions
# If @architect says NEEDS_HUMAN — edit the spec and resume
```

### Scenario 16: Same flow but inside the chat tool

Every terminal command has a slash equivalent for the 5 most-used ones:

```bash
cd your-project
claude
```
Then in the chat:
```
/sprint cycle           # = aa-orchestrator sprint cycle
/develop                # = aa-orchestrator develop
/resume                 # = aa-orchestrator resume
/discover "an idea"     # = aa-orchestrator discover "..."
/revisit STORY-X        # = aa-orchestrator revisit STORY-X
```

For everything else (`setup`, `new`, `wizard`, `validate`, `health-check`, `status`, `sprint plan/start/end/status`, `adr`, `refine`, `rfc`, `agent`), use the terminal.

---

## What you'll see when it runs

Per-phase timestamped log lines:

```
[2026-05-20T14:23:01Z] Phase 1: parsing spec markdown
[2026-05-20T14:23:01Z]   Found 3 epics, 12 stories
[2026-05-20T14:23:01Z] Phase 2: building execution plan
[2026-05-20T14:23:01Z]   Plan written: 12 pending stories, 3 waves

[2026-05-20T14:23:01Z] > Starting STORY-login-email: Login with email (wave 1)
[2026-05-20T14:23:55Z]     @check: PASS   @simplify: PASS
[2026-05-20T14:23:55Z]   Writing tests (@test)
[2026-05-20T14:24:32Z]   Implementing (@make), attempt 1/3
[2026-05-20T14:25:10Z]     @make self-reported: GREEN
[2026-05-20T14:25:14Z]   independent test run: npm test ... PASS
[2026-05-20T14:25:30Z]   Committing (@commit)
[2026-05-20T14:25:35Z] ✓ Completed STORY-login-email
```

In another terminal, you can watch progress + cost any time:

```bash
aa-orchestrator status
```

---

## What it produces when it's done

After a successful run, you'll have:

| File / directory | What's in it |
|---|---|
| `git log --oneline` | One commit per story, branch pattern `feat/<epic>/<story>-<slug>` |
| `docs/specs/epics/` | Your spec files (one per epic) |
| `docs/sprints/NN-plan.md` | Plan written by `@sprint-planner` for each sprint |
| `docs/sprints/NN-retro.md` | Retrospective written by `@retro` after each sprint |
| `docs/releases/v0.N.md` | Release notes written by `@release` for each sprint |
| `docs/adr/NNNN-*.md` | Architecture Decision Records (only when relevant) |
| `docs/specs/PROJECT_CONTEXT.md` | Auto-maintained summary of what each story produced |
| `.opencode/progress.json` | Full pipeline state (resumable, inspectable) |

---

## Known gotchas

### OpenCode PTY mode (now OFF by default)

OpenCode 1.15.x (and likely older builds) exit with code 1 and empty stdout when invoked through a PTY pipe, which made every agent call fail. As of M20, **`OpenCodeRunner` uses non-PTY mode (plain `subprocess.run`) by default** — no manual workaround needed.

If you're on a newer OpenCode build that handles PTY correctly and want the streaming behavior back, opt in explicitly:

```bash
# One-off:
OPENCODE_USE_PTY=true aa-orchestrator sprint cycle

# Permanent — add to ~/.zshrc or ~/.bashrc:
export OPENCODE_USE_PTY=true
```

You'll see a one-line stderr note confirming PTY mode is enabled. If you start hitting `non-zero exit (1); output_tail=(empty)` errors again, unset the env var to fall back to the safe default.

Claude Code users are unaffected — `ClaudeCodeRunner` never used PTY mode.

### "Agent definition not found: .opencode/agents/<name>.md"

**Fixed in current versions.** On the very first run after bootstrap, if you see:

```
FileNotFoundError: Agent definition not found: .opencode/agents/discover.md
```

…you're on an older install. Update:

```bash
cd ~/code/autonomous-agents     # or wherever you cloned the source
bash install.sh --update         # ships the new ClaudeCodeRunner fallback + new init.sh agent-copy
cd <your-project>
bash ~/.local/share/autonomous-agents/init.sh --force   # copies agents into .opencode/agents/
```

After this, both `--runner claude` and `--runner opencode` find agents automatically — no symlink or manual copy needed.

### "spec_parser.MalformedSpec: …"

Your `docs/specs/epics/*.md` doesn't match the canonical format. Run:

```bash
aa-orchestrator validate
```

It prints line-numbered errors. Read `docs/specs/AUTHORING_GUIDE.md` for the format rules. Or use `/discover` to regenerate the spec from your idea.

### Exit codes

| Code | Meaning | What to do |
|---|---|---|
| `0` | Success | Nothing — you're done |
| `1` | Spec/config error | Run `aa-orchestrator validate` to see line-numbered details |
| `2` | Any story finished as `failed` or `blocked` (incl. unmet deps) | `aa-orchestrator status` for the breakdown, then fix root cause + `develop --force` |
| `3` | Outer timeout (default 480s) | Just `aa-orchestrator resume` — picks up where it left off |
| `4` | Production gate failed (uncommitted files, failing tests, build broken) | Fix manually, then `develop`/`sprint cycle` again |
| `5` | Budget cap hit | Inspect `.opencode/progress.json` cost, raise `max_budget_usd`, then `resume` |
| `6` | An RFC needs human review | Open `docs/rfc/*.md` with `Status: open`, then `aa-orchestrator rfc` |

### Story keeps failing — too big?

```bash
aa-orchestrator refine STORY-X       # asks @architect to split into smaller stories
```

### Want to redo something that already shipped?

```bash
aa-orchestrator revisit STORY-X --reason "missed an edge case"
```

That archives the old artifacts and re-queues the story.

---

## Troubleshooting checklist

When something feels wrong:

```bash
aa-orchestrator status         # current state + cost + open issues
aa-orchestrator health-check   # runtime env: runner CLI, config, agents, skills, git
aa-orchestrator validate       # spec schema
aa-orchestrator wizard         # "what should I do next?" navigator
```

### Pipeline reported failures (`⚠ Pipeline finished with failures …`)

If `develop` exits with `⚠ Pipeline finished with failures (X completed, Y failed, Z blocked)`, the run actually finished with errors — pre-M20 builds called this case "✓ All stories complete." which lied. Recovery:

```bash
# 1. See exactly which stories failed and why
aa-orchestrator status

# 2. Back up the run and start clean
mv .opencode/progress.json .opencode/progress.broken.json

# 3. Re-run (root cause first — e.g. set a runner env var, fix a spec)
aa-orchestrator develop --force

# 4. Once stories actually commit, commit the bootstrap files so the
#    production gate (clean_working_tree) passes
git add .gitignore .opencode/ AGENTS.md CLAUDE.md docs/
git commit -m "chore: bootstrap autonomous-agents project layout"
```

The most common root cause has been the OpenCode PTY bug — fixed by M20's default flip. If you still see `non-zero exit (1); output_tail=(empty)` after M20, your `OPENCODE_USE_PTY` is set to a truthy value somewhere. Unset it.

`wizard` is the most useful when you're confused — it reads your project state and prints the suggested next command.

### Common situations

| Symptom | Fix |
|---|---|
| `aa-orchestrator: command not found` | Add `~/.local/bin` to `PATH` (see Install section) |
| `Agent definition not found` | `bash install.sh --update` in source, then `init.sh --force` in project |
| `@sprint-planner failed: non-zero exit (1); output_tail=(empty)` (OpenCode users) | `export OPENCODE_USE_PTY=false` and re-run |
| `progress.json is corrupt` | `cp .opencode/progress.backup.json .opencode/progress.json` |
| Pipeline ran but no commits | Run `git status` — check the working tree is clean (otherwise `@commit` blocks) |
| Costs ballooning | Edit `.opencode/config.json` → set `max_budget_usd` and lower `max_review_cycles` |
| Want to start over | `aa-orchestrator develop --force` (backs up old `progress.json`) |

---

## Frequently asked

**Q: Does it work with non-JS/Python projects?**
Yes. The orchestrator detects test runners (npm/pytest/go test/cargo test) and build commands (`npm run build`, `cargo build`, `go build`, `python -m compileall`). For other stacks, set `TEST_CMD` env var or `pipeline.test_cmd` in config.

**Q: Will it overwrite my code?**
Each story runs in its own git worktree (`.opencode/worktrees/<story-id>/`), commits there, then fast-forward merges into your main branch. If the merge can't fast-forward, the worktree is preserved for manual review.

**Q: Can I stop in the middle?**
Yes. Ctrl-C is safe. State is persisted before every transition. Resume with `aa-orchestrator resume`.

**Q: How much does it cost?**
Depends on spec size + model tier. A 10-story spec at default Sonnet settings is typically $1–5. Set `max_budget_usd` to cap it.

**Q: Can I use it for legacy/existing code?**
Yes. `bash init.sh` in the existing repo. Write specs for new features only — the orchestrator never touches files outside `files_to_touch`.

**Q: Why does it sometimes ask me to look at an RFC?**
The "watcher" detects pipeline anomalies (stalled story, cascade-of-failures, repeated retries). Instead of guessing, it writes a markdown file in `docs/rfc/` and asks `@architect` for a resolution. If `@architect` can't resolve it autonomously, you'll see exit code 6 and a request for human review.

**Q: Where do I report bugs?**
Open an issue with `aa-orchestrator status` output + the relevant log tail.

---

## Configuration

The defaults are sensible. If you want to tune:

`.opencode/config.json` (created by `init.sh`):

```json
{
  "pipeline": {
    "runner": "claude",
    "max_budget_usd": 5.0,
    "sprint_size": 5,
    "max_sprint_cycles": 10,
    "outer_timeout_sec": 480,
    "max_review_cycles": 2,
    "max_test_retries": 1,
    "max_make_retries": 2,
    "worktree_isolation": true,
    "auto_merge": true,
    "auto_tag": false,
    "auto_push_tags": false,
    "watcher_enabled": true,
    "rfc_auto_apply": true,
    "product_review_enabled": false,
    "project_context_enabled": true
  },
  "models": {
    "default": "claude-opus-4-7",
    "check": "claude-sonnet-4-6",
    "simplify": "claude-sonnet-4-6"
  }
}
```

Per-agent timeouts in the same file under `agent_timeouts: {}` (seconds). Most users never touch this — set `max_budget_usd` and forget the rest.

---

## Writing your own spec (the format)

If you skip `discover` and write the spec yourself, the format is strict. Open `docs/specs/EXAMPLE.md` after `init.sh` runs — copy the structure into `docs/specs/epics/NN-feature.md`. Each epic file looks like:

```markdown
---
id: EPIC-auth
title: User Authentication
priority: high
depends_on: []
---

Brief description of what this epic covers.

## Story: STORY-login-email
title: Login with email and password
complexity: medium
depends_on: []

As a user, I want to log in with my email so that I can access my account.

### Acceptance Criteria
- [ ] AC1: User submits email + password to /api/login
- [ ] AC2: Valid credentials return a JWT token in the response
- [ ] AC3: Invalid credentials return 401 with a clear error message

### Tasks
- [ ] TASK-handler `app/api/login/route.ts` (create)
- [ ] TASK-test `app/api/login/route.test.ts` (test)
```

Then `aa-orchestrator validate` checks the schema. Full rules in `docs/specs/AUTHORING_GUIDE.md` after bootstrap.

**Tips**: more acceptance criteria → better tests. Be specific about file paths in `files_to_touch` so `@guard` can detect out-of-scope writes.

---

## How it's built (for the curious)

Three layers:

1. **The orchestrator** (Python, deterministic, no LLM calls) — parses specs, manages state, persists progress, decides what runs next
2. **The agents** (LLM calls, one purpose each) — `@check`, `@simplify`, `@test`, `@make`, `@guard`, `@commit`, `@architect`, `@retro`, `@release`, etc.
3. **The runner adapter** — `OpenCodeRunner` and `ClaudeCodeRunner` translate orchestrator commands into actual LLM invocations with the right permissions per agent

Each agent has scoped permissions (bash allow/deny lists, file edit/write toggles). For example, `@test` can write only test files; `@make` can write only the files listed in the spec; `@guard` is read-only and audits via `git diff`.

The state machine is **resumable**. Every transition writes to `.opencode/progress.json` with `fsync`. Ctrl-C, kill -9, machine crash — `aa-orchestrator resume` picks up from the last completed phase.

### Sub-agent delegation (mid-task consultation)

Phase agents are not solo. Each one can `import` skills from `.opencode/skills/` (e.g., `@make` imports `fix-bug`, `refactor`, `debug`, `add-instrumentation`) and can **delegate mid-task** to a peer agent listed in its `consult_agents:` allow-list.

When an agent needs help (e.g., `@make` hits a design question), it ends its turn with:

```
DELEGATE_TO: @architect
QUESTION:
Should I use a queue or a list here?
END_DELEGATE
```

The orchestrator parses this marker, runs `@architect` via the normal `call_agent` path (so cost tracking flows through unchanged), splices the answer back into `@make`'s context, and re-invokes `@make`. Bounded by `pipeline.max_delegations_per_phase` (default 2). Sub-agents cannot themselves delegate — no nesting, no prompt-loop risk. Runner-agnostic: works identically on OpenCode and Claude Code.

### Testing

899 passing tests across 55 files, **100% line coverage** on every module in `lib/`.

```bash
pytest tests/                                       # full suite (~5s)
pytest tests/ --cov=lib --cov-report=term           # with coverage
```

100% is achieved via real tests where possible plus `# pragma: no cover` on truly defensive blocks (ImportError fallbacks for sibling modules, PTY race-condition handlers, etc.) — each pragma carries a one-line justification.

### Safety guarantees the orchestrator enforces

| Guarantee | How |
|---|---|
| Tests are real (not mocked) | Independent test run after `@make` claims GREEN — orchestrator runs them itself |
| Scope is enforced | `@guard` audits `git diff` against `files_to_touch`; FAIL_OUT_OF_SCOPE triggers retry |
| Every AC has a test | `validate_criterion_coverage` rejects `RED_VERIFIED` if any AC isn't mapped |
| State survives interruption | All transitions atomically written + fsynced before next phase |
| Cost cap is enforced | `MAX_BUDGET_USD` raises `AgentError` mid-call when exceeded |
| Production gates block bad sprints | Clean working tree + tests pass + build succeeds before sprint can complete |

---

## Update

```bash
cd ~/code/autonomous-agents
git pull
bash install.sh    # idempotent — re-runs safely
```

Existing projects pick up updates automatically since they reference the global install. If you changed templates/slash commands, re-run `init.sh --force` in each project.

## Uninstall

```bash
rm -f ~/.local/bin/aa-orchestrator
rm -rf ~/.local/share/autonomous-agents
rm -rf ~/.config/opencode/agents       # only if no other tool uses this dir
```

Per-project files (`.opencode/`, `docs/specs/`, etc.) stay — delete those manually if you want a clean slate.

---

## License

MIT. See `LICENSE`.

## Contributing

PRs welcome. Before submitting:

```bash
pytest tests/                                       # must pass (837/837 currently)
pytest tests/ --cov=lib --cov-report=term           # must stay at 100%
```

New features need new tests. Bug fixes need a regression test written RED-first (verify it fails against the unfixed code before the fix lands).

For deeper architecture details — the agent responsibilities, trust boundary, state machine diagram, and the silent-failure modes that shaped the safety properties — see `docs/ARCHITECTURE.md` (coming soon; until then, read `lib/orchestrator.py` top-down).
