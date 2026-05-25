"""M17.2: cost_tracker.py edge-case coverage for 100%."""

from __future__ import annotations

from cost_tracker import _parse_claude_json, parse_usage


def test_parse_claude_json_skips_invalid_json_candidate():
    assert _parse_claude_json("{invalid json with usage}") is None


def test_parse_claude_json_skips_non_dict_usage_field():
    text = '{"usage": "not-a-dict"}'
    assert _parse_claude_json(text) is None


def test_parse_claude_json_skips_non_int_tokens():
    text = '{"usage": {"input_tokens": "not-a-number", "output_tokens": 5}}'
    assert _parse_claude_json(text) is None


def test_parse_usage_claude_runner_uses_claude_parser():
    out = '{"usage": {"input_tokens": 100, "output_tokens": 50}}'
    usage = parse_usage(out, "claude")
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
