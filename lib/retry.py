"""Retry layer for transient agent / API failures.

Classifies agent errors as TRANSIENT (worth retrying with backoff) or HARD
(fail fast — retry won't help). Used by `lib/orchestrator.py:call_agent` to
wrap runner invocations.

Design principles:
- **Conservative classification**: when in doubt, treat as HARD. False
  positives waste cost; false negatives only delay an unrecoverable failure.
- **Bounded retries**: max_attempts (default 3) + max_total_seconds (default
  180s) so a flaky run can't loop forever.
- **Exponential backoff with jitter** to avoid thundering-herd retries when
  a rate-limit window opens.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar


T = TypeVar("T")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Patterns that indicate a transient failure worth retrying.
_TRANSIENT_PATTERNS = [
    # Network / connection
    r"\bconnection\b",
    r"\bECONNRESET\b",
    r"\bETIMEDOUT\b",
    r"\bEPIPE\b",
    r"\bResource temporarily unavailable\b",
    r"\bsocket hang up\b",
    # Timeouts (our own + upstream)
    r"\btimeout after \d+s\b",
    r"\bRead timeout\b",
    r"\bRequest timeout\b",
    # Rate limits
    r"\brate[ _-]?limit",
    r"\b429\b",
    r"\bTooManyRequests\b",
    r"\bquota exceeded\b",
    # 5xx server errors
    r"\b5\d{2}\b",
    r"\bInternalServerError\b",
    r"\bBadGateway\b",
    r"\bServiceUnavailable\b",
    r"\bGatewayTimeout\b",
    # Empty output / process died (often transient infra issues)
    r"\bempty stdout\b",
    r"\bempty output\b",
    r"\bTTY capture may have failed\b",
    # Anthropic / OpenAI specific error fragments
    r"\boverloaded_error\b",
    r"\bapi_error\b",
    r"\bAPIConnectionError\b",
    r"\bRateLimitError\b",
]

# Exit codes that always mean "process killed by OS" (OOM, signal) and are
# transient if the orchestrator can re-invoke with the same prompt.
_TRANSIENT_EXIT_CODES = {124, 134, 137, 143}

# Patterns that signal a HARD failure even when they appear in transient-looking
# contexts (these short-circuit the transient check).
_HARD_PATTERNS = [
    r"\brunner binary not found\b",
    r"\bbudget exceeded\b",
    r"\boutput is missing the required\b",
    r"\bdid not follow output contract\b",
    r"\bno commit hash extracted\b",
    r"\bauthentication\b",
    r"\bunauthorized\b",
    r"\b401\b",
    r"\b403\b",
    r"\binvalid[_ ]api[_ ]key\b",
]


def is_transient_error(detail: str) -> bool:
    """Classify an AgentError detail string. Returns True if retry is worthwhile.

    Conservative: HARD wins ties. Unknown errors are HARD.
    """
    if not detail:
        # No detail given — treat as HARD (we have no basis to retry)
        return False

    # HARD patterns short-circuit
    for pat in _HARD_PATTERNS:
        if re.search(pat, detail, re.IGNORECASE):
            return False

    # Check for transient exit codes embedded in the detail
    m = re.search(r"non-zero exit \((-?\d+)\)", detail)
    if m:
        try:
            rc = int(m.group(1))
            if rc in _TRANSIENT_EXIT_CODES:
                return True
        except ValueError:  # pragma: no cover — regex captures \d+, int() cannot fail
            pass

    # Transient signature scan
    for pat in _TRANSIENT_PATTERNS:
        if re.search(pat, detail, re.IGNORECASE):
            return True

    return False


# ---------------------------------------------------------------------------
# Output contract checker
# ---------------------------------------------------------------------------

def detect_missing_verdict(output: str, expected_verdicts: list[str]) -> Optional[str]:
    """Return the first expected verdict if the output is missing all of them.

    `expected_verdicts` is a list like ["RED_VERIFIED", "INCOMPLETE_COVERAGE",
    "ENV_BROKEN"] — any one of them is acceptable. If none appears, return the
    first as the canonical "missing" verdict for error reporting.

    Returns None if at least one expected verdict appears (output is OK).
    """
    if not expected_verdicts:
        return None
    if not output:
        return expected_verdicts[0]
    upper = output.upper()
    for v in expected_verdicts:
        if v.upper() in upper:
            return None
    return expected_verdicts[0]


# ---------------------------------------------------------------------------
# Retry executor
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 3        # 1 original + 2 retries
    base_delay_sec: float = 5.0  # first retry waits ~5s
    max_delay_sec: float = 60.0  # cap any single backoff at 60s
    max_total_sec: float = 180.0 # hard ceiling across all attempts
    jitter: float = 0.25         # ±25% jitter to avoid thundering herd


def _compute_backoff(attempt: int, policy: RetryPolicy) -> float:
    """Exponential backoff with jitter. `attempt` is 1-based (1 = first retry)."""
    raw = policy.base_delay_sec * (2 ** (attempt - 1))
    capped = min(raw, policy.max_delay_sec)
    jitter_range = capped * policy.jitter
    jittered = capped + random.uniform(-jitter_range, jitter_range)
    return max(0.1, jittered)


def with_retry(
    fn: Callable[[], T],
    *,
    is_transient: Callable[[Exception], bool],
    policy: Optional[RetryPolicy] = None,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Run `fn()` with retry on transient failures.

    Args:
        fn: thunk that performs the work; raises on failure.
        is_transient: callable that returns True if an exception should be retried.
        policy: RetryPolicy (defaults shown above).
        on_retry: optional hook called with (attempt_num, exception, delay_sec).
        sleep_fn: injectable for testing — defaults to time.sleep.

    Returns the result of fn() on success. Raises the last exception on
    final failure (whether transient-exhausted or hard error).
    """
    policy = policy or RetryPolicy()
    started = time.monotonic()
    last_exc: Optional[Exception] = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_transient(exc):
                # Hard failure — do not retry
                raise
            if attempt >= policy.max_attempts:
                raise
            elapsed = time.monotonic() - started
            if elapsed >= policy.max_total_sec:
                raise
            delay = _compute_backoff(attempt, policy)
            # Don't sleep past the total-time ceiling
            remaining = policy.max_total_sec - elapsed
            if delay > remaining:
                delay = max(0.1, remaining)
            if on_retry is not None:
                try:
                    on_retry(attempt, exc, delay)
                except Exception as hook_exc:
                    # M14.4: never let an on_retry callback break the retry, but
                    # surface the swallowed exception so a broken hook is visible.
                    import sys as _sys
                    print(
                        f"WARNING: on_retry hook raised {type(hook_exc).__name__}: {hook_exc}",
                        file=_sys.stderr,
                    )
            sleep_fn(delay)

    # Unreachable, but keeps type checkers happy
    assert last_exc is not None  # pragma: no cover
    raise last_exc  # pragma: no cover
