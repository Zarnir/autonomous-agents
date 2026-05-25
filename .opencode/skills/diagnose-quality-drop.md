---
id: diagnose-quality-drop
description: Investigate why @check verdicts are trending NEEDS_CHANGES.
inputs:
  - name: recent_verdicts
    type: list[string]
    description: Last N @check verdicts with story IDs
output_contract:
  - Diagnosis paragraph identifying the systemic issue
  - Recommendation: ESCALATE (quality issues usually need humans)
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

# Skill: diagnose-quality-drop

The rate of NEEDS_CHANGES verdicts has spiked. Don't try to fix it — quality
trends usually need a human looking at the actual code.

## Process

1. Read recent @check + @simplify outputs from execution_log.
2. Identify the most common finding type (security? naming? complexity?).
3. Recommend `ESCALATE` with a one-line summary of the pattern.

## Output (append to RFC file)

```markdown
## Watcher diagnosis (<timestamp>)

**Signal:** quality_drop
**Severity:** <medium | high>
**Diagnosis:** <2-3 sentences naming the recurring issue>
**Recommendation:** ESCALATE
**Detail:** <one paragraph: which stories, what finding, why a human should look>

VERDICT: WATCHER_DIAGNOSED
```

## Rules

- Quality issues = humans. The watcher diagnoses; it does not silently revisit
  stories en-masse.
