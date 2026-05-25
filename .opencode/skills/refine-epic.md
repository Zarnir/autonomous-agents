---
id: refine-epic
description: Split a large epic/story into 2-4 smaller stories that collectively cover the same acceptance criteria.
inputs:
  - name: story_id
    type: string
    description: Story to refine (e.g., STORY-payment-flow)
  - name: current_tasks
    type: list[string]
    description: Existing tasks in the story
output_contract:
  - 2-4 new story blocks written back into docs/specs/epics/ in canonical format
  - Original story marked with `superseded_by: [STORY-a, STORY-b, ...]`
  - Union of new stories' ACs covers all original ACs
  - Ends with `VERDICT: EPIC_REFINED` (or `VERDICT: REFINEMENT_REJECTED` with a one-sentence reason)
requires:
  edit: true
  write: true
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *", "find * -name *"]
  bash_deny: []
applicable_agents: [architect]
---

# Skill: refine-epic

Split a too-large story into smaller, independently-shippable stories. The
union of the new stories' acceptance criteria MUST cover the original
story's acceptance criteria.

## Process

1. Read the original story's spec (under `docs/specs/epics/`).
2. Identify natural seams (different files / different concerns).
3. Draft 2-4 new stories with:
   - Unique IDs (`STORY-<original>-<n>` is a fine convention)
   - Subset of the original ACs (collectively covering all)
   - 1-4 tasks each, concrete file paths
4. Write the new stories back into the same epic file.
5. Mark the original story with `superseded_by: [STORY-a, STORY-b, ...]`
   and leave its acceptance criteria intact (for audit).

## Verdict

- `VERDICT: EPIC_REFINED` — split applied; spec validates.
- `VERDICT: REFINEMENT_REJECTED` — split would lose AC coverage or break
  dependencies. Output a one-sentence reason.

## Rules

- AC coverage is mandatory. If you can't cover all ACs with the split, reject.
- New stories must respect existing dependencies (no cycles).
- Total task count after split ≈ sum of original tasks; don't add scope.
