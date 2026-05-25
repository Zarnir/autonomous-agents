---
id: daily-standup
description: Write a brief daily-standup-style synthesis (yesterday / today / blockers).
inputs: []
output_contract:
  - Writes docs/sprints/NN-day-N-standup.md
  - Three sections: Yesterday, Today, Blockers
  - One sentence per section (Blockers may be bulleted or "none")
  - Ends with `VERDICT: STANDUP_WRITTEN`
requires:
  edit: false
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "git log *"]
  bash_deny: []
applicable_agents: [scrum-master]
---

# Skill: daily-standup

Write a brief synthesis to `docs/sprints/NN-day-N-standup.md`:

```markdown
# Sprint N — Day N Standup

## Yesterday
<one sentence: what completed since the last standup>

## Today
<one sentence: what's in flight or queued>

## Blockers
<bullet list or "none">
```

End with `VERDICT: STANDUP_WRITTEN`.

## Rules

- One sentence per section. Brevity is the point.
- "Blockers" includes any open RFC or open impediment that affects this sprint.
- Pull facts from `progress.json` execution_log + open RFCs/impediments. Don't speculate.
