---
id: summarize-status
description: Produce a one-paragraph pipeline health summary.
inputs: []
output_contract:
  - One paragraph (3-5 sentences) covering sprints completed, velocity trend, open impediments, budget status
  - Plain prose, no markdown headings
  - Ends with `VERDICT: STATUS_SUMMARIZED`
requires:
  edit: false
  write: false
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *"]
  bash_deny: []
applicable_agents: [scrum-master]
---

# Skill: summarize-status

Read `progress.json` and `docs/impediments.md`. Produce a one-paragraph
summary covering:

- How many sprints completed
- Current velocity trend (rising / steady / falling)
- Open impediments count
- Budget status (if cost_tracking present)

Plain prose. 3-5 sentences. No markdown headings.

End with `VERDICT: STATUS_SUMMARIZED`.
