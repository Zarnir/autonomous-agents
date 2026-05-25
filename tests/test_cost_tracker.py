"""Unit tests for lib/cost_tracker.py (M3.2)."""

from __future__ import annotations

from cost_tracker import (
    DEFAULT_PRICING,
    Usage,
    accumulate,
    compute_cost,
    format_summary,
    parse_usage,
)


def test_usage_total_tokens():
    u = Usage(input_tokens=100, output_tokens=50)
    assert u.total_tokens == 150


def test_compute_cost_sonnet():
    u = Usage(input_tokens=1_000_000, output_tokens=500_000)
    cost = compute_cost(u, "claude-sonnet-4-6")
    assert abs(cost - 10.50) < 0.001


def test_compute_cost_haiku():
    u = Usage(input_tokens=1_000_000, output_tokens=200_000)
    cost = compute_cost(u, "claude-haiku-4-5-20251001")
    assert abs(cost - 2.00) < 0.001


def test_compute_cost_unknown_model_falls_back():
    u = Usage(input_tokens=1_000_000, output_tokens=0)
    cost = compute_cost(u, "claude-some-future-model")
    assert abs(cost - 3.00) < 0.001


def test_compute_cost_with_override_pricing():
    u = Usage(input_tokens=1_000_000, output_tokens=0)
    custom = {"my-model": (10.0, 50.0), "_default": (10.0, 50.0)}
    assert abs(compute_cost(u, "my-model", pricing=custom) - 10.0) < 0.001


def test_parse_usage_claude_json_full_object():
    out = '{"result": "OK", "usage": {"input_tokens": 1234, "output_tokens": 567}}'
    u = parse_usage(out, "claude")
    assert u is not None
    assert u.input_tokens == 1234
    assert u.output_tokens == 567


def test_parse_usage_claude_json_embedded():
    out = (
        "Some preamble text.\n"
        '{"usage": {"input_tokens": 100, "output_tokens": 50}}\n'
        "trailing\n"
    )
    u = parse_usage(out, "claude")
    assert u is not None
    assert u.input_tokens == 100


def test_parse_usage_returns_none_when_no_match():
    assert parse_usage("just some text", "claude") is None
    assert parse_usage("", "claude") is None


def test_parse_usage_generic_tokens_in_out():
    out = "Done.\ninput tokens: 1234\noutput tokens: 567\n"
    u = parse_usage(out, "opencode")
    assert u is not None
    assert u.input_tokens == 1234
    assert u.output_tokens == 567


def test_parse_usage_generic_handles_commas():
    out = "prompt tokens: 1,234,567\ncompletion tokens: 89,012\n"
    u = parse_usage(out, "opencode")
    assert u is not None
    assert u.input_tokens == 1234567
    assert u.output_tokens == 89012


def test_parse_usage_returns_none_for_partial_data():
    out = "input tokens: 100"
    assert parse_usage(out, "opencode") is None


def test_accumulate_initializes_state():
    state: dict = {}
    accumulate(state, "make", Usage(100, 50), 0.001)
    assert state["total_input_tokens"] == 100
    assert state["total_output_tokens"] == 50
    assert state["total_usd"] == 0.001
    assert state["by_agent"]["make"] == 0.001
    assert state["calls"] == 1


def test_accumulate_sums_multiple_calls():
    state: dict = {}
    accumulate(state, "make", Usage(100, 50), 0.001)
    accumulate(state, "check", Usage(200, 100), 0.002)
    accumulate(state, "make", Usage(50, 25), 0.0005)
    assert state["total_input_tokens"] == 350
    assert state["total_output_tokens"] == 175
    assert abs(state["total_usd"] - 0.0035) < 1e-9
    assert state["by_agent"]["make"] == 0.0015
    assert state["by_agent"]["check"] == 0.002
    assert state["calls"] == 3


def test_format_summary_zero_calls():
    assert "no agent" in format_summary({})


def test_format_summary_with_data():
    state = {"total_usd": 1.2345, "total_input_tokens": 1000,
             "total_output_tokens": 500, "calls": 5, "by_agent": {}}
    s = format_summary(state)
    assert "$1.2345" in s
    assert "1,000" in s
    assert "500" in s
    assert "5 calls" in s


def test_default_pricing_has_required_models():
    required = ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"]
    for model in required:
        assert model in DEFAULT_PRICING


def test_default_pricing_has_default_fallback():
    assert "_default" in DEFAULT_PRICING
