---
id: facilitate-retro
description: Validate the @retro's output; ensure action items are concrete and ownable.
inputs:
  - name: sprint_number
    type: int
    description: Sprint that just ended
  - name: retro_path
    type: string
    description: Path to docs/sprints/NN-retro.md
output_contract:
  - Validates action items are specific and ownable
  - Validates "What went wrong" lists root causes (not symptoms)
  - Validates metrics are quoted accurately
  - Ends with `VERDICT: RETRO_FACILITATED`
requires:
  edit: false
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *"]
  bash_deny: []
applicable_agents: [scrum-master]
---

# Skill: facilitate-retro

Read the retro doc and validate:

1. **Action items**: specific and ownable? "Improve quality" = boilerplate.
   "Split STORY-payment into 3 stories before next sprint" = ownable.
2. **Root causes**: each "What went wrong" item names a root cause, not a
   symptom. "Took 3x cost" is a symptom; "STORY-X had unclear ACs so @make
   retried 3 times" is a root cause.
3. **Metrics**: numbers match what's in progress.json.

If any check fails, append a one-paragraph addendum to the retro doc.

End with `VERDICT: RETRO_FACILITATED`.
