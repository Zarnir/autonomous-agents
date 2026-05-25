---
id: diagnose-cascade
description: Investigate why many stories are blocked at once.
inputs:
  - name: blocked_story_ids
    type: list[string]
    description: All stories in `blocked` state
output_contract:
  - Diagnosis paragraph identifying the root failure
  - Recommendation: REOPEN of the root story (cascade unblocks dependents)
  - Appended to the RFC file with header `## Watcher diagnosis (<timestamp>)`
  - Ends with `VERDICT: WATCHER_DIAGNOSED`
requires:
  edit: false
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *"]
  bash_deny: []
applicable_agents: [watcher]
---

# Skill: diagnose-cascade

Multiple stories are in `blocked` state. Walk the depends_on graph to find
the single root story whose failure cascaded.

## Process

1. Read each blocked story's `depends_on`.
2. Find the depends_on tree's root (the story that has no blocked predecessor).
3. That root is the diagnosis target. Recommend `REOPEN` on it; dependents
   will unblock automatically when it completes.

## Output (append to RFC file)

```markdown
## Watcher diagnosis (<timestamp>)

**Signal:** cascade
**Severity:** <medium | high | critical>
**Diagnosis:** Root cause: <STORY-id>. <2-3 sentences explaining the cascade.>
**Recommendation:** REOPEN <STORY-id>
**Detail:** <one paragraph naming the dependent stories that will unblock>

VERDICT: WATCHER_DIAGNOSED
```
