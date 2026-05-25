# Project notes for AI assistants

This project uses the autonomous-agents pipeline for spec-driven development. There is a strict contract for how specs are authored.

## When the user asks you to plan, write, or edit specs

**Before writing anything in `docs/specs/`, read these in order:**

1. `docs/specs/AUTHORING_GUIDE.md` — the canonical format
2. `docs/specs/EXAMPLE.md` — a complete working reference
3. `docs/specs/MIGRATION.md` — only if adapting legacy/SCRUM/waterfall docs

**Then:**

- Write epic files at `docs/specs/epics/NN-name.md` (e.g. `01-auth.md`)
- Use the YAML-frontmatter + structured-body format documented in the guide
- Generate stable, kebab-case IDs from titles (don't randomize)
- Make every acceptance criterion testable — concrete behaviors, not vague UX descriptions
- Declare cross-story dependencies explicitly via `depends_on:`
- List file paths in tasks even if you have to make a best guess

**After writing, run:**

```bash
aa-orchestrator validate
```

Fix every error and warning. Only then tell the user the spec is ready.

## When the user asks you to run the pipeline

Don't. Tell the user to run it themselves:

```bash
aa-orchestrator develop            # full pipeline
aa-orchestrator develop --dry-run  # plan only, no code changes
aa-orchestrator status             # current progress
aa-orchestrator resume             # continue after interruption
```

Or in OpenCode:

```
/develop
/resume
```

Planning and execution are intentionally separate phases. Execution is the user's deliberate choice — do not run it without explicit instruction.

## When the user asks about pipeline behavior

The pipeline is a deterministic state machine. For each story, it runs:

1. Parallel design review (`@check` + `@simplify`)
2. TDD test writing (`@test`) — every acceptance criterion must map to ≥1 test
3. Implementation (`@make`)
4. Scope verification (`@guard` — git diff against declared `files_to_touch`)
5. Independent test re-run by the orchestrator
6. Post-implementation review
7. Commit (`@commit`)

Stories run in dependency-ordered waves. Failed stories cascade to block their dependents. Concurrent writes to `.opencode/progress.json` use optimistic-concurrency versioning.

## Files you should not modify without explicit user instruction

- `.opencode/agents/*.md` — agent definitions (frontmatter + prompts)
- `.opencode/config.json` — pipeline configuration
- `.opencode/progress.json` — pipeline state (auto-managed)
- `lib/orchestrator.py` — orchestrator state machine (if vendored)
- `lib/spec_parser.py` — deterministic spec parser (if vendored)

## Shortcut: project layout

```
.opencode/
├── agents/         # AI agents called by the pipeline (global install)
├── commands/       # /develop and /resume slash commands
├── config.json     # pipeline tunables
└── progress.json   # state (gitignored)
docs/
└── specs/
    ├── AUTHORING_GUIDE.md   # spec format spec — read first
    ├── EXAMPLE.md           # working reference
    ├── MIGRATION.md         # legacy → canonical
    ├── index.yaml           # project metadata + epic order
    └── epics/
        ├── 01-*.md
        └── 02-*.md
```
