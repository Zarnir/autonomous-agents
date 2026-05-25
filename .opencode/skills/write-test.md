---
id: write-test
description: Add a single missing test for an existing function or behavior.
inputs:
  - name: target_path
    type: string
    description: File whose function needs a test
  - name: scenario
    type: string
    description: One sentence describing the scenario to test
output_contract:
  - One new test added in the appropriate test file
  - The test runs and reflects the named scenario
  - Ends with `VERDICT: WRITE_TEST_DONE`
requires:
  edit: true
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["pytest *", "pytest", "npm test *", "npm test", "go test *", "cargo test *", "ls *", "cat *", "rg *"]
  bash_deny: []
applicable_agents: [engineer, test]
---

# Skill: write-test

Add **one** test for one scenario. Don't sprawl. Don't refactor while you're
there.

## Process

1. Identify the existing test file (or create one if the language pattern
   requires).
2. Write a single test that exercises the named scenario.
3. Run the test; it must pass (or fail if testing a bug — but be explicit).

## Output format

```
## Scenario
<one-line description>

## Test added
<file:line where the test lives>

## Run
<test runner output>

VERDICT: WRITE_TEST_DONE
```

## Rules

- One test per invocation. Multiple scenarios = multiple invocations.
- Don't refactor the function under test. Just test it.
- If the framework requires a new test file, that's fine — create it.
