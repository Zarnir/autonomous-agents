---
id: propose-adr
description: Given a design question, write a Michael-Nygard-format ADR with status proposed.
inputs:
  - name: adr_number
    type: int
    description: Next monotonic ADR number (4-digit, zero-padded)
  - name: question
    type: string
    description: The design question this ADR answers
  - name: related_story_id
    type: string
    description: Optional story this ADR was prompted by
output_contract:
  - Writes docs/adr/NNNN-<slug>.md
  - File uses Michael Nygard template (Status, Context, Decision, Consequences)
  - Status starts as `proposed`
  - Ends with `VERDICT: ADR_PROPOSED`
requires:
  edit: true
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *", "git log *"]
  bash_deny: []
applicable_agents: [architect]
---

# Skill: propose-adr

Write an ADR following the Michael Nygard template. Save it to
`docs/adr/NNNN-<slug>.md` where NNNN is the next available 4-digit number.

## Required ADR structure

```markdown
# ADR-NNNN: <Title>

Status: proposed
Date: YYYY-MM-DD

## Context
<What is the issue that motivates this decision? 2-4 paragraphs.>

## Decision
<What is the change we're proposing? Direct, declarative. ≤ 3 sentences.>

## Consequences
<Both positive and negative consequences. What becomes easier? What becomes harder?>
```

## Rules

- Always check existing ADRs first — don't duplicate.
- ADR numbers are monotonic; never reuse.
- Decision section: ≤ 3 sentences.
- Consequences MUST list both wins AND costs. If you can't name a cost,
  you haven't thought hard enough.
- End with `VERDICT: ADR_PROPOSED` on its own line.
