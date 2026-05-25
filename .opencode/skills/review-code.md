---
id: review-code
description: Review a code diff against the spec; identify bugs, style, security issues. Read-only.
inputs:
  - name: story_id
    type: string
    description: Story being reviewed (e.g., STORY-login)
  - name: file_paths
    type: list[string]
    description: Files in the diff
  - name: diff_text
    type: string
    description: Unified diff
output_contract:
  - Severity-graded findings (critical / high / medium / nit)
  - file:line references for every finding
  - Ends with `VERDICT: REVIEW_DONE`
requires:
  edit: false
  write: false
  webfetch: false
  websearch: false
  bash_allow: ["git diff *", "git log *", "rg *", "cat *", "ls *"]
  bash_deny: []
applicable_agents: [engineer, architect, check, simplify, guard]
---

# Skill: review-code

You are reviewing a code diff. Your output must be **structured findings**,
not fixes. Fixes are a different skill.

## Process

1. Read each file mentioned in the diff.
2. For each issue, classify severity:
   - `critical`: security, data loss, crashes
   - `high`: bugs that affect correctness
   - `medium`: style, naming, maintainability
   - `nit`: minor preferences
3. Include `file:line` references for every finding.
4. Do NOT propose fixes inline — that's the `fix-bug` skill.

## Output format

```
## Findings

### Critical
- `src/auth.py:42` — SQL injection in login handler

### High
- `src/auth.py:88` — Race condition between token refresh and logout

### Medium
- `src/utils.py:15` — Function name `do_stuff` is unclear

### Nit
- `src/auth.py:103` — Inconsistent docstring style

VERDICT: REVIEW_DONE
```

If no issues found:
```
## Findings

(none)

VERDICT: REVIEW_DONE
```

## Rules

- One finding per line.
- File:line refs mandatory for every finding.
- Critical > High > Medium > Nit ordering.
- Do not propose fixes — only diagnose.
