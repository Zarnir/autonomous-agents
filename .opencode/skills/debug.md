---
id: debug
description: Triage an unclear error; narrow down to a specific cause. Read-only.
inputs:
  - name: error_message
    type: string
    description: The error message or symptom
  - name: context
    type: string
    description: When/where the error occurs
  - name: stack_trace
    type: string
    description: Optional stack trace
output_contract:
  - One-paragraph diagnosis identifying the most likely root cause
  - Follow-up suggestion (`fix-bug` skill or further investigation)
  - Ends with `VERDICT: DEBUG_DONE`
requires:
  edit: false
  write: false
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *", "git log *", "git diff *", "git status", "find * -name *"]
  bash_deny: []
applicable_agents: [engineer, make]
---

# Skill: debug

You triage; you don't fix. Your output is a clear hypothesis about the root
cause and a suggested next action.

## Process

1. Read the error message, stack trace, and named context files.
2. Search the codebase for related symbols (via `rg`).
3. Identify the single most likely root cause.
4. Recommend a next action: `fix-bug` if cause is clear, more investigation
   otherwise.

## Output format

```
## Diagnosis
<one paragraph: most likely root cause with file:line reference if known>

## Confidence
<low | medium | high>

## Next action
<one of: fix-bug | investigate further | needs human>

VERDICT: DEBUG_DONE
```

## Rules

- Do not modify code. Read-only investigation.
- One hypothesis. If you can't narrow to one, recommend further investigation
  with the specific question that needs answering.
- Cite file:line for any code reference.
