"""Unit tests for @review-product output parsing (M2.2)."""

from __future__ import annotations

from orchestrator import parse_review_product_output


def test_parse_pass_as_is():
    out = "Looks great. VERDICT: PASS_AS_IS"
    result = parse_review_product_output(out)
    assert result["verdict"] == "PASS_AS_IS"


def test_parse_pass_as_is_with_extra_whitespace():
    out = "All good.\n\nVERDICT:PASS_AS_IS\n"
    assert parse_review_product_output(out)["verdict"] == "PASS_AS_IS"


def test_parse_reopen_single_story():
    out = "Issue found. VERDICT: REOPEN STORY-login\nReason: missing error state"
    result = parse_review_product_output(out)
    assert result["verdict"] == "REOPEN"
    assert result["story_ids"] == ["STORY-login"]


def test_parse_reopen_multiple_stories():
    out = "VERDICT: REOPEN STORY-a, STORY-b, STORY-c"
    result = parse_review_product_output(out)
    assert result["verdict"] == "REOPEN"
    assert set(result["story_ids"]) == {"STORY-a", "STORY-b", "STORY-c"}


def test_parse_follow_up_stories_extracts_spec_blocks():
    out = """
Looks good but missing observability.

VERDICT: FOLLOW_UP_STORIES

```spec-block
## Story: STORY-add-logging

title: Add structured logging
complexity: small
depends_on: []

### Acceptance Criteria

- [ ] AC1: All requests produce a JSON log line

### Tasks

- [ ] TASK-x `src/logger.py` (create)
```

```spec-block
## Story: STORY-add-metrics

title: Emit Prometheus metrics
complexity: medium
depends_on: [STORY-add-logging]

### Acceptance Criteria

- [ ] AC1: A /metrics endpoint serves text/plain

### Tasks

- [ ] TASK-y `src/metrics.py` (create)
```
"""
    result = parse_review_product_output(out)
    assert result["verdict"] == "FOLLOW_UP_STORIES"
    assert len(result["story_blocks"]) == 2
    assert "STORY-add-logging" in result["story_blocks"][0]
    assert "STORY-add-metrics" in result["story_blocks"][1]


def test_parse_unknown_verdict():
    out = "I have no opinion."
    result = parse_review_product_output(out)
    assert result["verdict"] == "UNKNOWN"
    assert "raw" in result


def test_parse_pass_wins_over_reopen_when_both_strings_appear():
    out = "Initial thought was reopen but actually VERDICT: PASS_AS_IS"
    assert parse_review_product_output(out)["verdict"] == "PASS_AS_IS"
