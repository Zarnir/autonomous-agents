---
id: review-adr
description: Read recent ADRs and flag conflicts with a proposed change.
inputs:
  - name: proposed_change_description
    type: string
    description: The change being proposed (could be a code change, story, or new ADR)
output_contract:
  - Lists every ADR that the proposal contradicts
  - Lists every ADR that should be marked `superseded by` if accepted
  - Lists every ADR that's silent on a question the proposal raises
  - Ends with `VERDICT: ADR_REVIEWED`
requires:
  edit: false
  write: false
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *", "find * -name *"]
  bash_deny: []
applicable_agents: [architect]
---

# Skill: review-adr

Read every ADR under `docs/adr/` and assess a proposed change against them.

## Output format

```
## Conflicts
- ADR-NNNN: <one-line reason for conflict>

## Should supersede
- ADR-MMMM: <one-line reason>

## Silent but relevant
- ADR-PPPP: <what question it doesn't answer that this proposal raises>

VERDICT: ADR_REVIEWED
```

## Rules

- Cite ADR numbers, not just titles.
- "Silent" means the ADR could have answered the proposal's question but didn't.
- If nothing applies, say so explicitly — empty findings are valid.
