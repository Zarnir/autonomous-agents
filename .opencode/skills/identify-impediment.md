---
id: identify-impediment
description: Read recent execution log and progress.json; flag systemic impediments.
inputs: []
output_contract:
  - Each impediment appended to docs/impediments.md as `## IMP-NNNN: <title>` block
  - Includes Status, Identified date, Sprint, Description, Suggested mitigation
  - Ends with `VERDICT: IMPEDIMENT_IDENTIFIED` (or `VERDICT: NO_IMPEDIMENTS`)
requires:
  edit: false
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *"]
  bash_deny: []
applicable_agents: [scrum-master]
---

# Skill: identify-impediment

Read `progress.json`'s `execution_log` for the current and prior sprint. Look
for systemic patterns (not one-off events):

- Repeated story retries (3+ in a sprint)
- @check finding the same issue across multiple stories
- Cost spikes per story
- Stories blocked by external dependencies
- Production gates failing on the same gate repeatedly

For each impediment, append a block to `docs/impediments.md`:

```markdown
## IMP-NNNN: <title>
Status: open
Identified: YYYY-MM-DD
Sprint: #N
Description: <2-3 sentences>
Suggested mitigation: <one sentence>
```

If no systemic patterns, end with `VERDICT: NO_IMPEDIMENTS`. Empty findings
are valid — don't manufacture drama.

End with `VERDICT: IMPEDIMENT_IDENTIFIED` when at least one was added.
