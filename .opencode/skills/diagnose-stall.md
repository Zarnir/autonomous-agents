---
id: diagnose-stall
description: Investigate why a story is stuck in_progress.
inputs:
  - name: story_id
    type: string
    description: Story stuck in_progress
  - name: age_seconds
    type: int
    description: How long the story has been in_progress
output_contract:
  - Diagnosis paragraph identifying the most likely cause
  - Recommendation line (REOPEN | REFINE | EDIT_SCOPE | ESCALATE | NO_ACTION)
  - Appended to the RFC file with header `## Watcher diagnosis (<timestamp>)`
  - Ends with `VERDICT: WATCHER_DIAGNOSED`
requires:
  edit: false
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *", "git log *"]
  bash_deny: []
applicable_agents: [watcher]
---

# Skill: diagnose-stall

A story is stuck `in_progress`. Read `progress.json` + execution_log + the
story's artifacts to identify the most likely cause.

## Process

1. Read execution_log entries for this story.
2. Check artifacts: test_files populated? implementation_files populated?
3. Check retry markers.
4. Pick the most likely cause:
   - Retried 3+ times → recommend `REOPEN`
   - High cost but no retries → recommend `REFINE`
   - Stuck without progress → recommend `ESCALATE`

## Output (append to RFC file)

```markdown
## Watcher diagnosis (<timestamp>)

**Signal:** stalled_story
**Severity:** <low | medium | high | critical>
**Diagnosis:** <2-3 sentences>
**Recommendation:** <REOPEN STORY-id | REFINE STORY-id | EDIT_SCOPE STORY-id | ESCALATE | NO_ACTION>
**Detail:** <one paragraph>

VERDICT: WATCHER_DIAGNOSED
```
