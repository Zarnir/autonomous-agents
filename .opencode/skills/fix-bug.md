---
id: fix-bug
description: Given a failing test or error, identify and fix the root cause with the smallest possible diff.
inputs:
  - name: story_id
    type: string
    description: Story or task ID (optional)
  - name: failing_test
    type: string
    description: Path to the failing test (e.g., tests/test_foo.py::test_bar)
  - name: error_output
    type: string
    description: The error output / stack trace
output_contract:
  - Smallest possible diff that makes the failing test pass
  - Test re-run output proving the fix works
  - Ends with `VERDICT: FIX_BUG_DONE`
requires:
  edit: true
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["pytest *", "pytest", "npm test *", "npm test", "go test *", "cargo test *", "ls *", "cat *", "rg *", "git diff *", "git status"]
  bash_deny: []
applicable_agents: [engineer, make]
---

# Skill: fix-bug

You are fixing a single failing test. Apply the **minimum** diff that turns
red into green without changing unrelated behavior.

## Process

1. Read the failing test and the error output.
2. Read the code under test.
3. Make the smallest possible change that fixes the root cause.
4. Re-run the test to confirm GREEN.
5. Do not add unrelated changes (no drive-by refactors, no extra logging).

## Output format

```
## Diagnosis
<one-paragraph root-cause analysis>

## Fix
<paths changed and one-line explanation each>

## Verification
<test runner output showing GREEN>

VERDICT: FIX_BUG_DONE
```

## Rules

- One bug per invocation. If you find more, leave a TODO and stop.
- The diff must be minimal. A 1-line fix is better than a 10-line fix when
  both make the test pass.
- Do not modify the failing test to make it pass. Fix the code, not the test.
