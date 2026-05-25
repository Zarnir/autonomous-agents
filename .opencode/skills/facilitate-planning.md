---
id: facilitate-planning
description: Validate the @sprint-planner's output and flag coherence issues.
inputs:
  - name: sprint_number
    type: int
    description: Sprint being planned
  - name: backlog_snapshot
    type: string
    description: List of stories with complexities + ACs
output_contract:
  - Validates goal is a user-value statement
  - Validates selected points are within ±20% of rolling avg
  - Validates risks are concrete (not boilerplate)
  - Ends with `VERDICT: PLANNING_FACILITATED`
requires:
  edit: false
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *"]
  bash_deny: []
applicable_agents: [scrum-master]
---

# Skill: facilitate-planning

Read the @sprint-planner's plan doc (under `docs/sprints/NN-plan.md`).
Validate it against three checks:

1. **Goal**: is it a coherent user-value statement? Bad: "implement 5 stories".
   Good: "users can sign up with email and verify".
2. **Velocity**: selected points within ±20% of `velocity_rolling_avg`?
3. **Risks**: concrete (specific story IDs, specific concerns)? Or boilerplate?

If any check fails, append a one-paragraph addendum to the plan doc.

End with `VERDICT: PLANNING_FACILITATED`.
