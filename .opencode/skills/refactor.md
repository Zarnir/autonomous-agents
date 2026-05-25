---
id: refactor
description: Simplify a function or module without changing its behavior.
inputs:
  - name: target_path
    type: string
    description: File to refactor
  - name: target_function
    type: string
    description: Optional function/class name to focus on
output_contract:
  - Diff that preserves behavior (no AC changes)
  - Test runner output showing same pass/fail set before and after
  - Ends with `VERDICT: REFACTOR_DONE`
requires:
  edit: true
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["pytest *", "pytest", "npm test *", "npm test", "go test *", "cargo test *", "ls *", "cat *", "rg *", "git diff *", "git status"]
  bash_deny: []
applicable_agents: [engineer, simplify, make]
---

# Skill: refactor

You are refactoring code. Behavior must NOT change. Tests must pass before
and after with the same set of results.

## Process

1. Run the existing tests; record the pass/fail set.
2. Make the refactor (rename, extract, simplify, inline, etc.).
3. Re-run the tests; the pass/fail set MUST match.
4. If tests don't exist, recommend `write-test` skill first — do NOT proceed.

## Output format

```
## Before
<test runner output>

## Changes
<paths changed, one-line explanation each>

## After
<test runner output — must match Before set>

VERDICT: REFACTOR_DONE
```

## Rules

- Behavior preservation is mandatory.
- No drive-by bug fixes. If you spot a bug, file it via `review-code`.
- Do not add new tests. Do not modify existing tests.
