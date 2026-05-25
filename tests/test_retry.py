"""Unit tests for lib/retry.py (M11.1)."""

from __future__ import annotations

import pytest

from retry import (
    RetryPolicy,
    _compute_backoff,
    detect_missing_verdict,
    is_transient_error,
    with_retry,
)


# ---------------------------------------------------------------------------
# is_transient_error — TRANSIENT patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("detail", [
    "timeout after 600s",
    "Connection reset by peer",
    "ECONNRESET",
    "Read timeout",
    "429 Too Many Requests",
    "rate-limit exceeded",
    "rate_limit_exceeded",
    "503 Service Unavailable",
    "BadGateway from upstream",
    "empty stdout. stderr: (empty)",
    "empty output (TTY capture may have failed)",
    "overloaded_error: API is overloaded",
    "RateLimitError: please retry",
    "APIConnectionError: connection refused",
    "GatewayTimeout",
])
def test_transient_patterns_classified_transient(detail):
    assert is_transient_error(detail), f"should be transient: {detail!r}"


# ---------------------------------------------------------------------------
# is_transient_error — HARD patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("detail", [
    "runner binary not found: 'claude'",
    "budget exceeded: $5.0001 > $5.0000",
    "RED_VERIFIED reported but output is missing the required test files block",
    "@test did not follow output contract",
    "Authentication failed",
    "401 Unauthorized",
    "403 Forbidden",
    "invalid_api_key supplied",
    "invalid api key supplied",
])
def test_hard_patterns_classified_hard(detail):
    assert not is_transient_error(detail), f"should be hard: {detail!r}"


def test_empty_detail_is_hard():
    """No detail → cannot justify a retry."""
    assert not is_transient_error("")
    assert not is_transient_error(None)  # type: ignore[arg-type]


def test_unknown_pattern_is_hard():
    """Conservative default: unknown errors are not retried."""
    assert not is_transient_error("Some random unstructured error message")


def test_hard_pattern_short_circuits_transient_in_same_string():
    """A hard signature in the detail wins over a transient signature."""
    detail = "budget exceeded after timeout retry attempts"
    assert not is_transient_error(detail)


def test_transient_exit_codes_recognized():
    assert is_transient_error("non-zero exit (137)")  # SIGKILL
    assert is_transient_error("non-zero exit (124)")  # GNU timeout
    assert is_transient_error("non-zero exit (143)")  # SIGTERM


def test_non_transient_exit_code_alone_is_hard():
    """Exit code 1 with no other signal → unknown → HARD."""
    assert not is_transient_error("non-zero exit (1): stderr=Some logic error")


# ---------------------------------------------------------------------------
# detect_missing_verdict
# ---------------------------------------------------------------------------

def test_detect_missing_verdict_none_when_present():
    out = "Some content...\nVERDICT: REVIEW_DONE\n"
    assert detect_missing_verdict(out, ["REVIEW_DONE"]) is None


def test_detect_missing_verdict_returns_first_when_absent():
    out = "Agent said some things but never declared a verdict."
    assert detect_missing_verdict(out, ["RED_VERIFIED", "INCOMPLETE_COVERAGE"]) == "RED_VERIFIED"


def test_detect_missing_verdict_any_of_list_satisfies():
    out = "INCOMPLETE_COVERAGE: missing AC1 mapping"
    assert detect_missing_verdict(out, ["RED_VERIFIED", "INCOMPLETE_COVERAGE", "ENV_BROKEN"]) is None


def test_detect_missing_verdict_empty_list_returns_none():
    assert detect_missing_verdict("anything", []) is None


def test_detect_missing_verdict_empty_output_returns_first():
    assert detect_missing_verdict("", ["X", "Y"]) == "X"


def test_detect_missing_verdict_case_insensitive():
    out = "verdict: review_done"
    assert detect_missing_verdict(out, ["REVIEW_DONE"]) is None


# ---------------------------------------------------------------------------
# _compute_backoff
# ---------------------------------------------------------------------------

def test_compute_backoff_grows_exponentially():
    policy = RetryPolicy(base_delay_sec=2.0, max_delay_sec=100.0, jitter=0.0)
    d1 = _compute_backoff(1, policy)
    d2 = _compute_backoff(2, policy)
    d3 = _compute_backoff(3, policy)
    assert d1 == pytest.approx(2.0, rel=0.01)
    assert d2 == pytest.approx(4.0, rel=0.01)
    assert d3 == pytest.approx(8.0, rel=0.01)


def test_compute_backoff_respects_max_cap():
    policy = RetryPolicy(base_delay_sec=2.0, max_delay_sec=5.0, jitter=0.0)
    d10 = _compute_backoff(10, policy)
    assert d10 == pytest.approx(5.0, rel=0.01)


def test_compute_backoff_never_below_floor():
    policy = RetryPolicy(base_delay_sec=0.001, jitter=0.0)
    d = _compute_backoff(1, policy)
    assert d >= 0.1


def test_compute_backoff_jitter_within_range():
    policy = RetryPolicy(base_delay_sec=10.0, max_delay_sec=20.0, jitter=0.25)
    samples = [_compute_backoff(1, policy) for _ in range(100)]
    # All samples within ±25% of 10s
    assert all(7.5 - 0.001 <= s <= 12.5 + 0.001 for s in samples), samples


# ---------------------------------------------------------------------------
# with_retry executor
# ---------------------------------------------------------------------------

class _RetryableError(Exception):
    pass


class _PermanentError(Exception):
    pass


def _is_retryable(e: Exception) -> bool:
    return isinstance(e, _RetryableError)


def test_with_retry_returns_result_on_first_success():
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        return "ok"

    result = with_retry(fn, is_transient=_is_retryable, sleep_fn=lambda _: None)
    assert result == "ok"
    assert calls["n"] == 1


def test_with_retry_retries_transient_then_succeeds():
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _RetryableError("retry me")
        return "ok"

    result = with_retry(
        fn,
        is_transient=_is_retryable,
        policy=RetryPolicy(max_attempts=5),
        sleep_fn=lambda _: None,
    )
    assert result == "ok"
    assert calls["n"] == 3


def test_with_retry_no_retry_on_hard_error():
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise _PermanentError("hard")

    with pytest.raises(_PermanentError):
        with_retry(fn, is_transient=_is_retryable, sleep_fn=lambda _: None)
    assert calls["n"] == 1


def test_with_retry_exhausts_attempts_then_raises():
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise _RetryableError("always fails")

    with pytest.raises(_RetryableError):
        with_retry(
            fn,
            is_transient=_is_retryable,
            policy=RetryPolicy(max_attempts=3),
            sleep_fn=lambda _: None,
        )
    assert calls["n"] == 3


def test_with_retry_calls_on_retry_hook():
    seen: list[tuple[int, str, float]] = []

    def hook(attempt: int, exc: Exception, delay: float) -> None:
        seen.append((attempt, str(exc), delay))

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _RetryableError(f"attempt {calls['n']}")
        return "done"

    with_retry(
        fn,
        is_transient=_is_retryable,
        policy=RetryPolicy(max_attempts=5),
        on_retry=hook,
        sleep_fn=lambda _: None,
    )
    assert len(seen) == 2  # called twice (between attempt 1→2 and 2→3)
    assert seen[0][0] == 1
    assert seen[1][0] == 2


def test_with_retry_hook_exception_does_not_break_retry():
    """An on_retry callback that raises must not abort the retry loop."""
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise _RetryableError("once")
        return "ok"

    def bad_hook(*args):
        raise RuntimeError("hook is broken")

    result = with_retry(
        fn,
        is_transient=_is_retryable,
        policy=RetryPolicy(max_attempts=3),
        on_retry=bad_hook,
        sleep_fn=lambda _: None,
    )
    assert result == "ok"
    assert calls["n"] == 2


def test_call_agent_with_contract_passes_through_when_verdict_present(monkeypatch):
    """If the first response declares an expected verdict, no retry happens."""
    import orchestrator

    calls = []

    def fake_call(name, prompt, **kwargs):
        calls.append(prompt)
        return "Done.\n\nVERDICT: RED_VERIFIED\n"

    monkeypatch.setattr(orchestrator, "call_agent", fake_call)
    out = orchestrator.call_agent_with_contract(
        "test", "do the thing", expected_verdicts=["RED_VERIFIED", "INCOMPLETE_COVERAGE"],
    )
    assert "RED_VERIFIED" in out
    assert len(calls) == 1


def test_call_agent_with_contract_retries_once_on_missing_verdict(monkeypatch):
    """If the first response has no verdict, retry once with a stricter prompt."""
    import orchestrator

    responses = iter([
        "Wrote some tests but forgot the verdict line.",
        "Retry response.\n\nVERDICT: RED_VERIFIED\n",
    ])
    prompts: list[str] = []

    def fake_call(name, prompt, **kwargs):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(orchestrator, "call_agent", fake_call)
    out = orchestrator.call_agent_with_contract(
        "test", "do the thing", expected_verdicts=["RED_VERIFIED", "INCOMPLETE_COVERAGE"],
    )
    assert "RED_VERIFIED" in out
    assert len(prompts) == 2
    # Second prompt has stricter contract reminder
    assert "OUTPUT CONTRACT" in prompts[1]
    assert "RED_VERIFIED / INCOMPLETE_COVERAGE" in prompts[1]


def test_call_agent_with_contract_returns_first_when_retry_also_fails(monkeypatch):
    """If both attempts miss the verdict, fall back to first response (don't lose work)."""
    import orchestrator

    responses = iter([
        "First attempt — no verdict.",
        "Second attempt — also no verdict.",
    ])

    def fake_call(name, prompt, **kwargs):
        return next(responses)

    monkeypatch.setattr(orchestrator, "call_agent", fake_call)
    out = orchestrator.call_agent_with_contract(
        "test", "do the thing", expected_verdicts=["RED_VERIFIED"],
    )
    # Should return ONE of the two attempts (helper returns the original on retry fail)
    assert "attempt" in out
    assert "VERDICT" not in out  # truly no verdict


def test_call_agent_with_contract_returns_original_on_retry_hard_error(monkeypatch):
    """If the stricter retry raises an AgentError, the original output is returned."""
    import orchestrator

    state = {"call": 0}

    def fake_call(name, prompt, **kwargs):
        state["call"] += 1
        if state["call"] == 1:
            return "First response — no verdict."
        raise orchestrator.AgentError(name, "auth failure on retry")

    monkeypatch.setattr(orchestrator, "call_agent", fake_call)
    out = orchestrator.call_agent_with_contract(
        "test", "do the thing", expected_verdicts=["RED_VERIFIED"],
    )
    assert "First response" in out
    assert state["call"] == 2  # tried twice


def test_call_agent_with_contract_passes_kwargs_through(monkeypatch):
    """cwd, model, skill, timeout reach the underlying call_agent."""
    import orchestrator

    captured: dict = {}

    def fake_call(name, prompt, **kwargs):
        captured.update(kwargs)
        return "VERDICT: REVIEW_DONE"

    monkeypatch.setattr(orchestrator, "call_agent", fake_call)
    orchestrator.call_agent_with_contract(
        "engineer", "do the thing",
        expected_verdicts=["REVIEW_DONE"],
        cwd="/tmp/wt",
        model="claude-haiku-4-5-20251001",
        skill="review-code",
        timeout=120,
    )
    assert captured.get("cwd") == "/tmp/wt"
    assert captured.get("model") == "claude-haiku-4-5-20251001"
    assert captured.get("skill") == "review-code"
    assert captured.get("timeout") == 120


def test_with_retry_respects_total_time_ceiling():
    """If max_total_sec is exceeded mid-loop, the last exception propagates."""
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise _RetryableError("slow loop")

    # Simulate a sleep that advances "wall clock" past max_total_sec
    fake_time = [0.0]

    def fake_sleep(d: float) -> None:
        fake_time[0] += d

    import retry as retry_mod

    real_monotonic = retry_mod.time.monotonic
    try:
        retry_mod.time.monotonic = lambda: fake_time[0]
        with pytest.raises(_RetryableError):
            with_retry(
                fn,
                is_transient=_is_retryable,
                policy=RetryPolicy(
                    max_attempts=10,
                    base_delay_sec=10.0,
                    max_delay_sec=10.0,
                    max_total_sec=15.0,
                    jitter=0.0,
                ),
                sleep_fn=fake_sleep,
            )
    finally:
        retry_mod.time.monotonic = real_monotonic
    # Should have stopped early — much less than 10 attempts
    assert calls["n"] < 10
