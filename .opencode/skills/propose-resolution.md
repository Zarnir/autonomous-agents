---
id: propose-resolution
description: Read an open RFC and propose a single-shot resolution.
inputs:
  - name: rfc_path
    type: string
    description: Path to the open docs/rfc/NNNN-<slug>.md
output_contract:
  - Diagnosis (2-3 sentences) of the root cause
  - Recommendation line in the form `Recommendation: REOPEN STORY-id | NEW STORY | EDIT_SCOPE STORY-id | NONE`
  - Detail paragraph supporting the recommendation
  - Ends with `VERDICT: RFC_RESOLVED` (or `VERDICT: NEEDS_HUMAN` if ambiguous)
requires:
  edit: true
  write: false
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *", "git log *", "git diff *"]
  bash_deny: []
applicable_agents: [architect]
---

# Skill: propose-resolution

Read the RFC file. Produce a single-shot diagnosis + proposed fix + concrete
recommended action. Append your response to the RFC file (do not overwrite).

## Output format

```
## Architect resolution (<timestamp>)

**Diagnosis:** <2-3 sentences identifying the root cause>

**Recommendation:** REOPEN STORY-xxx
(or: NEW STORY: <one-line summary>
 or: EDIT_SCOPE STORY-xxx: <what to change>
 or: NONE — false positive, reason: <...>)

**Detail:** <one paragraph supporting the recommendation>

VERDICT: RFC_RESOLVED
```

If the RFC is genuinely ambiguous and needs human review:
```
VERDICT: NEEDS_HUMAN
<one-sentence specific question for the human>
```

## Rules

- Single-shot — no debate loop. Make your best judgment and commit to it.
- Recommendation MUST start with one of: REOPEN, NEW STORY, EDIT_SCOPE, NONE,
  ESCALATE — the orchestrator parses this line.
- If the RFC is a false positive (e.g., the metric was misleading), recommend
  `NONE` with a one-line reason.
- Use `NEEDS_HUMAN` sparingly — only for genuine judgment calls (e.g.,
  product direction questions). Watchers are not the right channel for those.
