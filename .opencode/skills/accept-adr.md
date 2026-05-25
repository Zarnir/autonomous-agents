---
id: accept-adr
description: Mark an ADR `accepted` after consensus.
inputs:
  - name: adr_number
    type: int
    description: ADR number to accept (e.g., 7 for ADR-0007)
output_contract:
  - Updates the ADR file Status from `proposed` to `accepted`
  - Adds an acceptance date line
  - Ends with `VERDICT: ADR_ACCEPTED`
requires:
  edit: true
  write: false
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *"]
  bash_deny: []
applicable_agents: [architect]
---

# Skill: accept-adr

Read ADR-NNNN. Verify the proposal is sound and not contradicted by other
accepted ADRs (`review-adr` skill can run first). Then edit the file:

- `Status: proposed` → `Status: accepted`
- Add line: `Accepted: YYYY-MM-DD`

End with `VERDICT: ADR_ACCEPTED`.

## Rules

- Do not accept if another accepted ADR contradicts the proposal.
- Status transitions are one-way: proposed → accepted → (optionally) superseded.
- Do not silently revise Decision or Consequences during acceptance — those
  are part of the record.
