---
description: Generate a SCRUM spec tree from a one-line product idea, then optionally develop it.
---

# /discover

Invokes `@discover` to turn a one-line product idea into a complete SCRUM-style
spec tree (`docs/specs/epics/*.md`). The generated spec is then validated by
the deterministic parser; if validation fails, @discover is re-run once with
the errors as feedback.

## Arguments

- `/discover "<one-line product idea>"` — generate the spec, stop
- `/discover "<idea>" --target-dir /path/to/project` — write to a different project
- `/discover "<idea>" --then-develop` — generate spec, then immediately run the pipeline

## How to execute

```bash
aa-orchestrator discover $ARGUMENTS
```

If the generated spec validates cleanly, the files under `docs/specs/epics/`
are ready to be reviewed (recommended) or developed directly via `/develop`.

## Recommended flow

1. `/discover "<your product idea>"` — generates the spec
2. Open `docs/specs/epics/*.md` and `docs/specs/index.yaml`, edit if needed
3. `aa-orchestrator validate` — sanity-check after edits
4. `/develop` — run the pipeline
