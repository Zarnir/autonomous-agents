---
id: diagnose-cost-spike
description: Investigate why per-story cost is anomalously high.
inputs:
  - name: story_id
    type: string
    description: Story with the cost spike
  - name: observed_cost
    type: string
    description: Cost in USD for this story
  - name: expected_cost
    type: string
    description: Rolling-average cost in USD
output_contract:
  - Diagnosis paragraph identifying the most likely cause (scope creep, AC ambiguity, retry storm)
  - Recommendation: REFINE or EDIT_SCOPE
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

# Skill: diagnose-cost-spike

A story burned much more LLM cost than average. The usual causes:

- **Scope creep**: story is bigger than estimated → recommend `REFINE`
- **AC ambiguity**: @make retried due to unclear ACs → recommend `EDIT_SCOPE`
- **Retry storm**: tests flaky → recommend `ESCALATE`

## Process

1. Read execution_log entries for this story.
2. Count retries. Read ACs and check for ambiguity.
3. Pick the most likely cause and recommend an action.

## Output (append to RFC file)

```markdown
## Watcher diagnosis (<timestamp>)

**Signal:** cost_spike
**Severity:** <medium | high>
**Diagnosis:** <2-3 sentences identifying the cause>
**Recommendation:** <REFINE STORY-id | EDIT_SCOPE STORY-id | ESCALATE>
**Detail:** <one paragraph with the cost numbers + cause>

VERDICT: WATCHER_DIAGNOSED
```
