---
id: evaluate-tradeoff
description: Compare 2-3 alternative approaches with pros/cons and a single recommendation.
inputs:
  - name: alternatives
    type: list[string]
    description: 2-3 candidate approaches
  - name: criteria
    type: list[string]
    description: Dimensions to compare (cost, latency, complexity, team familiarity, etc.)
output_contract:
  - Markdown table of alternatives vs. criteria
  - One-paragraph recommendation with justification
  - Ends with `VERDICT: TRADEOFF_EVALUATED`
requires:
  edit: false
  write: false
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *", "rg *"]
  bash_deny: []
applicable_agents: [architect]
---

# Skill: evaluate-tradeoff

Compare candidate approaches against the named criteria and produce a single
recommendation. Be honest about the costs of the choice you recommend.

## Output format

```
## Comparison

| Criterion | Alt A | Alt B | Alt C |
| --- | --- | --- | --- |
| <criterion 1> | <verdict> | <verdict> | <verdict> |
| <criterion 2> | <verdict> | <verdict> | <verdict> |

## Recommendation

<one paragraph: which option and why; name the costs you're accepting>

VERDICT: TRADEOFF_EVALUATED
```

## Rules

- 2-3 alternatives max. More than 3 = under-defined criteria.
- Cells in the table use concrete language ("$X/mo", "30ms p95"), not handwavy
  ("acceptable").
- Recommendation must NAME its downsides. No salesmanship.
