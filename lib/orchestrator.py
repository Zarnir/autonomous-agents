#!/usr/bin/env python3
"""
Autonomous-agents orchestrator.

Deterministic state machine that drives the OpenCode-based development pipeline.
Replaces the prose orchestrator in `commands/develop.md`. Calls agents via
OpenCode subprocess. All retry, convergence, verification, and error logic
lives here in code — not in LLM-interpreted prose.

Usage:
    python lib/orchestrator.py develop                     # full pipeline
    python lib/orchestrator.py develop --spec docs/specs/auth.md
    python lib/orchestrator.py develop --story STORY-id
    python lib/orchestrator.py develop --dry-run           # plan only, no impl
    python lib/orchestrator.py develop --force             # overwrite progress
    python lib/orchestrator.py resume                      # resume from checkpoint
    python lib/orchestrator.py resume --retry-failed
    python lib/orchestrator.py resume --retry-blocked
    python lib/orchestrator.py status                      # print current state

Agent invocation:
    By default this script invokes agents via:
        opencode run --agent <name>     (with the prompt sent via stdin)
    Override by setting OPENCODE_AGENT_CMD in the environment, e.g.:
        export OPENCODE_AGENT_CMD="my-agent-runner exec"

Test invocation (independent verification after @make claims GREEN):
    Detected automatically from the project tree, or override with TEST_CMD:
        export TEST_CMD="npm test"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import pty
import select
import sys
import threading
import time
import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# Deterministic spec parser (lives next to this file)
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from spec_parser import parse_specs, validate_specs, MalformedSpec  # type: ignore
except ImportError:  # pragma: no cover — defensive guard for missing module
    parse_specs = None
    validate_specs = None
    MalformedSpec = Exception  # type: ignore


PROGRESS_FILE = Path(".opencode/progress.json")
PROGRESS_BACKUP = Path(".opencode/progress.backup.json")
SCHEMA_VERSION = "2.0"
CONFIG_FILE = Path(".opencode/config.json")

EXIT_MORE_WORK = 3

DEFAULT_AGENT_CMD = os.environ.get("OPENCODE_AGENT_CMD", "opencode run --agent")
TEST_CMD_OVERRIDE = os.environ.get("TEST_CMD", "")

def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(
            f"WARNING: env {name}={raw!r} is not an integer; using default {default}",
            file=sys.stderr,
        )
        return default


MAX_REVIEW_CYCLES = _int_env("MAX_REVIEW_CYCLES", 2)
MAX_TEST_RETRIES = _int_env("MAX_TEST_RETRIES", 1)
MAX_MAKE_RETRIES = _int_env("MAX_MAKE_RETRIES", 2)
MAX_DELEGATIONS_PER_PHASE = _int_env("MAX_DELEGATIONS_PER_PHASE", 2)
# M24: live heartbeat tick interval during a single agent call.
# Set 0 (or any non-positive value) to disable heartbeat ticks.
HEARTBEAT_INTERVAL_SEC = _int_env("HEARTBEAT_INTERVAL_SEC", 30)
AGENT_TIMEOUT_SEC = _int_env("AGENT_TIMEOUT_SEC", 600)
OUTER_TIMEOUT_SEC = _int_env("OUTER_TIMEOUT_SEC", 480)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"WARNING: env {name}={raw!r} is not a number; using default {default}", file=sys.stderr)
        return default


MAX_BUDGET_USD = _float_env("MAX_BUDGET_USD", 0.0)  # 0 = uncapped
EXIT_BUDGET_EXCEEDED = 5

# M11: transient-failure retry knobs
AGENT_MAX_RETRIES = _int_env("AGENT_MAX_RETRIES", 3)        # 1 original + 2 retries
AGENT_RETRY_BASE_DELAY_SEC = _float_env("AGENT_RETRY_BASE_DELAY_SEC", 5.0)

AGENT_TIMEOUTS: dict[str, int] = {}
AGENT_MODELS: dict[str, str] = {}
GLOBAL_PERSIST_STATE: Optional[dict] = None
USE_PTY = True
_CONFIG_RUNNER: Optional[str] = None  # set by load_config()
# M21: registered by cmd_serve; when set, log() and finalize_story() fan out
# events through it. None by default so the pipeline runs unchanged.
_EVENT_BUS: Optional[object] = None


def _coerce_int(field: str, value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        print(
            f"WARNING: config field {field}={value!r} is not an integer; using default {default}",
            file=sys.stderr,
        )
        return default


def load_config() -> dict[str, Any]:
    global MAX_REVIEW_CYCLES, MAX_TEST_RETRIES, MAX_MAKE_RETRIES
    global MAX_DELEGATIONS_PER_PHASE, HEARTBEAT_INTERVAL_SEC
    global AGENT_TIMEOUT_SEC, OUTER_TIMEOUT_SEC, AGENT_TIMEOUTS
    global AGENT_MODELS, _CONFIG_RUNNER, _AGENT_RUNNERS

    if not CONFIG_FILE.exists():
        return {}

    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(
            f"WARNING: could not read {CONFIG_FILE}: {e}. Falling back to defaults.",
            file=sys.stderr,
        )
        return {}

    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    if "max_review_cycles" in pipe:
        MAX_REVIEW_CYCLES = _coerce_int("pipeline.max_review_cycles", pipe["max_review_cycles"], MAX_REVIEW_CYCLES)
    if "max_test_retries" in pipe:
        MAX_TEST_RETRIES = _coerce_int("pipeline.max_test_retries", pipe["max_test_retries"], MAX_TEST_RETRIES)
    if "max_make_retries" in pipe:
        MAX_MAKE_RETRIES = _coerce_int("pipeline.max_make_retries", pipe["max_make_retries"], MAX_MAKE_RETRIES)
    if "max_delegations_per_phase" in pipe:
        MAX_DELEGATIONS_PER_PHASE = _coerce_int(
            "pipeline.max_delegations_per_phase",
            pipe["max_delegations_per_phase"],
            MAX_DELEGATIONS_PER_PHASE,
        )
    if "heartbeat_interval_sec" in pipe:
        HEARTBEAT_INTERVAL_SEC = _coerce_int(
            "pipeline.heartbeat_interval_sec",
            pipe["heartbeat_interval_sec"],
            HEARTBEAT_INTERVAL_SEC,
        )
    if "agent_timeout_sec" in pipe:
        AGENT_TIMEOUT_SEC = _coerce_int("pipeline.agent_timeout_sec", pipe["agent_timeout_sec"], AGENT_TIMEOUT_SEC)
    if "outer_timeout_sec" in pipe:
        OUTER_TIMEOUT_SEC = _coerce_int("pipeline.outer_timeout_sec", pipe["outer_timeout_sec"], OUTER_TIMEOUT_SEC)

    timeo = cfg.get("agent_timeouts", {}) if isinstance(cfg, dict) else {}
    if isinstance(timeo, dict):
        AGENT_TIMEOUTS = {
            str(k): _coerce_int(f"agent_timeouts.{k}", v, AGENT_TIMEOUT_SEC)
            for k, v in timeo.items()
        }

    # Per-agent model overrides (M3.3)
    models = cfg.get("models", {}) if isinstance(cfg, dict) else {}
    if isinstance(models, dict):
        AGENT_MODELS = {str(k): str(v) for k, v in models.items() if v}

    # Runner selection (M1.1) — env always wins; config field is fallback
    runner_cfg = pipe.get("runner") if isinstance(pipe, dict) else None
    if isinstance(runner_cfg, str) and runner_cfg in ("opencode", "claude"):
        _CONFIG_RUNNER = runner_cfg

    # M25: per-agent runner dispatch. `pipeline.agent_runners` maps agent name
    # to runner name; unknown runner names are rejected with a warning.
    agent_runners_cfg = pipe.get("agent_runners") if isinstance(pipe, dict) else None
    if isinstance(agent_runners_cfg, dict):
        _VALID_RUNNERS = ("claude", "opencode")
        for agent_name, runner_name in agent_runners_cfg.items():
            if isinstance(runner_name, str) and runner_name in _VALID_RUNNERS:
                _AGENT_RUNNERS[str(agent_name)] = runner_name
            else:
                print(
                    f"WARNING: pipeline.agent_runners[{agent_name!r}]={runner_name!r} "
                    f"is not a valid runner name; valid: {_VALID_RUNNERS}. Skipping.",
                    file=sys.stderr,
                )

    # M3.2 budget — env wins, then config
    global MAX_BUDGET_USD
    if MAX_BUDGET_USD == 0.0:  # not set via env
        budget_cfg = pipe.get("max_budget_usd") if isinstance(pipe, dict) else None
        if budget_cfg is not None:
            try:
                MAX_BUDGET_USD = float(budget_cfg)
            except (TypeError, ValueError):
                print(f"WARNING: pipeline.max_budget_usd={budget_cfg!r} is not a number", file=sys.stderr)

    # M11 retry — env wins, then config
    global AGENT_MAX_RETRIES, AGENT_RETRY_BASE_DELAY_SEC
    if isinstance(pipe, dict):
        if "agent_max_retries" in pipe:
            AGENT_MAX_RETRIES = _coerce_int("pipeline.agent_max_retries", pipe["agent_max_retries"], AGENT_MAX_RETRIES)
        if "agent_retry_base_delay_sec" in pipe:
            try:
                AGENT_RETRY_BASE_DELAY_SEC = float(pipe["agent_retry_base_delay_sec"])
            except (TypeError, ValueError):
                print(
                    f"WARNING: pipeline.agent_retry_base_delay_sec={pipe['agent_retry_base_delay_sec']!r} "
                    "is not a number",
                    file=sys.stderr,
                )

    return cfg


load_config()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read_progress_json(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        die(f"progress.json is not valid UTF-8: {e}. Restore from {PROGRESS_BACKUP} or delete to restart.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        die(
            f"progress.json is corrupt or truncated: {e}. "
            f"Restore from {PROGRESS_BACKUP} or delete to restart with `develop --force`."
        )


def read_progress() -> dict:
    if not PROGRESS_FILE.exists():
        die("No .opencode/progress.json. Run `develop` first to create a plan.")
    data = _read_progress_json(PROGRESS_FILE)
    if data.get("schema_version") != SCHEMA_VERSION:
        die(
            f"Schema mismatch: on-disk={data.get('schema_version')!r}, "
            f"expected={SCHEMA_VERSION!r}. Re-run develop --force."
        )
    return data


def write_progress(data: dict, expected_version: int) -> int:
    """Optimistic concurrency. Raises VersionConflict on mismatch."""
    if PROGRESS_FILE.exists():
        current = _read_progress_json(PROGRESS_FILE)
        if current.get("version") != expected_version:
            raise VersionConflict(expected=expected_version, found=current.get("version"))
    new_version = expected_version + 1
    data["version"] = new_version
    data["updated_at"] = now_iso()
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(PROGRESS_FILE)
    return new_version


class VersionConflict(RuntimeError):
    def __init__(self, expected: Optional[int], found: Optional[int]):
        super().__init__(
            f"progress.json version conflict (expected={expected}, found={found})"
        )
        self.expected = expected
        self.found = found


def log(msg: str) -> None:
    print(f"[{now_iso()}] {msg}", flush=True)
    # M21: fan out to A2A event stream subscribers when `aa-orchestrator serve`
    # has registered a bus. No-op cost when bus is None (the default).
    if _EVENT_BUS is not None:
        try:
            _EVENT_BUS.notify("event/log_appended", {"ts": now_iso(), "msg": str(msg)})
        except Exception:  # pragma: no cover — defensive; never break log()
            pass


@contextlib.contextmanager
def _agent_heartbeat(name: str, timeout: int, interval: Optional[float] = None) -> Iterator[None]:
    """M24: emit live elapsed-time ticks during a synchronous agent call.

    Wraps a synchronous block (typically `runner.run()`). A daemon thread
    fires every `interval` seconds with an `⏱ @<name> running for Xs` log
    line. On exit (normal OR exception), emits one `⏲ @<name> finished in Xs`
    line so duration is always visible.

    Graduated slowness warnings fire ONCE each at 5/10/20 min elapsed:
      `⚠   @<name> elapsed 5m`
      `⚠⚠  @<name> elapsed 10m`
      `⚠⚠⚠ @<name> elapsed 20m — approaching timeout (<X>s remaining)`

    M21 integration: if `_EVENT_BUS` is registered, ticks also fan out as
    `event/agent_heartbeat` notifications.

    Disable: pass `interval=0` (or set `HEARTBEAT_INTERVAL_SEC=0`).
    """
    eff_interval = interval if interval is not None else HEARTBEAT_INTERVAL_SEC
    start = time.monotonic()
    stop_flag = threading.Event()
    warnings_fired = {5: False, 10: False, 20: False}

    def _tick_loop() -> None:
        while not stop_flag.wait(eff_interval):
            elapsed = time.monotonic() - start
            log(f"  ⏱ @{name} running for {elapsed:.0f}s (timeout {timeout}s)")
            if _EVENT_BUS is not None:
                try:
                    _EVENT_BUS.notify("event/agent_heartbeat", {
                        "agent": name,
                        "elapsed_sec": round(elapsed, 1),
                        "timeout": timeout,
                    })
                except Exception:  # pragma: no cover — defensive
                    pass
            # Graduated warnings — each fires once per heartbeat instance.
            for threshold_min in (5, 10, 20):
                if not warnings_fired[threshold_min] and elapsed >= threshold_min * 60:
                    warnings_fired[threshold_min] = True
                    marker = "⚠" * (1 if threshold_min == 5 else 2 if threshold_min == 10 else 3)
                    suffix = ""
                    if threshold_min == 20:
                        remaining = max(0, timeout - int(elapsed))
                        suffix = f" — approaching timeout ({remaining}s remaining)"
                    log(f"  {marker} @{name} elapsed {threshold_min}m{suffix}")

    tick_thread: Optional[threading.Thread] = None
    if eff_interval > 0:
        tick_thread = threading.Thread(target=_tick_loop, daemon=True)
        tick_thread.start()
    try:
        yield
    finally:
        stop_flag.set()
        if tick_thread is not None:
            tick_thread.join(timeout=2.0)
        elapsed = time.monotonic() - start
        log(f"  ⏲ @{name} finished in {elapsed:.1f}s")


def append_execution_log(data: dict, message: str) -> None:
    data.setdefault("execution_log", []).append({"ts": now_iso(), "msg": message})
    if len(data["execution_log"]) > 5000:
        data["execution_log"] = data["execution_log"][-4000:]


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Agent invocation
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


try:
    from runners import (
        AgentRunnerError,
        ClaudeCodeRunner,
        OpenCodeRunner,
        Runner,
        select_runner,
    )
    _RUNNERS_AVAILABLE = True
except ImportError:  # pragma: no cover — defensive guard
    _RUNNERS_AVAILABLE = False

try:
    from cost_tracker import accumulate as _cost_accumulate
    from cost_tracker import compute_cost as _cost_compute
    from cost_tracker import format_summary as _cost_format
    from cost_tracker import parse_usage as _cost_parse
    _COST_TRACKING_AVAILABLE = True
except ImportError:  # pragma: no cover — defensive guard
    _COST_TRACKING_AVAILABLE = False

try:
    from retry import RetryPolicy as _RetryPolicy
    from retry import is_transient_error as _is_transient_error
    from retry import with_retry as _with_retry
    _RETRY_AVAILABLE = True
except ImportError:  # pragma: no cover — defensive guard
    _RETRY_AVAILABLE = False

try:
    from wizard import (
        PipelineState as _PipelineState,
        WizardAborted as _WizardAborted,
        detect_state as _detect_state,
        prompt_choice as _prompt_choice,
        prompt_text as _prompt_text,
        prompt_yes_no as _prompt_yes_no,
    )
    _WIZARD_AVAILABLE = True
except ImportError:  # pragma: no cover — defensive guard
    _WIZARD_AVAILABLE = False


# M25: per-agent runner cache. Keys are runner names ("claude", "opencode");
# values are Runner instances. Replaces the prior singleton so a single project
# can route strategic agents through Claude Code while routing labor agents
# through OpenCode.
_RUNNER_INSTANCES: dict[str, object] = {}

# M25: populated by load_config from `pipeline.agent_runners`. Maps agent name
# (e.g. "planner") → runner name (e.g. "claude"). Unspecified agents fall
# through to the project-wide default.
_AGENT_RUNNERS: dict[str, str] = {}


def _get_runner_for_agent(name: str) -> object:
    """M25: resolve the runner instance for the given agent.

    Lookup order (first non-empty wins):
      1. `AA_RUNNER_<NAME>` env var (escape hatch for one-off testing)
      2. `pipeline.agent_runners[<name>]` from config (tiered cost-model entry)
      3. `AA_RUNNER` env var (project-wide override)
      4. `pipeline.runner` from config (project default)
      5. `select_runner(None)` auto-detect

    Runner instances are cached per runner-name. Two agents that map to the
    same runner share the same cached instance.
    """
    if not _RUNNERS_AVAILABLE:  # pragma: no cover — defensive against runners.py missing
        raise RuntimeError(
            "lib/runners.py is missing — re-run install.sh or ensure runners.py "
            "is installed alongside orchestrator.py."
        )

    # Per-agent env override: e.g. AA_RUNNER_PLANNER=claude
    per_agent_env = os.environ.get(f"AA_RUNNER_{name.upper().replace('-', '_')}")
    preference = (
        per_agent_env
        or _AGENT_RUNNERS.get(name)
        or os.environ.get("AA_RUNNER")
        or _CONFIG_RUNNER
    )

    cache_key = preference or "_auto_"
    cached = _RUNNER_INSTANCES.get(cache_key)
    if cached is not None:
        return cached

    instance = select_runner(preference if preference else None)
    _RUNNER_INSTANCES[cache_key] = instance
    log(f"  runner: {instance.name} (for @{name})")
    return instance


def call_agent(
    name: str,
    prompt: str,
    timeout: Optional[int] = None,
    *,
    cwd: Optional[str] = None,
    model: Optional[str] = None,
    skill: Optional[str] = None,
) -> str:
    effective_timeout = timeout if timeout is not None else AGENT_TIMEOUTS.get(name, AGENT_TIMEOUT_SEC)
    effective_model = model or AGENT_MODELS.get(name) or AGENT_MODELS.get("default")
    log(
        f"  -> calling @{name} "
        f"(timeout={effective_timeout}s"
        + (f", model={effective_model}" if effective_model else "")
        + (f", skill={skill}" if skill else "")
        + (f", cwd={cwd}" if cwd else "")
        + ")"
    )
    runner = _get_runner_for_agent(name)

    # M2.3: prepend cross-story context so each agent benefits from prior work
    context = load_project_context()
    if context:
        prompt = (
            "## Project context (recent completed stories)\n"
            f"{context}\n"
            "\n---\n\n"
            + prompt
        )

    # M7.2: prepend recent accepted ADRs so agents respect long-view decisions
    adr_context = load_recent_adrs()
    if adr_context:
        prompt = (
            "## Architecture decisions (accepted ADRs)\n"
            f"{adr_context}\n"
            "\n---\n\n"
            + prompt
        )

    # M11: wrap the runner invocation in a transient-failure retry loop.
    # M24: wrap in _agent_heartbeat so users see live elapsed-time ticks during
    # slow LLM calls instead of staring at an idle terminal.
    def _invoke() -> str:
        try:
            with _agent_heartbeat(name, timeout=effective_timeout):
                return runner.run(
                    name,
                    prompt,
                    timeout=effective_timeout,
                    cwd=cwd,
                    model=effective_model,
                    skill=skill,
                )
        except AgentRunnerError as e:
            # Translate runner error → AgentError with transient/hard classification
            transient = False
            if _RETRY_AVAILABLE:
                try:
                    transient = _is_transient_error(e.detail)
                except Exception:  # pragma: no cover — defensive
                    transient = False
            if transient:
                raise TransientAgentError(e.agent, e.detail) from e
            raise AgentError(e.agent, e.detail) from e

    if _RETRY_AVAILABLE:
        policy = _RetryPolicy(
            max_attempts=AGENT_MAX_RETRIES,
            base_delay_sec=AGENT_RETRY_BASE_DELAY_SEC,
        )

        def _on_retry(attempt: int, exc: Exception, delay: float) -> None:
            log(
                f"  ⟳ @{name} transient failure (attempt {attempt}/{AGENT_MAX_RETRIES}): "
                f"{getattr(exc, 'detail', str(exc))[:140]} — retrying in {delay:.1f}s"
            )

        try:
            output = _with_retry(
                _invoke,
                is_transient=lambda exc: isinstance(exc, TransientAgentError),
                policy=policy,
                on_retry=_on_retry,
            )
        except TransientAgentError as e:  # pragma: no cover — retries-exhausted path
            # Retries exhausted — surface as a regular AgentError to existing callers.
            raise AgentError(e.agent, f"transient_after_retries: {e.detail}") from e
    else:  # pragma: no cover — _RETRY_AVAILABLE always True in standard install
        output = _invoke()

    # M3.2: track token usage + cost (best-effort, never crashes the call)
    if _COST_TRACKING_AVAILABLE and GLOBAL_PERSIST_STATE is not None:
        try:
            usage = _cost_parse(output, runner.name)
            if usage is not None:
                cost_usd = _cost_compute(usage, effective_model)
                tracking = GLOBAL_PERSIST_STATE.setdefault("cost_tracking", {})
                _cost_accumulate(tracking, name, usage, cost_usd)
                # Budget check
                budget = MAX_BUDGET_USD
                if budget > 0 and tracking.get("total_usd", 0) > budget:
                    log(
                        f"  ⚠ budget exceeded: ${tracking['total_usd']:.4f} > ${budget:.4f}"
                    )
                    GLOBAL_PERSIST_STATE["status"] = "budget_exceeded"
                    try:
                        persist(GLOBAL_PERSIST_STATE)
                    except Exception:
                        pass
                    raise AgentError(
                        name,
                        f"budget exceeded: ${tracking['total_usd']:.4f} > ${budget:.4f}",
                    )
        except AgentError:
            raise
        except Exception as e:
            log(f"  ⚠ cost tracking failed: {type(e).__name__}: {e}")

    return output


class AgentError(RuntimeError):
    def __init__(self, agent: str, detail: str):
        super().__init__(f"@{agent} failed: {detail}")
        self.agent = agent
        self.detail = detail


class TransientAgentError(AgentError):
    """A transient agent failure (rate limit / timeout / 5xx). Worth retrying.

    Surfaces as a regular AgentError to callers once retries are exhausted
    (prefixed with `transient_after_retries:`).
    """


class MalformedOutputError(AgentError):
    """Agent returned a response that doesn't satisfy the output contract.

    Distinguished from generic AgentError so the orchestrator can retry once
    with a stricter prompt (instead of immediately failing the story).
    """


def call_agent_with_contract(
    name: str,
    prompt: str,
    expected_verdicts: list[str],
    *,
    cwd: Optional[str] = None,
    model: Optional[str] = None,
    skill: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """M11.6: invoke an agent and verify the output declares one of `expected_verdicts`.

    If the first response is missing all expected verdicts, retry ONCE with a
    stricter prompt that explicitly demands one of them. This catches the
    common LLM failure mode where the agent's narrative output omits the
    machine-readable verdict line the orchestrator depends on.

    After the second attempt also fails the contract, returns the response
    anyway (caller still has the original `AgentError` flow if it can't parse
    a verdict). This is intentional — we don't want contract-retry to
    obscure a genuine HARD agent failure.
    """
    output = call_agent(name, prompt, timeout=timeout, cwd=cwd, model=model, skill=skill)
    if not _RETRY_AVAILABLE:  # pragma: no cover — defensive
        return output

    try:
        from retry import detect_missing_verdict as _detect_missing
    except ImportError:  # pragma: no cover — defensive
        return output

    missing = _detect_missing(output, expected_verdicts)
    if missing is None:
        return output

    # One-shot retry with a stricter contract reminder
    log(
        f"  ⚠ @{name} output missing required verdict ({missing}) "
        f"— retrying once with stricter prompt"
    )
    verdicts_str = " / ".join(expected_verdicts)
    stricter_prompt = (
        f"{prompt}\n\n"
        "---\n\n"
        "## OUTPUT CONTRACT (your previous response failed this check)\n"
        f"Your response MUST end with one of these exact verdict tokens on its own line: {verdicts_str}\n"
        "Without one of these tokens, your work cannot be processed by the pipeline.\n"
        "Emit your analysis, then close with the appropriate verdict line.\n"
    )
    try:
        return call_agent(name, stricter_prompt, timeout=timeout, cwd=cwd, model=model, skill=skill)
    except AgentError:
        # If the stricter retry hard-fails, surface the original output —
        # the caller may still be able to extract meaning from it.
        return output


# ---------------------------------------------------------------------------
# M19: runner-agnostic mid-task sub-agent delegation
# ---------------------------------------------------------------------------

_DELEGATE_RE = re.compile(
    r"DELEGATE_TO:\s*@(?P<target>[a-zA-Z0-9_-]+)\s*\n"
    r"QUESTION:\s*\n"
    r"(?P<question>.*?)\n"
    r"END_DELEGATE",
    re.DOTALL,
)


def _parse_delegation_marker(output: str) -> Optional[tuple[str, str]]:
    """Return (target_agent, question) if a DELEGATE_TO marker is present.

    Returns None when no marker, malformed marker, or the END_DELEGATE token
    is missing — caller passes the parent's output through unchanged.
    """
    m = _DELEGATE_RE.search(output)
    if m is None:
        return None
    target = m.group("target").strip()
    question = m.group("question").strip()
    if not target or not question:
        return None
    return (target, question)


def call_agent_with_delegation(
    name: str,
    prompt: str,
    expected_verdicts: list[str],
    *,
    cwd: Optional[str] = None,
    model: Optional[str] = None,
    skill: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """Like `call_agent_with_contract`, but supports mid-task delegation.

    Contract visible to the LLM:
        DELEGATE_TO: @<agent>
        QUESTION:
        <one short focused question>
        END_DELEGATE

    The orchestrator parses the parent's output for this marker. If present
    AND the target is in the parent's `consult_agents` allow-list, the
    sub-agent is invoked via the normal `call_agent` path (so cost tracking
    flows through unchanged). The sub-agent's response is spliced back into
    the parent's prompt as a `## Consultation result from @<id>` block, and
    the parent is re-invoked via `call_agent_with_contract`.

    Bounded by `MAX_DELEGATIONS_PER_PHASE` (default 2). Sub-agents are
    reached via plain `call_agent`, so any `DELEGATE_TO:` markers they emit
    are ignored — no nesting.
    """
    consult_allow: list[str] = []
    delegation_block = ""
    if _RUNNERS_AVAILABLE:
        try:
            from runners import parse_agent_file
            agent_def = parse_agent_file(name)
            consult_allow = list(agent_def.consult_agents)
        except (FileNotFoundError, ImportError):
            consult_allow = []
        if consult_allow:
            delegation_block = (
                "\n\n---\n\n## Mid-task consultation\n"
                "If you need help from another agent, end your turn with:\n\n"
                "  DELEGATE_TO: @<agent>\n"
                "  QUESTION:\n"
                "  <one short, focused question>\n"
                "  END_DELEGATE\n\n"
                "The orchestrator will run that sub-agent, append its answer to your next turn,\n"
                f"and re-invoke you with full context. You may delegate at most {MAX_DELEGATIONS_PER_PHASE} times per phase.\n"
                "Sub-agents may NOT further delegate (no nesting).\n\n"
                f"Available sub-agents for your phase: {', '.join('@' + a for a in consult_allow)}\n"
            )

    current_prompt = prompt + delegation_block
    delegations_done = 0

    while True:
        output = call_agent_with_contract(
            name,
            current_prompt,
            expected_verdicts=expected_verdicts,
            cwd=cwd,
            model=model,
            skill=skill,
            timeout=timeout,
        )
        if not consult_allow:
            return output
        if delegations_done >= MAX_DELEGATIONS_PER_PHASE:
            return output

        parsed = _parse_delegation_marker(output)
        if parsed is None:
            return output

        target, question = parsed
        if target not in consult_allow:
            log(
                f"    ⚠ @{name} requested delegation to @{target} but it's not in "
                f"consult_agents={consult_allow!r} — ignoring marker"
            )
            return output

        log(
            f"    ↳ @{name} delegating to @{target} "
            f"(#{delegations_done + 1}/{MAX_DELEGATIONS_PER_PHASE})"
        )
        sub_prompt = (
            f"You are being consulted mid-task by @{name}.\n"
            f"@{name}'s question:\n\n{question}\n\n"
            "Give a focused answer suitable for @"
            f"{name} to act on. Do not modify any files unless your agent contract allows it."
        )
        try:
            sub_output = call_agent(target, sub_prompt, cwd=cwd)
        except AgentError as e:
            log(
                f"    ⚠ sub-agent @{target} failed "
                f"({str(e.detail)[:140]}) — returning parent output as-is"
            )
            return output

        delegations_done += 1
        current_prompt = (
            current_prompt
            + f"\n\n---\n\n## Consultation result from @{target}\n"
            + f"(question was: {question})\n\n"
            + sub_output.rstrip()
            + "\n\nContinue your task using this consultation result.\n"
        )


# ---------------------------------------------------------------------------
# Independent test verification (closes C2 — never trust @make's GREEN claim)
# ---------------------------------------------------------------------------

def detect_test_command() -> Optional[list[str]]:
    if TEST_CMD_OVERRIDE:
        return shlex.split(TEST_CMD_OVERRIDE)
    if Path("package.json").exists():
        return ["npm", "test", "--", "--silent"]
    if Path("pyproject.toml").exists() or Path("pytest.ini").exists():
        return ["pytest", "-q"]
    if Path("go.mod").exists():
        return ["go", "test", "./..."]
    if Path("Cargo.toml").exists():
        return ["cargo", "test"]
    return None


def run_tests_independently(test_files: list[str], cwd: Optional[str] = None) -> tuple[bool, str]:
    cmd = detect_test_command()
    if cmd is None:
        log("  ⚠ no test runner detected — cannot independently verify GREEN")
        return False, "no_test_runner_detected"
    if test_files and "pytest" in cmd[0]:
        cmd = cmd + test_files
    elif test_files and cmd[0] == "npm":
        cmd = cmd + ["--testPathPattern=" + "|".join(re.escape(f) for f in test_files)]
    log(f"  → independent test run: {' '.join(cmd)}" + (f" (cwd={cwd})" if cwd else ""))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False, cwd=cwd)
    except FileNotFoundError as e:
        die(
            f"test runner not found: {cmd[0]!r} ({e}). "
            "Configure TEST_CMD or ensure the project's test tooling is installed."
        )
    except subprocess.TimeoutExpired as e:
        return False, f"test_runner_timeout: {e}"
    output = (proc.stdout + "\n" + proc.stderr)[-4000:]
    return proc.returncode == 0, output


# ---------------------------------------------------------------------------
# Story finder helpers
# ---------------------------------------------------------------------------

def find_story(data: dict, story_id: str) -> Optional[dict]:
    for epic in data.get("epics", []):
        for story in epic.get("stories", []):
            if story["id"] == story_id:
                return story
    return None


def all_stories(data: dict) -> list[dict]:
    return [s for epic in data["epics"] for s in epic["stories"]]


def epic_for_story(data: dict, story_id: str) -> Optional[dict]:
    for epic in data["epics"]:
        for s in epic["stories"]:
            if s["id"] == story_id:
                return epic
    return None


def deps_satisfied(data: dict, story: dict) -> tuple[bool, list[str]]:
    """Closes M4: dep is satisfied iff story is `completed` AND has commit_hash."""
    unmet = []
    for dep_id in story.get("depends_on", []):
        dep = find_story(data, dep_id)
        if dep is None:
            unmet.append(f"{dep_id}(missing)")
            continue
        if dep.get("status") != "completed":
            unmet.append(f"{dep_id}(status={dep.get('status')})")
            continue
        if not (dep.get("artifacts") or {}).get("commit_hash"):
            unmet.append(f"{dep_id}(no_commit)")
    return (len(unmet) == 0, unmet)


def next_eligible_story(data: dict) -> Optional[dict]:
    pending = [s for s in all_stories(data) if s.get("status") == "pending"]
    if not pending:
        return None
    pending.sort(key=lambda s: (s.get("execution_wave", 999), s["id"]))
    for s in pending:
        ok, _ = deps_satisfied(data, s)
        if ok:
            return s
    return None


def cascade_fail(data: dict, failed_story_id: str, reason: str) -> int:
    """Closes H4: when a story fails, mark transitive dependents as blocked."""
    blocked_count = 0
    progress_made = True
    while progress_made:
        progress_made = False
        for s in all_stories(data):
            if s.get("status") in ("blocked", "failed", "completed"):
                continue
            for dep_id in s.get("depends_on", []):
                dep = find_story(data, dep_id)
                if dep and dep.get("status") in ("failed", "blocked"):
                    s["status"] = "blocked"
                    # M24: per-story failure_reason for visibility in cmd_status
                    s["failure_reason"] = (
                        f"cascade from {dep_id} ({dep.get('status')})"
                    )
                    s["completed_at"] = now_iso()
                    data.setdefault("blocked_stories", []).append(s["id"])
                    append_execution_log(
                        data,
                        f"cascade_blocked story={s['id']} upstream={dep_id} "
                        f"root_cause={failed_story_id} reason={reason}"
                    )
                    blocked_count += 1
                    progress_made = True
                    break
    return blocked_count


# ---------------------------------------------------------------------------
# Phase: spec parse + plan
# ---------------------------------------------------------------------------

def phase_spec_and_plan(spec_path: Optional[str], use_llm_spec: bool = False) -> dict:
    """
    Phase 1+2: parse specs and build execution plan.

    Default path: deterministic Python parsing of docs/specs/ via spec_parser.
    Fallback (use_llm_spec=True): invoke the LLM @spec agent for unstructured
    or legacy spec formats. Slower, less reliable, but tolerant of prose specs.
    """
    log("Phase 1: parsing spec markdown")

    spec_json: Optional[dict] = None

    if not use_llm_spec and parse_specs is not None:
        try:
            spec_json = parse_specs(Path("."))
            log(f"  parsed deterministically (no LLM call)")
        except MalformedSpec as e:
            log(f"  deterministic parse failed: {e}")
            log("  hint: run `aa-orchestrator validate` to see all issues, or pass --spec-llm-fallback")
            die(str(e))

    if spec_json is None:
        # LLM fallback path
        spec_prompt = (
            "CRITICAL: Your FIRST and ONLY output must be a fenced ```json ... ``` block. "
            "No prose before it, no explanation after it. No tool-narration.\n\n"
            f"Scan {'spec file=' + spec_path if spec_path else 'all .md files in the project'} "
            "and emit structured JSON per your schema."
        )
        spec_out = call_agent("spec", spec_prompt)
        spec_json = extract_json(spec_out)
        if "error" in spec_json:
            die(f"@spec error: {spec_json}")

    if not spec_json.get("epics"):
        die("No epics found in spec. See docs/specs/AUTHORING_GUIDE.md")

    log(
        f"  Found {len(spec_json['epics'])} epics, "
        f"{sum(len(e['stories']) for e in spec_json['epics'])} stories "
        f"({spec_json.get('methodology', 'structured')})"
    )

    log("Phase 2: building execution plan")
    if not _try_local_planner(spec_json):
        # Fallback: ask the LLM @planner agent to write progress.json
        plan_prompt = (
            "Convert this @spec output into a progress.json plan. "
            "Initialize all status fields to 'pending'. Spec:\n"
            f"{json.dumps(spec_json, indent=2)}"
        )
        call_agent("planner", plan_prompt)
        if not PROGRESS_FILE.exists():
            die("@planner did not write .opencode/progress.json")

    data = read_progress()
    log(
        f"  Plan written: version={data['version']}, "
        f"{sum(1 for s in all_stories(data) if s['status'] == 'pending')} pending stories, "
        f"{max(s.get('execution_wave', 1) for s in all_stories(data))} waves"
    )
    return data


def _try_local_planner(spec_json: dict) -> bool:
    """
    Build progress.json deterministically from a spec dict.

    Topological-sort stories by depends_on, assign execution_wave, initialize
    all status fields. Returns True on success. If something goes wrong,
    returns False so the caller can fall back to the LLM @planner.
    """
    try:
        # Build dep graph and assign waves
        story_map: dict[str, dict] = {}
        for epic in spec_json["epics"]:
            for s in epic.get("stories", []):
                story_map[s["id"]] = s

        # Kahn's algorithm for topological order
        in_degree: dict[str, int] = {sid: 0 for sid in story_map}
        for sid, s in story_map.items():
            for d in s.get("depends_on", []) or []:
                if d in story_map:
                    in_degree[sid] += 1

        wave: dict[str, int] = {}
        ready = [sid for sid, deg in in_degree.items() if deg == 0]
        current_wave = 1
        while ready:
            next_ready: list[str] = []
            for sid in ready:
                wave[sid] = current_wave
            for sid, s in story_map.items():
                if sid in wave:
                    continue
                deps = s.get("depends_on", []) or []
                if deps and all(dep in wave for dep in deps if dep in story_map):
                    next_ready.append(sid)
            ready = next_ready
            current_wave += 1

        # Anything left over is in a cycle — assign last-wave + 1 and warn
        max_wave = max(wave.values()) if wave else 1
        for sid in story_map:
            if sid not in wave:
                wave[sid] = max_wave + 1

        # Assemble progress.json
        epics_out = []
        for epic in spec_json["epics"]:
            stories_out = []
            for s in epic.get("stories", []):
                story_copy = dict(s)
                story_copy["status"] = "pending"
                story_copy["execution_wave"] = wave[s["id"]]
                story_copy["artifacts"] = {
                    "branch": None,
                    "worktree_path": None,
                    "test_files": [],
                    "implementation_files": [],
                    "review_findings_hashes": [],
                    "criterion_test_mapping": {},
                    "commit_hash": None,
                    "test_run_evidence": None,
                }
                # Ensure tasks have status
                tasks_out = []
                for t in s.get("tasks", []):
                    t_copy = dict(t)
                    t_copy.setdefault("status", "pending")
                    tasks_out.append(t_copy)
                story_copy["tasks"] = tasks_out
                stories_out.append(story_copy)
            epic_copy = dict(epic)
            epic_copy["status"] = "pending"
            epic_copy["stories"] = stories_out
            epics_out.append(epic_copy)

        plan = {
            "schema_version": SCHEMA_VERSION,
            "version": 1,
            "methodology": spec_json.get("methodology", "structured"),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "source_files": spec_json.get("source_files", []),
            "status": "pending",
            "current_story_id": None,
            "completed_stories": [],
            "failed_stories": [],
            "blocked_stories": [],
            "epics": epics_out,
            "execution_log": [{"ts": now_iso(), "msg": "plan created"}],
        }

        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROGRESS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(plan, indent=2))
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(PROGRESS_FILE)
        log("  plan built deterministically (no LLM call)")
        return True
    except (KeyError, TypeError, ValueError) as e:
        log(f"  deterministic planner failed ({type(e).__name__}: {e}) -- falling back to @planner")
        return False
    except OSError as e:
        log(f"  deterministic planner failed writing progress.json ({e}) -- falling back to @planner")
        return False


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """
    Robustly extract a JSON object from arbitrary agent output.

    Handles:
    - Fenced ```json blocks (most common, may have or omit closing fence)
    - Plain ``` blocks
    - Bare JSON with optional prose surrounding it (uses outermost braces)
    - Trailing/leading whitespace, BOM, log lines

    Never crashes on malformed input — calls die() with a useful diagnostic
    showing both the head and tail of the actual output.
    """
    text = text.strip()

    # 1. Try every fenced block, keep the largest valid JSON object found
    candidates: list[str] = []
    for m in _FENCED_JSON_RE.finditer(text):
        candidates.append(m.group(1).strip())

    # 2. Also consider bare brace extraction as a last resort
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(text[first_brace:last_brace + 1])

    # 3. Try parsing each candidate, prefer the one that decodes successfully
    parse_errors: list[str] = []
    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError as exc:
            parse_errors.append(f"  {exc}: {cand[:120]}...")
            continue

    # 4. Nothing parsed — emit a useful diagnostic
    head = text[:600]
    tail = text[-300:] if len(text) > 600 else ""
    msg = (
        f"could not extract JSON from agent output (length={len(text)}).\n"
        f"--- first 600 chars ---\n{head}\n"
    )
    if tail:
        msg += f"--- last 300 chars ---\n{tail}\n"
    if parse_errors:
        msg += "--- parse attempts ---\n" + "\n".join(parse_errors)
    die(msg)


# ---------------------------------------------------------------------------
# Impediment log (M9.4)
# ---------------------------------------------------------------------------

IMPEDIMENTS_PATH = Path("docs/impediments.md")


def _next_impediment_number() -> int:
    if not IMPEDIMENTS_PATH.exists():
        return 1
    try:
        content = IMPEDIMENTS_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 1
    nums = [int(m.group(1)) for m in re.finditer(r"^## IMP-(\d+)", content, re.MULTILINE)]
    return max(nums, default=0) + 1


def append_impediment(title: str, description: str, sprint: Optional[int] = None,
                     suggested_mitigation: str = "(none)") -> Path:
    """M9.4: append an impediment entry to docs/impediments.md."""
    IMPEDIMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    n = _next_impediment_number()
    block = (
        f"\n## IMP-{n:04d}: {title}\n"
        f"Status: open\n"
        f"Identified: {now_iso()[:10]}\n"
        f"Sprint: #{sprint if sprint is not None else 'n/a'}\n"
        f"Description: {description}\n"
        f"Suggested mitigation: {suggested_mitigation}\n"
    )
    if not IMPEDIMENTS_PATH.exists():
        IMPEDIMENTS_PATH.write_text(
            "# Impediments\n\nAppend-only log. Each entry has Status: open|mitigated|closed.\n",
            encoding="utf-8",
        )
    with open(IMPEDIMENTS_PATH, "a", encoding="utf-8") as f:
        f.write(block)
    return IMPEDIMENTS_PATH


def count_open_impediments() -> int:
    if not IMPEDIMENTS_PATH.exists():
        return 0
    try:
        return IMPEDIMENTS_PATH.read_text(encoding="utf-8").lower().count("status: open")
    except (OSError, UnicodeDecodeError):
        return 0


# ---------------------------------------------------------------------------
# Definition of Done (M9.2)
# ---------------------------------------------------------------------------

DOD_PATH = Path("docs/definition-of-done.md")

DEFAULT_DOD = """# Definition of Done

A **story** is "done" when:
- [ ] All acceptance criteria have a passing test (auto-enforced)
- [ ] @guard reports PASS_SCOPE (auto-enforced)
- [ ] Independent test run is GREEN (auto-enforced)
- [ ] @commit completes with a real commit hash (auto-enforced)
- [ ] No new high-severity npm-audit / pip-audit issues (optional)
- [ ] No new TODO/FIXME without a linked story (optional)

A **sprint** is "done" when:
- [ ] All stories in the sprint are completed
- [ ] Production gates pass (clean tree, tests, build) (auto-enforced)
- [ ] No open RFCs (auto-enforced if rfc_auto_apply=true)
- [ ] Retro doc written (auto-enforced)
- [ ] Release notes written (auto-enforced if at least one commit)
"""


def load_definition_of_done() -> str:
    """Return the project's DoD or the default."""
    if DOD_PATH.exists():
        try:
            return DOD_PATH.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return DEFAULT_DOD
    return DEFAULT_DOD


def enforce_definition_of_done(data: dict, sprint: dict) -> tuple[bool, list[str]]:
    """M9.2: check sprint-level DoD items. Returns (all_passed, failures)."""
    failures: list[str] = []

    # All stories in the sprint completed?
    incomplete = [
        sid for sid in sprint.get("story_ids", [])
        if (find_story(data, sid) or {}).get("status") != "completed"
    ]
    if incomplete:
        failures.append(f"Stories not completed: {', '.join(incomplete)}")

    # No open RFCs?
    open_rfcs = find_open_rfcs()
    if open_rfcs:
        failures.append(f"Open RFCs: {', '.join(p.name for p in open_rfcs)}")

    # Retro doc written?
    if not sprint.get("retro_path"):
        failures.append("No retro doc")

    # Release notes written (if any commit)
    has_commits = any(
        (find_story(data, sid) or {}).get("artifacts", {}).get("commit_hash")
        for sid in sprint.get("story_ids", [])
    )
    if has_commits and not sprint.get("release_path"):
        failures.append("No release notes (commits exist)")

    return (len(failures) == 0, failures)


# ---------------------------------------------------------------------------
# RFC pipeline (M9.1)
# ---------------------------------------------------------------------------

EXIT_RFC_NEEDS_HUMAN = 6


def find_open_rfcs() -> list[Path]:
    """Return paths of all RFC files with Status: open in the header."""
    if not RFC_DIR.exists():
        return []
    open_rfcs: list[Path] = []
    for path in sorted(RFC_DIR.glob("*.md")):
        try:
            head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:10])
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"^Status:\s*open\b", head, re.MULTILINE | re.IGNORECASE):
            open_rfcs.append(path)
    return open_rfcs


def parse_rfc_resolution(text: str) -> dict:
    """Parse @architect's response to an open RFC.

    Looks for:
      VERDICT: RFC_RESOLVED | VERDICT: NEEDS_HUMAN
      Recommendation: REOPEN STORY-id | NEW STORY | EDIT_SCOPE STORY-id | NONE
    Returns {verdict, action, target_story_id, raw}.
    """
    upper = text.upper()
    verdict = "UNKNOWN"
    if "VERDICT: RFC_RESOLVED" in upper or "VERDICT:RFC_RESOLVED" in upper:
        verdict = "RFC_RESOLVED"
    elif "VERDICT: NEEDS_HUMAN" in upper or "VERDICT:NEEDS_HUMAN" in upper:
        verdict = "NEEDS_HUMAN"

    action = "NONE"
    target = None
    m = re.search(
        r"(?:Recommendation|Action)\s*:\s*(REOPEN|NEW\s+STORY|EDIT_SCOPE|EDIT\s+SCOPE|NONE|ESCALATE)\s*"
        r"(?:STORY-([A-Za-z0-9_-]+))?",
        text,
        re.IGNORECASE,
    )
    if m:
        action_raw = m.group(1).upper().replace(" ", "_")
        if action_raw == "EDIT_SCOPE":
            action = "EDIT_SCOPE"
        elif action_raw.startswith("NEW"):
            action = "NEW_STORY"
        else:
            action = action_raw
        if m.group(2):
            target = f"STORY-{m.group(2)}"
    return {"verdict": verdict, "action": action, "target_story_id": target, "raw": text[-500:]}


def _rfc_auto_apply_enabled() -> bool:
    if not CONFIG_FILE.exists():
        return True
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    return bool(pipe.get("rfc_auto_apply", True))


def process_rfc_files(data: dict) -> int:
    """M9.1: detect open RFCs, invoke @architect, apply or halt.

    Returns exit code: 0 = all resolved or no RFCs, 6 = needs_human halt.
    """
    open_rfcs = find_open_rfcs()
    if not open_rfcs:
        return 0
    log(f"\n📋 {len(open_rfcs)} open RFC(s) — invoking @architect to resolve")
    needs_human = False
    for rfc_path in open_rfcs:
        try:
            rfc_text = rfc_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            log(f"  ⚠ cannot read {rfc_path}: {e}")
            continue
        prompt = (
            f"Task: propose-resolution\n\n"
            f"RFC file: {rfc_path}\n\n"
            f"## RFC contents\n{rfc_text}\n\n"
            "Propose a single-shot resolution per your contract. End with "
            "VERDICT: RFC_RESOLVED or VERDICT: NEEDS_HUMAN."
        )
        try:
            out = call_agent("architect", prompt, skill="propose-resolution")
        except AgentError as e:
            log(f"  ⚠ @architect failed on {rfc_path}: {e}")
            continue

        # Append response to RFC file
        try:
            with open(rfc_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n## Architect resolution ({now_iso()})\n\n{out}\n")
        except OSError as e:
            log(f"  ⚠ could not append resolution to {rfc_path}: {e}")

        parsed = parse_rfc_resolution(out)
        log(f"  {rfc_path.name}: verdict={parsed['verdict']}, action={parsed['action']}, target={parsed['target_story_id']}")

        # M14.1: UNKNOWN verdict (architect didn't follow output contract) must NOT
        # be treated as auto-resolved. Surface to human review.
        if parsed["verdict"] == "UNKNOWN":
            log(f"  ⚠ {rfc_path.name}: architect did not emit VERDICT line — needs human review")
            needs_human = True
            continue

        if parsed["verdict"] == "NEEDS_HUMAN":
            needs_human = True
            continue

        if not _rfc_auto_apply_enabled():
            needs_human = True
            continue

        # Apply the action
        action = parsed["action"]
        target = parsed["target_story_id"]
        if action == "REOPEN" and target:
            story = find_story(data, target)
            if story and story.get("status") in ("completed", "failed", "blocked"):
                story.setdefault("artifacts", {}).setdefault("previous", []).append({
                    "status": story["status"],
                    "archived_at": now_iso(),
                    "revisit_reason": f"rfc_apply rfc={rfc_path.name}",
                })
                prior_status = story["status"]
                story["status"] = "pending"
                for list_key, status in (
                    ("completed_stories", "completed"),
                    ("failed_stories", "failed"),
                    ("blocked_stories", "blocked"),
                ):
                    if prior_status == status and target in (data.get(list_key) or []):
                        data[list_key].remove(target)
                append_execution_log(data, f"rfc_reopen story={target} rfc={rfc_path.name}")
                log(f"    ↻ reopened {target}")
        elif action == "ESCALATE":
            needs_human = True
            continue
        # NEW_STORY / EDIT_SCOPE / NONE / unknown — log only for v1, no auto-action

        # Mark RFC resolved (replace Status: open with Status: resolved)
        try:
            content = rfc_path.read_text(encoding="utf-8")
            content = re.sub(
                r"^Status:\s*open\b",
                "Status: resolved",
                content,
                flags=re.MULTILINE | re.IGNORECASE,
            )
            rfc_path.write_text(content, encoding="utf-8")
        except OSError as e:
            # M14.2: surface the failure so a stuck-open RFC is visible to the
            # operator. Otherwise the same RFC will be re-triggered next run.
            log(f"  ⚠ could not update {rfc_path.name} status to resolved: {e}")

    persist(data)
    return EXIT_RFC_NEEDS_HUMAN if needs_human else 0


# ---------------------------------------------------------------------------
# Watcher (M8.3) — deterministic pipeline health detection + RFC stub writing
# ---------------------------------------------------------------------------

RFC_DIR = Path("docs/rfc")


def _watcher_config() -> dict:
    defaults = {
        "enabled": True,
        "stall_threshold_sec": 1800,
        "max_blocked": 3,
        "max_retries": 3,
        "cadence_stories": 2,
    }
    if not CONFIG_FILE.exists():
        return defaults
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return defaults
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    return {
        "enabled": bool(pipe.get("watcher_enabled", True)),
        "stall_threshold_sec": int(pipe.get("watcher_stall_threshold_sec", 1800)),
        "max_blocked": int(pipe.get("watcher_max_blocked", 3)),
        "max_retries": int(pipe.get("watcher_max_retries", 3)),
        "cadence_stories": int(pipe.get("watcher_cadence_stories", 2)),
    }


def detect_watcher_signals(data: dict) -> list[dict]:
    """Read-only watcher: returns list of detected signals.

    Each signal is `{type, story_id, detail}` where:
      - type ∈ {stalled_story, cascade, repeated_retries}
      - story_id is the related story (or None for cross-story signals)
      - detail is a one-line human-readable description.
    """
    signals: list[dict] = []
    cfg = _watcher_config()
    if not cfg.get("enabled", True):
        return signals

    # Stalled stories
    threshold = cfg["stall_threshold_sec"]
    now_dt = datetime.now(timezone.utc)
    for story in all_stories(data):
        if story.get("status") != "in_progress":
            continue
        sid = story["id"]
        start_ts: Optional[str] = None
        for entry in reversed(data.get("execution_log", [])):
            msg = entry.get("msg", "")
            if msg.startswith(f"start story={sid}"):
                start_ts = entry.get("ts")
                break
        if not start_ts:
            continue
        try:
            t = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
            age_sec = (now_dt - t).total_seconds()
        except (ValueError, TypeError):  # pragma: no cover — defensive parser
            continue
        if age_sec > threshold:
            signals.append({
                "type": "stalled_story",
                "story_id": sid,
                "detail": f"in_progress for {int(age_sec)}s (threshold {threshold}s)",
            })

    # Cascade (too many blocked)
    blocked = [s for s in all_stories(data) if s.get("status") == "blocked"]
    if len(blocked) > cfg["max_blocked"]:
        signals.append({
            "type": "cascade",
            "story_id": None,
            "detail": f"{len(blocked)} stories blocked (threshold {cfg['max_blocked']})",
        })

    # Repeated retries — count "retry" markers per story in execution_log
    retry_counts: dict[str, int] = {}
    for entry in data.get("execution_log", []):
        msg = entry.get("msg", "")
        if "retry" in msg.lower():
            for token in msg.split():
                if token.startswith("story="):
                    sid = token.split("=", 1)[1]
                    retry_counts[sid] = retry_counts.get(sid, 0) + 1
                    break
    max_retries = cfg["max_retries"]
    for sid, count in retry_counts.items():
        if count > max_retries:
            signals.append({
                "type": "repeated_retries",
                "story_id": sid,
                "detail": f"{count} retries (threshold {max_retries})",
            })

    return signals


def _next_rfc_number() -> int:
    if not RFC_DIR.exists():
        return 1
    nums = []
    for path in RFC_DIR.glob("*.md"):
        m = re.match(r"(\d+)-", path.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def write_rfc_stub(signal: dict) -> Path:
    """Write a docs/rfc/NNNN-<slug>.md stub for a detected signal."""
    RFC_DIR.mkdir(parents=True, exist_ok=True)
    n = _next_rfc_number()
    slug = re.sub(r"[^a-z0-9]+", "-", signal["type"]).strip("-")[:30] or "issue"
    path = RFC_DIR / f"{n:04d}-{slug}.md"
    path.write_text(
        f"# RFC-{n:04d}: {signal['type']}\n\n"
        f"Status: open\n"
        f"Detected: {now_iso()}\n"
        f"Story: {signal.get('story_id') or 'n/a'}\n\n"
        f"## Detail\n{signal['detail']}\n\n"
        f"## Proposed resolution\n_(awaiting @architect via M9.1 RFC pipeline)_\n",
        encoding="utf-8",
    )
    return path


def run_watcher(data: dict) -> int:
    """Run deterministic watcher; write RFC stubs for new signals. Returns count written."""
    cfg = _watcher_config()
    if not cfg.get("enabled", True):
        return 0
    signals = detect_watcher_signals(data)
    if not signals:
        return 0
    log(f"  ⚠ watcher detected {len(signals)} signal(s)")
    written = 0
    for sig in signals:
        # Skip duplicate signals (one per story-id+type combo per session)
        existing_match = False
        if RFC_DIR.exists():
            for existing in RFC_DIR.glob("*.md"):
                try:
                    content = existing.read_text(encoding="utf-8")
                except OSError:  # pragma: no cover — defensive read
                    continue
                if (sig["type"] in content
                        and "Status: open" in content
                        and (sig.get("story_id") or "n/a") in content):
                    existing_match = True
                    break
        if existing_match:
            log(f"    (skip — existing open RFC for {sig['type']} {sig.get('story_id')})")
            continue
        path = write_rfc_stub(sig)
        log(f"    📄 {path}")
        written += 1
    return written


# ---------------------------------------------------------------------------
# ADR infrastructure (M7.2)
# ---------------------------------------------------------------------------

ADR_DIR = Path("docs/adr")


def _adr_context_max_entries() -> int:  # pragma: no cover — config helper rarely exercised
    if not CONFIG_FILE.exists():
        return 5
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 5
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    try:
        return int(pipe.get("adr_context_max_entries", 5))
    except (TypeError, ValueError):
        return 5


def load_recent_adrs(max_entries: Optional[int] = None) -> str:
    """Return last N accepted ADR titles + decision summaries as a text block."""
    if not ADR_DIR.exists():
        return ""
    n = max_entries if max_entries is not None else _adr_context_max_entries()
    if n <= 0:
        return ""
    blocks: list[str] = []
    for path in sorted(ADR_DIR.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover — defensive read
            continue
        # Only include accepted ADRs
        first_lines = "\n".join(content.splitlines()[:10]).lower()
        if "status: accepted" not in first_lines:
            continue
        lines = content.splitlines()
        title = next((l for l in lines if l.startswith("# ")), path.stem).strip("# ").strip()
        decision = ""
        capturing = False
        for line in lines:
            if line.startswith("## Decision") or line.startswith("# Decision"):
                capturing = True
                continue
            if capturing:
                if line.startswith("#"):
                    break
                if line.strip():
                    decision += line.strip() + " "
                    if len(decision) > 200:  # pragma: no cover — truncate guard for long ADRs
                        break
        blocks.append(f"- {title}: {decision.strip()[:200]}")
    return "\n".join(blocks[-n:])


def next_adr_number() -> int:
    """Next monotonic ADR number (4-digit)."""
    if not ADR_DIR.exists():
        return 1
    nums = []
    for path in ADR_DIR.glob("*.md"):
        m = re.match(r"(\d+)-", path.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def cmd_rfc(args: argparse.Namespace) -> int:
    """M9.1: manually process open RFC files."""
    data = read_progress()
    rc = process_rfc_files(data)
    if rc == EXIT_RFC_NEEDS_HUMAN:
        log("\n⚠ Some RFCs require human review. Inspect docs/rfc/ for status.")
    else:
        log("\n✓ All RFCs resolved (or none open).")
    return rc


def cmd_agent(args: argparse.Namespace) -> int:
    """M10.6: ad-hoc agent invocation with a named skill.

    Useful for testing personas + skills outside the sprint pipeline.
    """
    agent_name = args.agent
    skill_id = args.skill
    prompt = args.prompt

    # Validate the agent + skill before invoking
    try:
        from runners import (
            check_skill_permissions as _check_perms,
            parse_agent_file as _parse_agent,
            resolve_skill_for_agent as _resolve,
        )
    except ImportError:  # pragma: no cover — defensive
        die("runners module unavailable")

    try:
        agent_def = _parse_agent(agent_name)
    except FileNotFoundError as e:
        die(f"Agent not found: {e}")

    if skill_id:
        inline, skill_file = _resolve(agent_def, skill_id)
        if inline is None and skill_file is None:
            die(
                f"Skill {skill_id!r} not declared by @{agent_name} "
                f"(inline skills: {[s.id for s in agent_def.skills]}, "
                f"imports: {agent_def.imports})"
            )
        if skill_file is not None:
            ok, conflicts = _check_perms(agent_def, skill_id)
            if not ok:
                die("Permission conflicts:\n  - " + "\n  - ".join(conflicts))

    log(f"\n▶ ad-hoc invocation: @{agent_name}" + (f" --skill {skill_id}" if skill_id else ""))
    try:
        output = call_agent(agent_name, prompt, skill=skill_id)
    except AgentError as e:
        log(f"\n✗ @{agent_name} failed: {e}")
        return 1

    print("\n--- agent output ---")
    print(output)
    return 0


def cmd_refine(args: argparse.Namespace) -> int:
    """M7.3: split a large story into 2-4 smaller stories via @architect."""
    story_id = args.story
    data = read_progress()
    story = find_story(data, story_id)
    if not story:
        die(f"Story {story_id} not found in progress.json")

    tasks = story.get("tasks", [])
    complexity = story.get("estimated_complexity", "?")
    if complexity != "large":
        log(f"⚠ {story_id} is {complexity!r} complexity (refining anyway since user asked)")
    if len(tasks) < 3:
        log(f"⚠ {story_id} has only {len(tasks)} tasks — splitting may not be useful")

    prompt = (
        f"Task: refine-epic\n\n"
        f"Story to refine: {story_id}\n"
        f"Title: {story.get('title', '?')}\n"
        f"Complexity: {complexity}\n"
        f"Acceptance criteria ({len(story.get('acceptance_criteria', []))}):\n"
        + "\n".join(f"  - {ac}" for ac in story.get('acceptance_criteria', []))
        + f"\n\nTasks ({len(tasks)}):\n"
        + "\n".join(
            f"  - {t.get('id', '?')}: {', '.join(t.get('files_to_touch', []))} "
            f"({t.get('type', '?')})"
            for t in tasks
        )
        + "\n\nSplit this story into 2-4 smaller stories. Each new story should cover "
        "a coherent subset of the ACs (the UNION of new ACs must cover all original ACs). "
        "Write the new story blocks back into the appropriate spec file under "
        "docs/specs/epics/. Mark the original story's superseded_by field. "
        "End with VERDICT: EPIC_REFINED or VERDICT: REFINEMENT_REJECTED."
    )

    try:
        out = call_agent("architect", prompt)
    except AgentError as e:
        die(f"@architect failed: {e}")

    upper = out.upper()
    if "VERDICT: REFINEMENT_REJECTED" in upper or "VERDICT:REFINEMENT_REJECTED" in upper:
        log(f"@architect rejected refinement:\n{out[-400:]}")
        return 0
    if "VERDICT: EPIC_REFINED" not in upper and "VERDICT:EPIC_REFINED" not in upper:
        die(f"@architect did not emit a refinement verdict. Last 500 chars:\n{out[-500:]}")

    # Validate spec is still well-formed after the edit
    if validate_specs is None:
        die("spec_parser unavailable — cannot validate refined spec")
    report = validate_specs(Path("."))
    if not report.ok:
        log("\n✗ Refined spec failed validation:")
        log(report.render())
        return 1

    log(f"\n✓ {story_id} refined; spec re-validated cleanly")
    log("  Re-run `aa-orchestrator develop --force` to rebuild the plan with the new stories.")
    return 0


def cmd_adr(args: argparse.Namespace) -> int:
    """M7.2: propose an ADR via @architect."""
    question = args.question
    story_id = getattr(args, "story", None)
    ADR_DIR.mkdir(parents=True, exist_ok=True)
    n = next_adr_number()
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:50] or "decision"
    expected_path = ADR_DIR / f"{n:04d}-{slug}.md"

    prompt = (
        f"Task: propose-adr\n\n"
        f"ADR number: {n:04d}\n"
        f"Suggested filename: {expected_path}\n"
        f"Question: {question}\n"
    )
    if story_id:
        prompt += f"Related story: {story_id}\n"
    prompt += (
        f"\nWrite the ADR file at the suggested filename following the "
        "Michael Nygard template. Status starts as `proposed`. End with VERDICT: ADR_PROPOSED."
    )

    try:
        out = call_agent("architect", prompt)
    except AgentError as e:
        die(f"@architect failed: {e}")

    if "VERDICT: ADR_PROPOSED" not in out and "VERDICT:ADR_PROPOSED" not in out:
        die(f"@architect did not emit VERDICT: ADR_PROPOSED. Last 500 chars:\n{out[-500:]}")

    # Find the actual file (agent may use different slug)
    candidates = sorted(ADR_DIR.glob(f"{n:04d}-*.md"))
    if not candidates:
        die(f"@architect did not write an ADR with number {n:04d}")
    adr_path = candidates[0]
    log(f"\n✓ ADR-{n:04d} proposed: {adr_path}")
    return 0


# ---------------------------------------------------------------------------
# Sprint engine (M6) — story-count-boxed sprint cycle
# ---------------------------------------------------------------------------

STORY_POINTS = {"small": 1, "medium": 3, "large": 5}


def _sprint_size() -> int:
    """Default 5 stories per sprint, configurable via pipeline.sprint_size."""
    if not CONFIG_FILE.exists():
        return 5
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):  # pragma: no cover — defensive
        return 5
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    try:
        return int(pipe.get("sprint_size", 5))
    except (TypeError, ValueError):
        return 5


def story_points(story: dict) -> int:
    """Map estimated_complexity to integer points (small=1, medium=3, large=5)."""
    return STORY_POINTS.get(story.get("estimated_complexity", "medium"), 3)


def ensure_sprint_state(data: dict) -> dict:
    """Lazy-initialize sprints[] and current_sprint fields if missing."""
    if "sprints" not in data:
        data["sprints"] = []
    if "current_sprint" not in data:
        data["current_sprint"] = 0  # 0 means no sprint started yet
    return data


def current_sprint(data: dict) -> Optional[dict]:
    """Return the active sprint dict or None if no sprint is in progress."""
    sprints = data.get("sprints", [])
    if not sprints:
        return None
    last = sprints[-1]
    if last.get("status") == "in_progress":
        return last
    return None


def sprint_for_story(data: dict, story_id: str) -> Optional[int]:
    """Which sprint number contains this story? None if unscheduled."""
    for sprint in data.get("sprints", []):
        if story_id in sprint.get("story_ids", []):
            return sprint["number"]
    return None


def compute_sprint_velocity(data: dict, sprint_number: int) -> int:
    """Sum story points for completed stories in a given sprint."""
    sprint = next((s for s in data.get("sprints", []) if s["number"] == sprint_number), None)
    if not sprint:
        return 0
    total = 0
    for sid in sprint.get("story_ids", []):
        story = find_story(data, sid)
        if story and story.get("status") == "completed":
            total += story_points(story)
    return total


def rolling_velocity_avg(data: dict, last_n: int = 3) -> float:
    """Average velocity over the last N completed sprints."""
    completed = [s for s in data.get("sprints", []) if s.get("status") == "completed"]
    if not completed:
        return 0.0
    recent = completed[-last_n:]
    total = sum(s.get("velocity_points", 0) for s in recent)
    return total / len(recent) if recent else 0.0


# ---------------------------------------------------------------------------
# Cross-story memory (M2.3) — auto-maintained docs/specs/PROJECT_CONTEXT.md
# ---------------------------------------------------------------------------

PROJECT_CONTEXT_FILE = Path("docs/specs/PROJECT_CONTEXT.md")


def _project_context_enabled() -> bool:
    if not CONFIG_FILE.exists():
        return True
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    return bool(pipe.get("project_context_enabled", True))


def _project_context_max_entries() -> int:
    if not CONFIG_FILE.exists():
        return 10
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 10
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    try:
        return int(pipe.get("project_context_max_entries", 10))
    except (TypeError, ValueError):
        return 10


def update_project_context(data: dict, story: dict) -> None:
    """Append a summary block for a completed story.

    Best-effort: failures are logged but do not interrupt the pipeline.
    """
    if not _project_context_enabled():
        return
    try:
        PROJECT_CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not PROJECT_CONTEXT_FILE.exists():
            PROJECT_CONTEXT_FILE.write_text(
                "# Project Context\n\n"
                "Auto-maintained by autonomous-agents. Each completed story appends "
                "a block below; agents see the most recent N entries.\n",
                encoding="utf-8",
            )
        arts = story.get("artifacts") or {}
        impl_files = arts.get("implementation_files", [])
        test_files = arts.get("test_files", [])
        files = sorted(set((impl_files or []) + (test_files or [])))
        commit_hash = arts.get("commit_hash", "")
        block = [
            f"\n## {story['id']} ({now_iso()})",
            f"- Title: {story.get('title', '?')}",
            f"- Files: {', '.join(files) if files else '(none)'}",
            f"- ACs: {len(story.get('acceptance_criteria', []))}",
        ]
        if commit_hash:
            block.append(f"- Commit: `{commit_hash}`")
        with open(PROJECT_CONTEXT_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(block) + "\n")
    except Exception as e:
        log(f"  ⚠ project context update failed: {type(e).__name__}: {e}")


def load_project_context(max_entries: Optional[int] = None) -> str:
    """Return a string of the last N story-summary blocks, or empty if disabled/missing."""
    if not _project_context_enabled():
        return ""
    if not PROJECT_CONTEXT_FILE.exists():
        return ""
    try:
        text = PROJECT_CONTEXT_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    n = max_entries if max_entries is not None else _project_context_max_entries()
    if n <= 0:
        return ""
    # Split on "## STORY-" headings; keep only the last N
    parts = re.split(r"(?=^## STORY-)", text, flags=re.MULTILINE)
    entries = [p for p in parts if p.startswith("## STORY-")]
    if not entries:
        return ""
    selected = entries[-n:]
    return "\n".join(selected).strip()


# ---------------------------------------------------------------------------
# Worktree isolation (M3.1) — per-story git worktree, optional cleanup/merge
# ---------------------------------------------------------------------------

def _in_git_repo() -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _worktree_enabled() -> bool:
    """Check `pipeline.worktree_isolation` (default True)."""
    if not CONFIG_FILE.exists():
        return True
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    return bool(pipe.get("worktree_isolation", True))


def _auto_merge_enabled() -> bool:
    if not CONFIG_FILE.exists():
        return True
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    return bool(pipe.get("auto_merge", True))


def _cleanup_worktrees_enabled() -> bool:
    if not CONFIG_FILE.exists():
        return True
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    return bool(pipe.get("cleanup_worktrees", True))


def setup_worktree(data: dict, story: dict) -> Optional[str]:
    """Create or reuse a worktree for this story. Returns absolute path or None
    if worktrees are disabled / not in a git repo (graceful degradation).
    """
    if not _worktree_enabled():
        return None
    if not _in_git_repo():
        log("  ⚠ not in a git repo — worktree isolation disabled for this run")
        return None

    sid = story["id"]
    epic = epic_for_story(data, sid)
    epic_id = epic["id"] if epic else "unknown-epic"
    title = story.get("title", "")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    branch = f"feat/{epic_id}/{sid}" + (f"-{slug}" if slug else "")
    worktree_path = (Path(".opencode/worktrees") / sid).resolve()

    if worktree_path.exists():
        log(f"  ↻ reusing existing worktree: {worktree_path}")
        return str(worktree_path)

    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    branch_check = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        capture_output=True, text=True, check=False,
    )
    branch_exists = branch_check.returncode == 0

    log(f"  → creating worktree at {worktree_path} on branch {branch}")
    if branch_exists:  # pragma: no cover — branch-reuse path
        cmd = ["git", "worktree", "add", str(worktree_path), branch]
    else:
        cmd = ["git", "worktree", "add", "-b", branch, str(worktree_path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        log(f"  ⚠ worktree create failed: {e.stderr[:300]} — falling back to in-place execution")
        return None

    return str(worktree_path)


def teardown_worktree(story: dict) -> None:
    """After terminal status, merge branch back to main (if auto_merge) and prune."""
    arts = story.get("artifacts") or {}
    worktree_path = arts.get("worktree_path")
    if not worktree_path:
        return
    wt = Path(worktree_path)
    if not wt.exists():
        return

    branch = arts.get("branch")
    commit_hash = arts.get("commit_hash")

    if _auto_merge_enabled() and branch and commit_hash:
        log(f"  → merging {branch} into current branch (fast-forward)")
        proc = subprocess.run(
            ["git", "merge", "--ff-only", branch],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            log(f"  ⚠ fast-forward merge of {branch} failed: {proc.stderr.strip()[:200]}")
            log(f"  branch is preserved at {branch} for manual merge; worktree NOT removed")
            return

    if _cleanup_worktrees_enabled():
        log(f"  → removing worktree {worktree_path}")
        proc = subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            log(f"  ⚠ worktree remove failed (non-fatal): {proc.stderr.strip()[:200]}")


# ---------------------------------------------------------------------------
# Per-story pipeline
# ---------------------------------------------------------------------------

def run_story(data: dict, story: dict) -> dict:  # pragma: no cover — integration-level orchestration, tested via run_loop stubs
    sid = story["id"]
    log(
        f"\n▶ Starting {sid}: {story['title']} "
        f"(wave {story.get('execution_wave', 1)}, "
        f"complexity {story.get('estimated_complexity', '?')})"
    )

    # 3a. mark in_progress
    story["status"] = "in_progress"
    data["current_story_id"] = sid
    append_execution_log(data, f"start story={sid}")
    data = persist(data)
    story = find_story(data, sid)

    # 3b. design review (no worktree yet — read-only review of the spec)
    review_outcome, prior_findings = run_review_loop(story, mode="design", impl_files=None)
    if review_outcome == "BLOCK":
        return finalize_story(data, sid, status="blocked", reason="design_review_block")

    # M3.1: create isolated worktree for the implementation phases
    worktree_cwd = setup_worktree(data, story)
    if worktree_cwd:
        story = find_story(data, sid)
        story.setdefault("artifacts", {})["worktree_path"] = worktree_cwd
        data = persist(data)

    # 3c. test writing (in worktree if available)
    story["status"] = "review_pass"
    data = persist(data)
    test_outcome = run_test_writer(story, prior_findings, cwd=worktree_cwd)
    if test_outcome["status"] == "FAIL":
        return finalize_story(data, sid, status="blocked", reason=f"test_writer:{test_outcome['detail']}")
    story = find_story(data, sid)
    story["status"] = "test_written"
    arts = story.setdefault("artifacts", {})
    arts["test_files"] = test_outcome["test_files"]
    arts["criterion_test_mapping"] = test_outcome["criterion_test_mapping"]
    data = persist(data)

    # 3d. implementation + verification (in worktree)
    story = find_story(data, sid)
    impl_outcome = run_implementation_with_verification(story, cwd=worktree_cwd)
    if impl_outcome["status"] != "GREEN_VERIFIED":
        return finalize_story(data, sid, status="failed", reason=impl_outcome["detail"])
    story = find_story(data, sid)
    story["status"] = "implemented"
    story.setdefault("artifacts", {})["implementation_files"] = impl_outcome["files"]
    story["artifacts"]["test_run_evidence"] = impl_outcome["test_evidence_hash"]
    data = persist(data)

    # 3e. post-implementation review (in worktree so reviewer sees impl)
    story = find_story(data, sid)
    post_outcome, _ = run_review_loop(story, mode="implementation", impl_files=impl_outcome["files"], cwd=worktree_cwd)
    if post_outcome == "BLOCK":
        return finalize_story(data, sid, status="failed", reason="post_impl_review_block")

    # 3f. commit (in worktree)
    commit_result = run_commit(story, cwd=worktree_cwd)
    if not commit_result["ok"]:
        # Closes H2: commit failure → fail (NOT complete)
        return finalize_story(data, sid, status="failed", reason=f"commit:{commit_result['detail']}")
    story = find_story(data, sid)
    story.setdefault("artifacts", {})["commit_hash"] = commit_result["hash"]
    story["artifacts"]["branch"] = commit_result["branch"]
    # M13.6: persist commit_hash + branch BEFORE finalize_story re-reads from disk,
    # otherwise these mutations are silently lost (finalize_story calls read_progress()
    # internally which discards in-memory state).
    data = persist(data)
    data = finalize_story(data, sid, status="completed", reason=None)

    # M3.1: merge branch back to main + clean up worktree
    final_story = find_story(data, sid)
    if final_story:
        teardown_worktree(final_story)
    return data


# --- review loop ----------------------------------------------------------

def run_review_loop(
    story: dict, mode: str, impl_files: Optional[list[str]], cwd: Optional[str] = None
) -> tuple[str, dict]:
    """
    Runs @check and @simplify with proper CONVERGENCE semantics.
    Closes C3 + M2: CONVERGENCE only proceeds if previous verdict was PASS.
    Closes M2 specifically: prior findings are passed back into cycle 2.
    """
    prior_check = ""
    prior_simplify = ""
    final_check_verdict = "UNKNOWN"
    final_simplify_verdict = "UNKNOWN"

    for cycle in range(1, MAX_REVIEW_CYCLES + 1):
        log(f"  Review cycle {cycle}/{MAX_REVIEW_CYCLES} ({mode})")
        check_prompt = build_review_prompt("check", story, mode, impl_files, prior_check)
        simplify_prompt = build_review_prompt("simplify", story, mode, impl_files, prior_simplify)

        check_out = call_agent_with_delegation(
            "check", check_prompt,
            expected_verdicts=["PASS", "NEEDS_CHANGES", "BLOCK", "SIMPLIFY"],
            cwd=cwd,
        )
        simplify_out = call_agent_with_delegation(
            "simplify", simplify_prompt,
            expected_verdicts=["PASS", "NEEDS_CHANGES", "BLOCK", "SIMPLIFY"],
            cwd=cwd,
        )

        check_verdict = parse_verdict(check_out)
        simplify_verdict = parse_verdict(simplify_out)
        log(f"    @check: {check_verdict}   @simplify: {simplify_verdict}")

        check_pass = is_pass(check_verdict, prior_was_pass=is_pass(final_check_verdict))
        simplify_pass = is_pass(simplify_verdict, prior_was_pass=is_pass(final_simplify_verdict))

        if "BLOCK" in check_verdict and "CONVERGENCE" not in check_verdict:
            return "BLOCK", {"check": check_out, "simplify": simplify_out}

        if check_pass and simplify_pass:
            return "PASS", {"check": check_out, "simplify": simplify_out}

        # Convergence on non-PASS verdict means we're stuck — don't auto-accept.
        if "CONVERGENCE" in check_verdict and not is_pass(final_check_verdict):  # pragma: no cover — multi-cycle convergence
            log(
                f"    ⛔ @check converged on non-PASS verdict ({check_verdict}) "
                "— not accepting"
            )
            if "BLOCK" in final_check_verdict:
                return "BLOCK", {"check": check_out, "simplify": simplify_out}

        prior_check = check_out
        prior_simplify = simplify_out
        final_check_verdict = check_verdict
        final_simplify_verdict = simplify_verdict

    if "BLOCK" in final_check_verdict or "BLOCK" in final_simplify_verdict:
        return "BLOCK", {"check": prior_check, "simplify": prior_simplify}
    log("    Proceeding with NEEDS_CHANGES findings — passed to @make as constraints")
    return "PROCEED_WITH_WARN", {"check": prior_check, "simplify": prior_simplify}


def build_review_prompt(
    agent: str, story: dict, mode: str, impl_files: Optional[list[str]], prior: str
) -> str:
    parts = [f"Review {mode} for story {story['id']}: {story['title']}"]
    parts.append(f"Description: {story.get('description', '')}")
    parts.append("Acceptance Criteria:")
    for c in story.get("acceptance_criteria", []):
        parts.append(f"  - {c}")
    if impl_files:
        parts.append(f"Files to review: {', '.join(impl_files)}")
    if prior:
        parts.append("\n--- PREVIOUS REVIEW (for CONVERGENCE detection) ---")
        parts.append(prior)
        parts.append(
            "Compare your current findings to the previous review. "
            "If identical, append [CONVERGENCE] to your verdict."
        )
    return "\n".join(parts)


def parse_verdict(text: str) -> str:
    upper = text.upper()
    if "VERDICT:" in upper:
        tail = upper.split("VERDICT:", 1)[1].splitlines()[0].strip()
        # M22: strip markdown formatting that leaks in from `**Verdict:**` /
        # `_Verdict:_` patterns (real-world regression with MiniMax-M2.7 via
        # OpenCode — Claude normalizes these away, MiniMax emits them verbatim).
        tail = tail.strip("*").strip("_").strip()
    else:
        tail = "UNKNOWN"
    # M22: only append " [CONVERGENCE]" if the marker isn't already in the tail
    # (prevents the historical "NEEDS_CHANGES [CONVERGENCE] [CONVERGENCE]" doubling).
    is_convergence = "CONVERGENCE" in upper
    if is_convergence and "CONVERGENCE" not in tail:
        return tail + " [CONVERGENCE]"
    return tail


def is_pass(verdict: str, prior_was_pass: bool = False) -> bool:
    upper = verdict.upper()
    if "BLOCK" in upper:
        return False
    if "PASS" in upper and "NEEDS" not in upper:
        return True
    if "[CONVERGENCE]" in upper and prior_was_pass:
        return True
    return False


# --- test writer ---------------------------------------------------------

def run_test_writer(story: dict, prior_findings: dict, cwd: Optional[str] = None) -> dict:
    log("  Writing tests (@test)")
    review_constraints = (
        prior_findings.get("check", "") + "\n" + prior_findings.get("simplify", "")
    ).strip()
    prompt = (
        f"Write failing tests for story {story['id']}: {story['title']}\n"
        f"Description: {story.get('description', '')}\n"
        "Acceptance Criteria:\n"
        + "\n".join(f"  - {c}" for c in story.get("acceptance_criteria", []))
        + f"\n\nReview constraints:\n{review_constraints}\n"
        "Output the criterion→test mapping table — every acceptance criterion must "
        "map to ≥1 test."
    )
    for attempt in range(MAX_TEST_RETRIES + 1):
        out = call_agent_with_delegation(
            "test", prompt,
            expected_verdicts=["RED_VERIFIED", "INCOMPLETE_COVERAGE", "ENV_BROKEN"],
            cwd=cwd,
        )
        if "RED_VERIFIED" in out and validate_criterion_coverage(out, story):
            files = extract_test_files(out)
            mapping = extract_criterion_mapping(out)
            if not files:
                raise AgentError(
                    "test",
                    "RED_VERIFIED reported but no test files extracted — "
                    "@test output is missing the required `- `path/to/test`` block",
                )
            if not mapping:
                raise AgentError(
                    "test",
                    "RED_VERIFIED reported but criterion→test mapping table is missing or empty — "
                    "agent did not follow output contract",
                )
            return {"status": "OK", "test_files": files, "criterion_test_mapping": mapping}
        if "INCOMPLETE_COVERAGE" in out:
            log(f"    ⚠ INCOMPLETE_COVERAGE — retry {attempt + 1}/{MAX_TEST_RETRIES}")
            continue
        if "ENV_BROKEN" in out:
            return {"status": "FAIL", "detail": "env_broken"}
    return {"status": "FAIL", "detail": "no_red_verified_after_retries"}


def validate_criterion_coverage(test_report: str, story: dict) -> bool:
    """Closes H5: every acceptance criterion must appear in the mapping."""
    criteria = [c.strip().lower() for c in story.get("acceptance_criteria", [])]
    if not criteria:
        return True
    report_lower = test_report.lower()
    missing = [c for c in criteria if not criterion_appears(c, report_lower)]
    if missing:
        log(f"    ⛔ criteria not covered in @test mapping: {missing}")
        return False
    return True


def criterion_appears(criterion: str, report_lower: str) -> bool:
    words = [w for w in criterion.split() if len(w) > 3][:4]
    return all(w in report_lower for w in words)


def extract_test_files(out: str) -> list[str]:
    files = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("- `") and "`" in line[3:]:
            path = line[3:line.index("`", 3)]
            files.append(path)
    return files


def extract_criterion_mapping(out: str) -> dict:
    mapping: dict[str, list[str]] = {}
    in_table = False
    for line in out.splitlines():
        if "Acceptance Criterion" in line and "Covering Test" in line:
            in_table = True
            continue
        if in_table:
            if "---" in line:
                continue
            if not line.strip().startswith("|"):
                in_table = False
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2:
                mapping[cells[0]] = [t.strip(" `") for t in cells[1].split(",")]
    return mapping


# --- implementation + verification ---------------------------------------

def run_implementation_with_verification(story: dict, cwd: Optional[str] = None) -> dict:
    """Closes C1 (scope) + C2 (independent test verification)."""
    last_detail = ""
    for attempt in range(MAX_MAKE_RETRIES + 1):
        log(f"  Implementing (@make), attempt {attempt + 1}/{MAX_MAKE_RETRIES + 1}")
        make_prompt = build_make_prompt(story, retry_constraint=last_detail)
        make_out = call_agent_with_delegation(
            "make", make_prompt,
            expected_verdicts=["Status: GREEN", "**Status:** GREEN", "BLOCKED", "PARTIAL"],
            cwd=cwd,
        )
        if "Status: GREEN" in make_out or "**Status:** GREEN" in make_out:
            make_status = "GREEN"
        elif "BLOCKED" in make_out:
            make_status = "BLOCKED"
        else:
            make_status = "PARTIAL"
        log(f"    @make self-reported: {make_status}")

        if make_status == "BLOCKED":
            last_detail = "make_blocked"
            continue
        if make_status == "PARTIAL":
            # PARTIAL_GREEN never proceeds (closes H5 implication)
            last_detail = "make_partial_green"
            continue

        # @guard verification (closes C1)
        log("  Verifying scope (@guard)")
        guard_prompt = build_guard_prompt(story)
        guard_out = call_agent_with_delegation(
            "guard", guard_prompt,
            expected_verdicts=["PASS_SCOPE", "FAIL_OUT_OF_SCOPE", "FAIL_NOTHING_CHANGED"],
            cwd=cwd,
        )
        if "FAIL_OUT_OF_SCOPE" in guard_out:
            last_detail = "guard_out_of_scope"
            log(
                "    ⛔ @guard reported out-of-scope writes "
                "— retrying @make with stricter constraint"
            )
            continue
        if "FAIL_NOTHING_CHANGED" in guard_out:
            last_detail = "guard_nothing_changed"
            log(
                "    ⛔ @guard reported no changes despite @make GREEN "
                "— likely hallucination"
            )
            continue

        # Independent test re-run (closes C2)
        test_files = (story.get("artifacts") or {}).get("test_files", [])
        ok, output = run_tests_independently(test_files, cwd=cwd)
        if not ok:
            last_detail = "independent_test_run_failed"
            log("    ⛔ orchestrator-run tests failed — @make's GREEN claim is invalid")
            continue

        impl_files = extract_impl_files(make_out)
        return {
            "status": "GREEN_VERIFIED",
            "files": impl_files,
            "test_evidence_hash": hash_text(output),
            "detail": "ok",
        }
    return {"status": "FAIL", "detail": last_detail or "max_retries_exceeded"}


def build_make_prompt(story: dict, retry_constraint: str = "") -> str:
    arts = story.get("artifacts") or {}
    test_files = arts.get("test_files", [])
    files_to_touch: list[str] = []
    for t in story.get("tasks", []):
        files_to_touch.extend(t.get("files_to_touch", []))
    parts = [
        f"Story: {story['id']} — {story['title']}",
        f"Description: {story.get('description', '')}",
        "Acceptance Criteria:",
    ]
    for c in story.get("acceptance_criteria", []):
        parts.append(f"  - {c}")
    parts.append(
        f"Files to touch: "
        f"{', '.join(files_to_touch) if files_to_touch else '[none declared]'}"
    )
    parts.append(f"Test files (do not modify): {', '.join(test_files)}")
    if retry_constraint:
        parts.append(f"\nRETRY CONSTRAINT (previous attempt failed: {retry_constraint}):")
        parts.append("- Strictly stay within Files to touch")
        parts.append("- Do not write to .env, .git, .opencode, or any unlisted path")
    return "\n".join(parts)


def build_guard_prompt(story: dict) -> str:
    arts = story.get("artifacts") or {}
    test_files = arts.get("test_files", [])
    files_to_touch: list[str] = []
    for t in story.get("tasks", []):
        files_to_touch.extend(t.get("files_to_touch", []))
    return (
        f"Story: {story['id']}\n"
        f"Declared files_to_touch:\n"
        + "\n".join(f"  - {f}" for f in files_to_touch)
        + f"\nTest files (allowed):\n"
        + "\n".join(f"  - {f}" for f in test_files)
        + "\n\nVerify the working tree contains only changes to declared files + test files."
    )


def extract_impl_files(make_out: str) -> list[str]:
    files = []
    in_impl = False
    for line in make_out.splitlines():
        if "Implementation:" in line:
            in_impl = True
            continue
        if in_impl:
            if line.startswith("**") or line.startswith("##"):
                in_impl = False
                continue
            if line.strip().startswith("- ") and "`" in line:
                path = line[line.index("`") + 1: line.rindex("`")]
                if path:
                    files.append(path)
    return files


# --- commit --------------------------------------------------------------

def run_commit(story: dict, cwd: Optional[str] = None) -> dict:
    log("  Committing (@commit)")
    arts = story.get("artifacts") or {}
    files_to_commit = list(set(arts.get("test_files", []) + arts.get("implementation_files", [])))
    epic = epic_for_story(read_progress(), story["id"])
    epic_id = epic["id"] if epic else "unknown-epic"
    prompt = (
        f"Story: {story['id']}\n"
        f"Epic: {epic_id}\n"
        f"Title: {story['title']}\n"
        f"Description: {story.get('description', '')}\n"
        "Files to commit:\n"
        + "\n".join(f"  - {f}" for f in files_to_commit)
        + f"\nAcceptance criteria met: {len(story.get('acceptance_criteria', []))}\n"
        "Branch pattern: feat/{epic-id}/{story-id}-{slug}\n"
    )
    out = call_agent_with_delegation(
        "commit", prompt,
        expected_verdicts=[
            "Status: COMMITTED",
            "**Status:** COMMITTED",
            "FAIL_NO_REPO",
            "FAIL_BRANCH_EXISTS",
            "FAIL_FILE_UNCHANGED",
            "FAIL_NOTHING_STAGED",
            "FAIL_HOOK_REJECTED",
        ],
        cwd=cwd,
    )
    if "Status: COMMITTED" in out or "**Status:** COMMITTED" in out:
        commit_hash = extract_commit_hash(out)
        if not commit_hash:
            raise AgentError(
                "commit",
                "COMMITTED reported but no commit hash extracted — "
                "agent output is missing the `Commit hash: <sha>` line",
            )
        return {
            "ok": True,
            "hash": commit_hash,
            "branch": extract_branch(out),
            "detail": "committed",
        }
    detail = "unknown"
    for marker in (
        "FAIL_NO_REPO",
        "FAIL_BRANCH_EXISTS",
        "FAIL_FILE_UNCHANGED",
        "FAIL_NOTHING_STAGED",
        "FAIL_HOOK_REJECTED",
    ):
        if marker in out:
            detail = marker.lower()
            break
    return {"ok": False, "hash": None, "branch": None, "detail": detail}


def extract_commit_hash(out: str) -> Optional[str]:
    for line in out.splitlines():
        if "Commit hash:" in line:
            tail = line.split(":", 1)[1].strip().strip("`*")
            if tail:
                return tail
    return None


def extract_branch(out: str) -> Optional[str]:
    for line in out.splitlines():
        if "Branch:" in line and "branch:" not in line.lower().split("branch:")[0]:
            tail = line.split(":", 1)[1].strip().strip("`*")
            if tail:
                return tail
    return None


# --- finalize ------------------------------------------------------------

def finalize_story(data: dict, sid: str, status: str, reason: Optional[str]) -> dict:
    data = read_progress()
    story = find_story(data, sid)
    if story is None:
        die(f"finalize_story: cannot find {sid}")
    from_status = story.get("status")
    story["status"] = status
    # M24: stamp the terminal-transition time + store failure_reason for visibility
    story["completed_at"] = now_iso()
    if status in ("failed", "blocked") and reason:
        story["failure_reason"] = reason
    # M21: emit a status-change event for A2A subscribers
    if _EVENT_BUS is not None:
        try:
            _EVENT_BUS.notify("event/story_status_changed", {
                "story_id": sid,
                "from": from_status,
                "to": status,
                "reason": reason,
                "commit_hash": (story.get("artifacts") or {}).get("commit_hash"),
            })
        except Exception:  # pragma: no cover — defensive
            pass
    if status == "completed":
        data.setdefault("completed_stories", []).append(sid)
        if data.get("current_story_id") == sid:
            data["current_story_id"] = None
        log(f"✓ Completed {sid}")
        append_execution_log(data, f"completed story={sid}")
        # M2.3: record the completion in project context for future stories
        update_project_context(data, story)
    elif status == "failed":
        data.setdefault("failed_stories", []).append(sid)
        if data.get("current_story_id") == sid:
            data["current_story_id"] = None
        log(f"✗ Failed {sid}: {reason}")
        append_execution_log(data, f"failed story={sid} reason={reason}")
        cascaded = cascade_fail(data, sid, reason or "")
        if cascaded:
            log(f"  ↳ cascaded: {cascaded} dependent story(ies) marked blocked")
    elif status == "blocked":
        data.setdefault("blocked_stories", []).append(sid)
        if data.get("current_story_id") == sid:
            data["current_story_id"] = None
        log(f"⏸ Blocked {sid}: {reason}")
        append_execution_log(data, f"blocked story={sid} reason={reason}")
    return persist(data)


MAX_PERSIST_RETRIES = 3


def persist(data: dict) -> dict:
    """Write progress with optimistic-concurrency rebase.

    Assumes single-writer use (one orchestrator process per project root).
    On VersionConflict, rebases the in-memory `data` onto the on-disk version
    number and retries up to MAX_PERSIST_RETRIES times with exponential backoff.

    Concurrent multi-writer use is NOT supported: if another process wrote
    semantically-different state between read and write, this retry will
    overwrite it. A warning is logged when we detect the conflict targeted
    keys that materially differ. Re-raises VersionConflict after the final
    retry attempt.
    """
    expected = data["version"]
    last_err: Optional[VersionConflict] = None
    for attempt in range(MAX_PERSIST_RETRIES):
        try:
            write_progress(data, expected_version=expected)
            return read_progress()
        except VersionConflict as e:
            last_err = e
            time.sleep(0.1 * (2 ** attempt))
            on_disk = read_progress()
            # Detect potentially-clobbering rebase
            on_disk_sids = {
                s["id"]: s.get("status")
                for ep in on_disk.get("epics", [])
                for s in ep.get("stories", [])
            }
            mem_sids = {
                s["id"]: s.get("status")
                for ep in data.get("epics", [])
                for s in ep.get("stories", [])
            }
            divergent = [
                sid for sid in on_disk_sids
                if sid in mem_sids and on_disk_sids[sid] != mem_sids[sid]
            ]
            if divergent:
                log(
                    f"  ⚠ persist conflict: on-disk has different statuses for "
                    f"{divergent[:3]}{'...' if len(divergent) > 3 else ''} — "
                    "single-writer assumption violated; overwriting"
                )
            data["version"] = on_disk["version"]
            expected = on_disk["version"]
    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------

def cmd_develop(args: argparse.Namespace) -> int:
    if PROGRESS_FILE.exists() and not args.force:
        existing = read_progress()
        if existing.get("status") == "completed":
            log("Project already complete. Use --force to re-run from scratch.")
            return 0
        if existing.get("status") == "in_progress":
            log("In-progress plan detected. Use `resume`, or --force to restart.")
            return 1
    if args.force and PROGRESS_FILE.exists():
        shutil.copy(PROGRESS_FILE, PROGRESS_BACKUP)
        log(f"  backed up existing plan -> {PROGRESS_BACKUP}")
        PROGRESS_FILE.unlink()

    use_llm_spec = getattr(args, "spec_llm_fallback", False)
    data = phase_spec_and_plan(args.spec, use_llm_spec=use_llm_spec)
    if args.dry_run:
        log("--dry-run: plan written, exiting before execution.")
        return 0
    return run_loop(data, only_story=args.story, from_story=args.from_story)


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate docs/specs/ without invoking any agent. Pre-flight check."""
    if validate_specs is None:
        die("spec_parser unavailable -- cannot validate")
    report = validate_specs(Path("."))
    print(report.render())
    return 0 if report.ok else 1


def cmd_health_check(args: argparse.Namespace) -> int:
    """M11.3: pre-flight checks before any LLM call.

    Verifies:
      - Runner CLI is on PATH (claude or opencode)
      - Project config parses cleanly
      - All agent files parse and have permission frontmatter
      - All skill files load and have required fields
      - Persona imports resolve + permission contracts are compatible
      - Git repo state is sensible (user.name + user.email set)

    Exit code 0 = all green; 1 = at least one issue.
    """
    checks: list[tuple[str, bool, str]] = []  # (label, ok, detail)

    # 1. Runner CLI on PATH
    try:
        from runners import select_runner as _select_runner
        runner = _select_runner(os.environ.get("AA_RUNNER") or _CONFIG_RUNNER)
        checks.append(("runner CLI", True, f"using {runner.name}"))
    except Exception as e:
        checks.append(("runner CLI", False, str(e)[:200]))

    # 2. Config parse
    if CONFIG_FILE.exists():
        try:
            json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            checks.append(("config.json", True, str(CONFIG_FILE)))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            checks.append(("config.json", False, str(e)[:200]))
    else:
        checks.append(("config.json", True, "absent (using defaults)"))

    # 3. Agent files parse
    agents_dir = Path(".opencode/agents")
    if agents_dir.exists():
        try:
            from runners import parse_agent_file as _parse_agent
            parsed = 0
            errors: list[str] = []
            for f in sorted(agents_dir.glob("*.md")):
                try:
                    _parse_agent(f.stem, agents_dir=agents_dir)
                    parsed += 1
                except Exception as e:
                    errors.append(f"{f.name}: {type(e).__name__}: {e}")
            if errors:
                checks.append(("agents parse", False, f"{parsed} ok; failures: " + " | ".join(errors[:3])))
            else:
                checks.append(("agents parse", True, f"{parsed} agent file(s)"))
        except ImportError as e:  # pragma: no cover — defensive
            checks.append(("agents parse", False, f"runners module unavailable: {e}"))
    else:
        checks.append(("agents parse", False, f"{agents_dir} does not exist — run init.sh"))

    # 4. Skill files load
    skills_dir = Path(".opencode/skills")
    try:
        from skills import load_all_skills as _load_skills
        skills = _load_skills(skills_dir=skills_dir if skills_dir.exists() else None)
        missing_fields = [
            sid for sid, sk in skills.items()
            if not sk.id or not sk.description or not sk.applicable_agents
        ]
        if missing_fields:
            checks.append(("skills load", False, f"{len(skills)} loaded; incomplete: {missing_fields[:5]}"))
        else:
            checks.append(("skills load", True, f"{len(skills)} skill file(s)"))
    except ImportError as e:  # pragma: no cover — defensive
        checks.append(("skills load", False, f"skills module unavailable: {e}"))

    # 5. Persona imports resolve + permissions compatible
    persona_names = ["engineer", "architect", "scrum-master", "watcher"]
    try:
        from runners import (
            check_skill_permissions as _check_perms,
            parse_agent_file as _parse_agent,
            resolve_skill_for_agent as _resolve,
        )
        unresolved: list[str] = []
        perm_conflicts: list[str] = []
        for persona in persona_names:
            try:
                agent_def = _parse_agent(persona, agents_dir=agents_dir if agents_dir.exists() else None)
            except FileNotFoundError:
                continue  # not all projects ship every persona
            for sid in agent_def.imports:
                inline, sk_file = _resolve(agent_def, sid, skills_dir=skills_dir if skills_dir.exists() else None)
                if inline is None and sk_file is None:
                    unresolved.append(f"@{persona}/{sid}")
                    continue
                if sk_file is not None:
                    ok, conflicts = _check_perms(agent_def, sid, skills_dir=skills_dir if skills_dir.exists() else None)
                    if not ok:  # pragma: no cover — rare permission mismatch
                        perm_conflicts.append(f"@{persona}/{sid}: {conflicts[0]}")
        if unresolved:
            checks.append(("persona imports", False, f"unresolved: {unresolved[:5]}"))
        elif perm_conflicts:  # pragma: no cover — rare permission mismatch
            checks.append(("persona perms", False, f"conflicts: {perm_conflicts[:3]}"))
        else:
            checks.append(("persona imports", True, f"{len(persona_names)} personas, all imports resolved + permissions OK"))
    except ImportError as e:  # pragma: no cover — defensive
        checks.append(("persona imports", False, f"runners module unavailable: {e}"))

    # 6. Git repo sanity
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if proc.returncode != 0 or proc.stdout.strip() != "true":
            checks.append(("git repo", False, "not inside a git work tree"))
        else:
            email = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, check=False).stdout.strip()
            name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, check=False).stdout.strip()
            if not email or not name:
                checks.append(("git config", False, f"user.email={email!r} user.name={name!r} — @commit will fail"))
            else:
                checks.append(("git config", True, f"{name} <{email}>"))
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        checks.append(("git", False, f"git not available: {e}"))

    # 7. Spec validity (best-effort — only if specs exist)
    if Path("docs/specs/epics").exists():
        try:
            if validate_specs is not None:
                report = validate_specs(Path("."))
                if report.ok:
                    checks.append(("specs", True, "validate clean"))
                else:
                    checks.append(("specs", False, f"{len(report.errors)} error(s); run `aa-orchestrator validate` for details"))
        except Exception as e:  # pragma: no cover — defensive
            checks.append(("specs", False, f"{type(e).__name__}: {e}"))

    # Print report
    all_ok = all(ok for _, ok, _ in checks)
    print("\nHealth check\n" + "=" * 60)
    for label, ok, detail in checks:
        symbol = "✓" if ok else "✗"
        print(f"  {symbol} {label:18s} {detail}")
    print("=" * 60)
    print("OVERALL: " + ("✓ healthy" if all_ok else "✗ issues detected"))
    return 0 if all_ok else 1


def cmd_resume(args: argparse.Namespace) -> int:
    data = read_progress()
    if args.retry_failed:
        for s in all_stories(data):
            if s["status"] == "failed":
                s["status"] = "pending"
                append_execution_log(data, f"retry_failed reset story={s['id']}")
        data["failed_stories"] = []
        data = persist(data)
    if args.retry_blocked:
        for s in all_stories(data):
            if s["status"] == "blocked":
                s["status"] = "pending"
                append_execution_log(data, f"retry_blocked reset story={s['id']}")
        data["blocked_stories"] = []
        data = persist(data)

    intermediate = {"in_progress", "review_pass", "test_written"}
    for s in all_stories(data):
        if s["status"] in intermediate:
            log(f"  ↻ resetting stuck story {s['id']} from `{s['status']}` to `pending`")
            s["status"] = "pending"
            append_execution_log(
                data, f"resume_reset story={s['id']} from={s['status']}"
            )
    if data.get("current_story_id"):
        story = find_story(data, data["current_story_id"])
        if story and story["status"] in intermediate:  # pragma: no cover — dead branch: intermediate reset above
            data["current_story_id"] = None
    data = persist(data)

    cur = data.get("current_story_id")
    if cur:
        story = find_story(data, cur)
        if story and story.get("status") == "implemented":
            log(f"  ↻ resuming {cur} from `implemented` — re-running tests")
            test_files = (story.get("artifacts") or {}).get("test_files", [])
            ok, _ = run_tests_independently(test_files)
            if not ok:
                log("  ⚠ tests fail after resume — re-running implementation phase")
                story["status"] = "review_pass"
                data = persist(data)
    return run_loop(data, only_story=args.story)


def cmd_discover(args: argparse.Namespace) -> int:
    """M5.1: one-line product idea -> SCRUM spec tree via @discover.

    M12.4: when `--interactive` is set, asks 3 clarifying questions first and
    appends the answers to the prompt so @discover gets a richer input than
    just the one-line idea.
    """
    target = Path(args.target_dir).resolve() if args.target_dir else Path.cwd()
    if not target.exists():
        die(f"target dir does not exist: {target}")
    os.chdir(target)
    specs_dir = Path("docs/specs/epics")
    specs_dir.mkdir(parents=True, exist_ok=True)

    # M12.4: optional clarifying Q&A before generating the spec
    clarifications: list[str] = []
    interactive = bool(getattr(args, "interactive", False))
    if interactive and _WIZARD_AVAILABLE:
        log("\n💬 A few clarifying questions before generating the spec:")
        try:
            who = _prompt_text(
                "  1. Who is the primary user? (e.g., 'solo developer', 'small team', 'general consumers')",
                default="general users",
            )
            scale = _prompt_text(
                "  2. What scale do you need? (e.g., 'side project', 'paying customers', 'high-traffic')",
                default="side project",
            )
            tech = _prompt_text(
                "  3. Any tech-stack constraints? (e.g., 'TypeScript + Postgres', 'no preference')",
                default="no preference",
            )
            clarifications.extend([
                f"Primary user: {who}",
                f"Scale target: {scale}",
                f"Tech-stack constraints: {tech}",
            ])
            log("")
        except _WizardAborted:
            log("  (clarifications skipped — proceeding with the one-line idea)")
            clarifications = []

    # Build the prompt
    context_snippets = []
    if Path("README.md").exists():
        readme = Path("README.md").read_text(encoding="utf-8", errors="replace")[:2000]
        context_snippets.append(f"## Existing README.md (truncated)\n{readme}")
    if Path("package.json").exists():
        context_snippets.append(
            f"## Existing package.json\n{Path('package.json').read_text(encoding='utf-8')[:1000]}"
        )
    if Path("pyproject.toml").exists():
        context_snippets.append(
            f"## Existing pyproject.toml\n{Path('pyproject.toml').read_text(encoding='utf-8')[:1000]}"
        )
    existing = "\n\n".join(context_snippets) if context_snippets else "(empty project)"

    clarification_block = ""
    if clarifications:
        clarification_block = (
            "\n\n## Clarifying answers from the user\n"
            + "\n".join(f"- {c}" for c in clarifications)
        )

    prompt = (
        f"Product idea: {args.idea}\n\n"
        f"Target directory: {target}\n\n"
        f"Existing context:\n{existing}"
        f"{clarification_block}\n\n"
        "Decompose into 3–7 epics and write spec files under docs/specs/epics/. "
        "Follow the canonical format exactly. Validate IDs and dependencies."
    )

    log(f"\n🔍 @discover: '{args.idea[:80]}...'")
    out = call_agent("discover", prompt)
    if "NEEDS_CLARIFICATION" in out.upper():
        log("\n⚠ @discover needs clarification:")
        # Echo the question from the output
        for line in out.splitlines():
            if "?" in line:
                log(f"  {line.strip()}")
        return 1
    if "VERDICT: SPEC_WRITTEN" not in out and "VERDICT:SPEC_WRITTEN" not in out:
        die("@discover did not emit VERDICT: SPEC_WRITTEN — abort. Last 500 chars:\n" + out[-500:])

    # Validate the produced spec
    log("\nValidating generated spec...")
    if validate_specs is None:  # pragma: no cover — defensive
        die("spec_parser unavailable — cannot validate")
    report = validate_specs(Path("."))
    if not report.ok:
        log("\n✗ Generated spec has validation errors:")
        log(report.render())
        log("\nRe-running @discover with the errors as feedback...")
        retry_prompt = (
            prompt
            + "\n\n## Previous attempt validation errors\n"
            + "\n".join(f"- {e}" for e in report.errors)
            + "\n\nFix these errors and rewrite the spec files. Use the exact same canonical format."
        )
        out2 = call_agent("discover", retry_prompt)
        if "VERDICT: SPEC_WRITTEN" not in out2 and "VERDICT:SPEC_WRITTEN" not in out2:
            die("@discover retry also failed. Last 500 chars:\n" + out2[-500:])
        report = validate_specs(Path("."))
        if not report.ok:
            log("\n✗ Spec STILL invalid after retry:")
            log(report.render())
            return 1

    log("\n✓ Spec validated successfully.")
    print(report.render())
    if args.then_develop:
        log("\nChaining into develop...")
        return cmd_develop(
            argparse.Namespace(
                spec=None,
                story=None,
                from_story=None,
                dry_run=False,
                force=False,
                spec_llm_fallback=False,
            )
        )
    return 0


def cmd_revisit(args: argparse.Namespace) -> int:
    """M2.1: reopen a completed story for another pass."""
    data = read_progress()
    sid = args.story
    story = find_story(data, sid)
    if story is None:
        die(f"Story {sid!r} not found")
    if story["status"] not in ("completed", "failed", "blocked"):
        die(
            f"Story {sid} is in state {story['status']!r} — only terminal stories "
            "(completed/failed/blocked) can be revisited. Use `resume` for in-progress stories."
        )

    # Archive prior artifacts so the next pass can compare
    arts = story.setdefault("artifacts", {})
    prev = arts.setdefault("previous", [])
    snapshot = {
        "status": story["status"],
        "reason": story.get("reason"),
        "test_files": arts.get("test_files", []),
        "implementation_files": arts.get("implementation_files", []),
        "commit_hash": arts.get("commit_hash"),
        "branch": arts.get("branch"),
        "worktree_path": arts.get("worktree_path"),
        "archived_at": now_iso(),
        "revisit_reason": args.reason or "(no reason given)",
    }
    prev.append(snapshot)

    # Reset story to pending so run_loop picks it up
    prior_status = story["status"]
    story["status"] = "pending"
    # Clear current artifacts so the new pass starts clean
    for k in ("test_files", "implementation_files", "commit_hash", "branch", "worktree_path",
              "test_run_evidence", "criterion_test_mapping"):
        arts[k] = [] if k.endswith("_files") else None
    arts["criterion_test_mapping"] = {}

    # Remove from terminal lists
    for list_key, status in (
        ("completed_stories", "completed"),
        ("failed_stories", "failed"),
        ("blocked_stories", "blocked"),
    ):
        if prior_status == status and sid in (data.get(list_key) or []):
            data[list_key].remove(sid)

    append_execution_log(
        data,
        f"revisit story={sid} from={prior_status} reason={args.reason or '(none)'}"
    )

    # Cascade reopen of dependents (opt-in)
    if args.cascade_dependents:
        reopened = []
        for other in all_stories(data):
            if sid in other.get("depends_on", []) and other["status"] in ("completed", "failed", "blocked"):
                prev_other = other.setdefault("artifacts", {}).setdefault("previous", [])
                prev_other.append({
                    "status": other["status"],
                    "archived_at": now_iso(),
                    "revisit_reason": f"cascade_from={sid}",
                })
                other["status"] = "pending"
                reopened.append(other["id"])
        if reopened:
            log(f"  ↳ cascade-reopened {len(reopened)} dependent(s): {', '.join(reopened)}")

    data = persist(data)
    log(f"↻ Revisited {sid} (was {prior_status}); now pending. Reason: {args.reason or '(none)'}")
    log(f"  Run `aa-orchestrator develop` to re-execute, or `aa-orchestrator develop --story {sid}` for this story only.")
    return 0


def _extract_sprint_goal(out: str) -> Optional[str]:
    """Heuristic: extract a sprint goal sentence from agent output."""
    for raw in out.splitlines():
        line = raw.strip().lstrip("*").strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("goal:"):
            return line.split(":", 1)[1].strip().strip("*").strip()
        if low.startswith("**goal:**") or low.startswith("sprint goal:"):  # pragma: no cover — alternate goal-prefix path
            return line.split(":", 1)[1].strip().strip("*").strip()
    # Fallback: first short non-heading paragraph
    for raw in out.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and not line.startswith("VERDICT"):
            return line[:200]
    return None


def cmd_sprint(args: argparse.Namespace) -> int:
    """M6.5: sprint subcommand dispatcher (plan|start|end|status|cycle)."""
    action = args.action
    if action == "plan":
        return _sprint_plan()
    if action == "start":  # pragma: no cover — alt dispatch already covered for plan/end/status
        return _sprint_start(args)
    if action == "end":
        return _sprint_end()
    if action == "status":
        return _sprint_status()
    if action == "cycle":
        return _sprint_cycle(args)
    die(f"unknown sprint action: {action!r}. Try plan|start|end|status|cycle.")


def _sprint_plan() -> int:
    data = read_progress()
    ensure_sprint_state(data)

    cur = current_sprint(data)
    if cur:
        log(f"Sprint {cur['number']} is already in progress. End it first with `sprint end`.")
        return 1

    # Pick the next N eligible stories deterministically (orchestrator owns selection)
    size = _sprint_size()
    selected: list[dict] = []
    scheduled_ids: set[str] = set()
    for _ in range(size):
        next_s = _next_eligible_excluding(data, scheduled_ids)
        if next_s is None:
            break
        selected.append(next_s)
        scheduled_ids.add(next_s["id"])

    if not selected:
        log("No eligible pending stories — backlog is empty or all dependencies blocked.")
        return 1

    sprint_number = (data.get("current_sprint", 0) or 0) + 1
    velocity_avg = rolling_velocity_avg(data)
    total_points = sum(story_points(s) for s in selected)

    prompt = (
        f"Sprint {sprint_number} planning.\n\n"
        f"Velocity rolling avg (last 3 sprints): {velocity_avg:.1f} points\n"
        f"Sprint size target: {size} stories\n"
        f"Selected total points: {total_points}\n\n"
        f"Selected stories (already filtered for dependency satisfaction):\n"
        + "\n".join(
            f"- {s['id']} ({s.get('estimated_complexity', 'medium')}, "
            f"{story_points(s)}pts, {len(s.get('tasks', []))} tasks): "
            f"{s.get('title', '?')}"
            for s in selected
        )
        + "\n\nProduce a sprint goal + justification + risks per your instructions."
    )

    try:
        out = call_agent("sprint-planner", prompt)
    except AgentError as e:
        log(f"  ⚠ @sprint-planner failed ({e}); using fallback goal")
        out = (
            f"**Goal:** Sprint {sprint_number}: complete {len(selected)} eligible stories.\n\n"
            "**Justification:** (deterministic fallback — @sprint-planner unavailable)\n\n"
            "VERDICT: SPRINT_PLANNED\n"
        )

    goal = _extract_sprint_goal(out) or f"Sprint {sprint_number}"

    # Write plan doc
    plan_dir = Path("docs/sprints")
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{sprint_number:02d}-plan.md"
    plan_path.write_text(
        f"# Sprint {sprint_number} Plan\n\n"
        f"**Goal:** {goal}\n\n"
        f"**Stories ({len(selected)}, {total_points} pts total):**\n"
        + "\n".join(
            f"- `{s['id']}` ({story_points(s)}pts, {s.get('estimated_complexity', 'medium')}) — {s.get('title', '?')}"
            for s in selected
        )
        + f"\n\n**Velocity context:** rolling avg = {velocity_avg:.1f} pts\n\n"
        f"---\n\n## Planner notes\n\n{out}\n",
        encoding="utf-8",
    )

    # Add sprint to state with status "planned"
    sprint = {
        "number": sprint_number,
        "goal": goal,
        "story_ids": [s["id"] for s in selected],
        "started_at": None,
        "ended_at": None,
        "status": "planned",
        "velocity_points": 0,
        "plan_path": str(plan_path),
    }
    data.setdefault("sprints", []).append(sprint)
    persist(data)

    log(f"\n✓ Sprint {sprint_number} planned: {len(selected)} stories ({total_points} pts)")
    log(f"  Goal: {goal}")
    log(f"  Plan: {plan_path}")
    log("  Run `aa-orchestrator sprint start` to execute.")
    return 0


def _next_eligible_excluding(data: dict, exclude: set[str]) -> Optional[dict]:
    """Variant of next_eligible_story that skips already-picked stories."""
    pending = [s for s in all_stories(data)
               if s.get("status") == "pending" and s["id"] not in exclude]
    if not pending:
        return None
    pending.sort(key=lambda s: (s.get("execution_wave", 999), s["id"]))
    for s in pending:
        ok, _ = deps_satisfied(data, s)
        if ok:
            return s
    return None  # pragma: no cover — all pending stories blocked by deps


def _sprint_start(args: argparse.Namespace) -> int:
    """M6.4: execute the planned sprint — bounded run_loop over sprint story_ids."""
    data = read_progress()
    ensure_sprint_state(data)

    # Find the most recent planned-or-in-progress sprint
    sprints = data.get("sprints", [])
    if not sprints:
        log("No sprints planned. Run `aa-orchestrator sprint plan` first.")
        return 1
    sprint = sprints[-1]
    if sprint.get("status") not in ("planned", "in_progress"):
        log(f"Last sprint ({sprint['number']}) is {sprint['status']!r} — run `sprint plan` for the next one.")
        return 1

    # Mark in_progress
    if sprint["status"] == "planned":
        sprint["status"] = "in_progress"
        sprint["started_at"] = now_iso()
        data["current_sprint"] = sprint["number"]
        append_execution_log(data, f"sprint_start number={sprint['number']} goal={sprint.get('goal', '?')}")
        data = persist(data)

    log(f"\n▶ Starting sprint {sprint['number']}: {sprint.get('goal', '?')}")
    log(f"  Stories: {len(sprint['story_ids'])}")

    # Run only the sprint's stories. We reuse run_loop with a custom filter via from_story
    # but the simplest is to iterate the story_ids directly.
    global GLOBAL_PERSIST_STATE
    GLOBAL_PERSIST_STATE = data
    deadline = time.monotonic() + OUTER_TIMEOUT_SEC

    while True:
        remaining = deadline - time.monotonic()
        if remaining < 120:
            log(f"\n⏱ Approaching outer timeout — persisting and returning EXIT_MORE_WORK")
            persist(data)
            return EXIT_MORE_WORK

        # Find next eligible story IN THIS SPRINT
        sprint_pending = [
            find_story(data, sid)
            for sid in sprint["story_ids"]
        ]
        sprint_pending = [s for s in sprint_pending if s and s.get("status") == "pending"]

        if not sprint_pending:
            log(f"\n✓ All sprint {sprint['number']} stories terminal.")
            return 0

        # Pick from sprint_pending using deps_satisfied
        next_story = None
        sprint_pending.sort(key=lambda s: (s.get("execution_wave", 999), s["id"]))
        for s in sprint_pending:
            ok, _ = deps_satisfied(data, s)
            if ok:
                next_story = s
                break

        if next_story is None:
            log(f"\n⏸ Sprint {sprint['number']}: remaining stories blocked on unmet deps.")
            return 2

        try:
            data = run_story(data, next_story)
            GLOBAL_PERSIST_STATE = data
            # M8.3: deterministic watcher between sprint stories
            run_watcher(data)
        except AgentError as e:
            log(f"  ✗ agent failure during {next_story['id']}: {e}")
            data = read_progress()
            data = finalize_story(
                data,
                next_story["id"],
                "failed",
                f"agent_error:{e.agent}:{e.detail[:120]}",
            )
            GLOBAL_PERSIST_STATE = data
            continue
        except KeyboardInterrupt:  # pragma: no cover — user-triggered SIGINT
            raise
        except Exception as e:
            import traceback
            log(f"  ✗ unexpected error during {next_story['id']}: {type(e).__name__}: {e}")
            log(traceback.format_exc())
            return 1


def _sprint_end() -> int:
    """M6.3: complete the current sprint, invoke @retro, advance counter."""
    data = read_progress()
    ensure_sprint_state(data)

    cur = current_sprint(data)
    if cur is None:
        # Allow ending the last sprint even if its status is "planned" but had no run
        sprints = data.get("sprints", [])
        if not sprints:
            log("No sprints to end. Run `sprint plan` first.")
            return 1
        cur = sprints[-1]
        if cur.get("status") in ("completed", "ended"):
            log(f"Sprint {cur['number']} already ended.")
            return 0

    sprint_num = cur["number"]
    sprint_goal = cur.get("goal", f"Sprint {sprint_num}")

    # Classify story outcomes
    outcomes: dict[str, list[str]] = {
        "completed": [], "failed": [], "blocked": [], "pending": [], "other": [],
    }
    for sid in cur.get("story_ids", []):
        story = find_story(data, sid)
        if not story:  # pragma: no cover — sprint story_ids should always exist
            outcomes["other"].append(f"{sid} (missing)")
            continue
        status = story.get("status", "unknown")
        bucket = outcomes.get(status, outcomes["other"])
        title = story.get("title", "?")
        bucket.append(f"{sid}: {title}")

    # Compute velocity
    velocity = compute_sprint_velocity(data, sprint_num)
    rolling = rolling_velocity_avg(data)
    delta_pct = 0.0
    if rolling > 0:  # pragma: no cover — alternate velocity-delta path
        delta_pct = (velocity - rolling) / rolling * 100

    # Read cost tracking (M3.2) for this sprint window if available
    cost_tracking = data.get("cost_tracking", {})
    total_cost = cost_tracking.get("total_usd", 0.0)

    # Read open impediments (M9.4 — file may not exist yet)
    impediments_count = 0
    impediments_path = Path("docs/impediments.md")
    if impediments_path.exists():
        try:
            content = impediments_path.read_text(encoding="utf-8")
            impediments_count = content.lower().count("status: open")
        except OSError:  # pragma: no cover — defensive
            pass

    # Build retro prompt
    prompt = (
        f"Sprint {sprint_num} retrospective.\n\n"
        f"**Goal:** {sprint_goal}\n\n"
        f"**Story outcomes:**\n"
        + "".join(
            f"\n{status.upper()} ({len(stories)}):\n" + "\n".join(f"  - {s}" for s in stories)
            for status, stories in outcomes.items() if stories
        )
        + f"\n\n**Velocity:** {velocity} pts (rolling avg: {rolling:.1f}, delta: {delta_pct:+.0f}%)\n"
        + f"**Total cost:** ${total_cost:.4f}\n"
        + f"**Open impediments:** {impediments_count}\n\n"
        "Produce the retro doc per your instructions. End with VERDICT: RETRO_COMPLETE."
    )

    try:
        out = call_agent("retro", prompt)
    except AgentError as e:
        log(f"  ⚠ @retro failed ({e}); writing deterministic retro")
        out = (
            f"# Sprint {sprint_num} Retro\n\n"
            f"## What went well\n- (retro agent unavailable; deterministic fallback)\n\n"
            f"## What went wrong\n- @retro agent error: {e}\n\n"
            f"## Metrics\n"
            f"- Stories completed: {len(outcomes['completed'])} / {len(cur.get('story_ids', []))}\n"
            f"- Velocity: {velocity} pts\n"
            f"- Cost: ${total_cost:.4f}\n\n"
            f"VERDICT: RETRO_COMPLETE\n"
        )

    # Write retro doc
    retro_dir = Path("docs/sprints")
    retro_dir.mkdir(parents=True, exist_ok=True)
    retro_path = retro_dir / f"{sprint_num:02d}-retro.md"
    retro_path.write_text(out, encoding="utf-8")

    # Update sprint state
    cur["status"] = "completed"
    cur["ended_at"] = now_iso()
    cur["velocity_points"] = velocity
    cur["velocity_delta_pct"] = round(delta_pct, 1)
    cur["velocity_rolling_avg_at_end"] = round(rolling, 1)
    cur["retro_path"] = str(retro_path)
    data["current_sprint"] = sprint_num
    data = persist(data)

    log(f"\n✓ Sprint {sprint_num} ended.")
    log(f"  Velocity: {velocity} pts (delta vs rolling avg: {delta_pct:+.0f}%)")
    log(f"  Outcomes: {len(outcomes['completed'])} completed, "
        f"{len(outcomes['failed'])} failed, {len(outcomes['blocked'])} blocked")
    log(f"  Retro: {retro_path}")

    # M7.1: produce a release note if any stories committed
    if outcomes["completed"]:
        try:
            _run_release(data, cur)
        except Exception as e:  # pragma: no cover — non-fatal release-note guard
            log(f"  ⚠ release note generation failed (non-fatal): {type(e).__name__}: {e}")

    # M9.2: report Definition of Done compliance (advisory — does not block)
    dod_ok, dod_failures = enforce_definition_of_done(data, cur)
    if not dod_ok:  # pragma: no cover — DoD failures already covered by enforce_definition_of_done tests
        log(f"\n⚠ Definition of Done: {len(dod_failures)} item(s) missing:")
        for f in dod_failures:
            log(f"  - {f}")
    else:
        log("  ✓ Definition of Done: all items met")

    return 0


def _run_release(data: dict, sprint: dict) -> None:
    """M7.1: invoke @release to produce docs/releases/v0.NN.md and optionally tag."""
    sprint_num = sprint["number"]
    release_version = f"v0.{sprint_num}"

    # Collect commits for completed stories in this sprint
    commits: list[dict] = []
    files_touched: set[str] = set()
    for sid in sprint.get("story_ids", []):
        story = find_story(data, sid)
        if not story or story.get("status") != "completed":
            continue
        arts = story.get("artifacts", {})
        commit_hash = arts.get("commit_hash")
        if not commit_hash:
            continue
        commits.append({
            "hash": commit_hash,
            "story_id": sid,
            "title": story.get("title", ""),
            "files": (arts.get("implementation_files") or []) + (arts.get("test_files") or []),
        })
        files_touched.update(arts.get("implementation_files") or [])
        files_touched.update(arts.get("test_files") or [])

    if not commits:
        log("  no commits found in sprint — skipping release note")
        return

    prompt = (
        f"Release {release_version}\n"
        f"Sprint #{sprint_num}: {sprint.get('goal', '?')}\n"
        f"Date: {now_iso()}\n\n"
        f"Commits in this sprint ({len(commits)}):\n"
        + "\n".join(
            f"- `{c['hash'][:7]}` ({c['story_id']}): {c['title']}\n"
            f"  Files: {', '.join(c['files'])}"
            for c in commits
        )
        + f"\n\nTotal files changed: {len(files_touched)}\n"
        f"Velocity: {sprint.get('velocity_points', 0)} pts\n\n"
        "Produce the release note per your instructions."
    )

    try:
        out = call_agent("release", prompt)
    except AgentError as e:
        log(f"  ⚠ @release agent failed ({e}); writing deterministic fallback")
        out = (
            f"# Release {release_version}\n\n"
            f"Released: {now_iso()[:10]}\nSprint: #{sprint_num}\n"
            f"Goal: {sprint.get('goal', '?')}\n\n"
            "## Commits\n"
            + "\n".join(f"- `{c['hash'][:7]}` ({c['story_id']}): {c['title']}" for c in commits)
            + f"\n\n## Stats\n- Stories shipped: {len(commits)}\n"
            f"- Files changed: {len(files_touched)}\n"
            f"- Velocity: {sprint.get('velocity_points', 0)} pts\n\n"
            "VERDICT: RELEASE_NOTED\n"
        )

    rel_dir = Path("docs/releases")
    rel_dir.mkdir(parents=True, exist_ok=True)
    rel_path = rel_dir / f"{release_version}.md"
    rel_path.write_text(out, encoding="utf-8")
    log(f"  📦 release notes: {rel_path}")

    # Optional git tag (config flags)
    cfg_pipe = {}
    if CONFIG_FILE.exists():
        try:
            cfg_pipe = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("pipeline", {})
        except (json.JSONDecodeError, OSError):  # pragma: no cover — defensive
            pass
    if cfg_pipe.get("auto_tag", False):
        # M14.3: separate tag vs push so failure modes log distinctly.
        # tag-already-exists is benign; push failure means the release isn't published.
        try:
            subprocess.run(
                ["git", "tag", "-a", release_version, "-m", f"Sprint #{sprint_num}: {sprint.get('goal', '')}"],
                check=True, capture_output=True, text=True,
            )
            log(f"  🏷  tagged {release_version}")
        except subprocess.CalledProcessError as e:  # pragma: no cover — tag-already-exists
            log(f"  ⚠ git tag failed (non-fatal — tag may already exist): {e.stderr.strip()[:200]}")

        if cfg_pipe.get("auto_push_tags", False):  # pragma: no cover — auto-push covered in test_m14_bug_fixes
            try:
                subprocess.run(
                    ["git", "push", "--tags"],
                    check=True, capture_output=True, text=True,
                )
                log(f"  ⤴  pushed tag {release_version}")
            except subprocess.CalledProcessError as e:
                # Push failure = real release-artifact loss. Log at error severity.
                log(f"  ✗ ERROR: git push --tags failed — release {release_version} NOT published: {e.stderr.strip()[:200]}")

    # Record release in sprint state
    sprint["release_path"] = str(rel_path)
    sprint["release_version"] = release_version
    persist(data)


def _sprint_status() -> int:
    """M6.5: show current sprint summary."""
    data = read_progress()
    ensure_sprint_state(data)
    sprints = data.get("sprints", [])
    if not sprints:
        log("No sprints yet. Run `aa-orchestrator sprint plan` to start.")
        return 0
    print(f"Total sprints: {len(sprints)}")
    cur = current_sprint(data)
    if cur:
        print(f"Active sprint: #{cur['number']} ({cur['status']})")
        print(f"  Goal: {cur.get('goal', '?')}")
        print(f"  Stories: {len(cur.get('story_ids', []))}")
    else:
        last = sprints[-1]
        print(f"Last sprint: #{last['number']} ({last['status']})")
    velocity_avg = rolling_velocity_avg(data)
    if velocity_avg > 0:  # pragma: no cover — requires completed sprints with velocity
        print(f"Velocity (rolling avg): {velocity_avg:.1f} pts")
    return 0


def _sprint_cycle(args: argparse.Namespace) -> int:
    """M6.4: chain plan → start → end → groom → plan ... until backlog empty.

    Bounded by:
    - `pipeline.max_sprint_cycles` (default 10) to prevent runaway
    - production-gate failure (halts the cycle)
    - empty backlog (natural end)
    """
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}
    max_cycles = int(cfg.get("pipeline", {}).get("max_sprint_cycles", 10))

    for cycle in range(1, max_cycles + 1):
        log(f"\n═══ Sprint cycle iteration {cycle}/{max_cycles} ═══")

        # Plan
        rc = _sprint_plan()
        if rc != 0:
            log(f"  cycle ended: no more eligible stories (rc={rc})")
            return 0 if rc == 1 else rc

        # Start
        rc = _sprint_start(argparse.Namespace())
        if rc != 0:
            log(f"  cycle halted: sprint start returned {rc}")
            return rc

        # End (retro)
        rc = _sprint_end()
        if rc != 0:
            log(f"  cycle halted: sprint end returned {rc}")
            return rc

        # Groom (best-effort)
        try:
            _run_backlog_groomer()
        except Exception as e:
            log(f"  ⚠ backlog grooming failed (non-fatal): {e}")

        # M12.6: interactive pause between sprints
        if getattr(args, "interactive", False) and _WIZARD_AVAILABLE:
            data = read_progress()
            sprints_done = data.get("sprints", [])
            last = sprints_done[-1] if sprints_done else {}
            pending = sum(
                1 for ep in data.get("epics", [])
                for s in ep.get("stories", [])
                if s.get("status") == "pending"
            )
            cost = data.get("cost_tracking", {}).get("total_usd", 0.0)
            log("\n--- Sprint review ---")
            log(f"  Just finished:  sprint #{last.get('number', '?')} "
                f"(velocity {last.get('velocity_points', 0)} pts, status={last.get('status', '?')})")
            log(f"  Retro:          {last.get('retro_path', '(none)')}")
            log(f"  Release notes:  {last.get('release_path', '(none)')}")
            log(f"  Backlog:        {pending} pending stor{'y' if pending == 1 else 'ies'}")
            log(f"  Cost so far:    ${cost:.4f}")

            if pending == 0:
                log("\n✓ Backlog empty — cycle complete.")
                return 0
            try:
                choice = _prompt_choice(
                    "\nContinue to the next sprint?",
                    options=[
                        "Yes — plan and run the next sprint",
                        "Show full status, then ask again",
                        "Stop — I'll resume manually later",
                    ],
                    default_index=0,
                )
            except _WizardAborted:
                log("aborted — state was persisted; resume anytime with `aa-orchestrator sprint cycle`")
                return 0
            if choice.startswith("Stop"):
                log("paused — state persisted")
                return 0
            if choice.startswith("Show full status"):
                cmd_status(argparse.Namespace())
                if not _prompt_yes_no("\nContinue to the next sprint?", default=True):
                    return 0

    log(f"\n⚠ Sprint cycle hit max iterations ({max_cycles}). Backlog may still have stories.")
    return 0


def _run_backlog_groomer() -> None:
    """Invoke @backlog-groomer between sprints. Output is advisory; written to docs."""
    data = read_progress()
    pending = [s for s in all_stories(data) if s.get("status") == "pending"]
    if not pending:
        return  # nothing to groom

    last_retro = ""
    sprints = data.get("sprints", [])
    if sprints:  # pragma: no cover — pre-existing retro path
        retro_path = sprints[-1].get("retro_path")
        if retro_path and Path(retro_path).exists():
            try:
                last_retro = Path(retro_path).read_text(encoding="utf-8")[-2000:]
            except OSError:
                pass

    prompt = (
        f"Backlog grooming after sprint {sprints[-1]['number'] if sprints else 0}.\n\n"
        f"## Last retro (tail)\n{last_retro or '(no prior retro)'}\n\n"
        "## Pending stories\n"
        + "\n".join(
            f"- `{s['id']}` ({s.get('estimated_complexity', 'medium')}, "
            f"{len(s.get('tasks', []))} tasks, {len(s.get('acceptance_criteria', []))} ACs, "
            f"depends_on={s.get('depends_on', [])}): {s.get('title', '?')}"
            for s in pending
        )
    )

    try:
        out = call_agent("backlog-groomer", prompt)
    except AgentError as e:
        log(f"  ⚠ @backlog-groomer failed ({e})")
        return

    # Write advisory output
    sprint_num = sprints[-1]["number"] if sprints else 0
    groom_dir = Path("docs/sprints")
    groom_dir.mkdir(parents=True, exist_ok=True)
    groom_path = groom_dir / f"{sprint_num:02d}-grooming.md"
    groom_path.write_text(out, encoding="utf-8")
    log(f"  📋 grooming written: {groom_path}")


# ---------------------------------------------------------------------------
# M12: interactive wizard commands
# ---------------------------------------------------------------------------

def _require_wizard() -> None:
    if not _WIZARD_AVAILABLE:  # pragma: no cover — defensive
        die("wizard module unavailable — reinstall via install.sh")


def cmd_setup(args: argparse.Namespace) -> int:
    """M12.2: one-time machine setup wizard.

    Checks Python version, git, runner CLIs, git user config, PATH.
    Offers to fix what it can; explains what the user needs to fix manually.
    """
    _require_wizard()
    print("\nautonomous-agents — one-time machine setup")
    print("=" * 60)

    issues: list[str] = []

    # Python version
    py = sys.version_info
    py_str = f"Python {py.major}.{py.minor}.{py.micro}"
    if (py.major, py.minor) >= (3, 10):
        print(f"  ✓ {py_str}")
    else:
        print(f"  ✗ {py_str} — need Python 3.10+")
        issues.append("Install Python 3.10 or newer.")

    # Git
    try:
        proc = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            print(f"  ✓ {proc.stdout.strip()}")
        else:
            issues.append("Install git.")
            print("  ✗ git not working")
    except FileNotFoundError:
        print("  ✗ git not found")
        issues.append("Install git.")

    # Git global user config
    email = subprocess.run(
        ["git", "config", "--global", "user.email"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    name = subprocess.run(
        ["git", "config", "--global", "user.name"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    if email and name:
        print(f"  ✓ git user: {name} <{email}>")
    else:
        print("  ✗ git user.email/user.name not set globally")
        if _prompt_yes_no("    Set them now interactively?", default=True):
            new_email = _prompt_text("    git user.email:", validator=lambda v: None if "@" in v else "must contain @")
            new_name = _prompt_text("    git user.name:", validator=lambda v: None if v.strip() else "cannot be empty")
            subprocess.run(["git", "config", "--global", "user.email", new_email], check=False)
            subprocess.run(["git", "config", "--global", "user.name", new_name], check=False)
            print(f"  ✓ git user: {new_name} <{new_email}>")
        else:
            issues.append("Run: git config --global user.email <you@example.com>; git config --global user.name <Your Name>")

    # Runner CLIs
    claude_present = shutil.which("claude") is not None
    opencode_present = shutil.which("opencode") is not None
    if claude_present:
        print("  ✓ claude CLI on PATH")
    if opencode_present:
        print("  ✓ opencode CLI on PATH")
    if not claude_present and not opencode_present:
        print("  ✗ neither claude nor opencode on PATH")
        issues.append(
            "Install one of:\n"
            "    - Claude Code:  https://claude.com/claude-code\n"
            "    - OpenCode:     https://opencode.ai/"
        )

    # API key sanity (Claude Code reads ANTHROPIC_API_KEY)
    if claude_present:
        if os.environ.get("ANTHROPIC_API_KEY"):
            print("  ✓ ANTHROPIC_API_KEY set")
        else:
            print("  ⚠ ANTHROPIC_API_KEY not set in environment")
            print("    (Claude Code may still use ~/.config/claude or its own credential store)")

    # aa-orchestrator on PATH?
    if shutil.which("aa-orchestrator"):
        print("  ✓ aa-orchestrator on PATH")
    else:
        print("  ✗ aa-orchestrator not on PATH")
        issues.append(
            "Add $AA_BIN to PATH (default $HOME/.local/bin):\n"
            '    export PATH="$HOME/.local/bin:$PATH"  >> ~/.zshrc  # or ~/.bashrc'
        )

    print("=" * 60)
    if not issues:
        print("✓ machine setup looks good — ready to `aa-orchestrator new <project>`")
        return 0
    print(f"✗ {len(issues)} issue(s) to fix:\n")
    for i, msg in enumerate(issues, 1):
        print(f"  {i}. {msg}\n")
    return 1


def cmd_new(args: argparse.Namespace) -> int:
    """M12.3: interactive new-project bootstrap.

    Validates project name, creates dir, runs git init, runs init.sh, optionally
    runs `discover` if the user has an idea ready.
    """
    _require_wizard()
    name = args.name or _prompt_text(
        "Project name (will become a directory):",
        validator=lambda v: None if v.strip() and "/" not in v and " " not in v else "no spaces or slashes",
    )

    target = Path(name).resolve()
    if target.exists() and any(target.iterdir()):
        log(f"⚠ {target} already exists and is non-empty")
        if not _prompt_yes_no("Continue anyway? (existing files will be preserved)", default=False):
            log("aborted")
            return 1
    target.mkdir(parents=True, exist_ok=True)

    # git init
    log(f"\n▶ Initializing git in {target}")
    git_init = subprocess.run(["git", "init", "-q"], cwd=target, capture_output=True, text=True, check=False)
    if git_init.returncode != 0:
        # M14.5: surface git init failure immediately rather than masking it
        # behind a later init.sh failure.
        die(f"git init failed (rc={git_init.returncode}): {git_init.stderr.strip()[:200]}")
    if not subprocess.run(["git", "-C", str(target), "config", "user.email"],
                          capture_output=True, text=True).stdout.strip():
        log("  ⚠ no git user.email set for this repo (using global if available)")

    # init.sh
    aa_home = os.environ.get("AA_HOME") or os.path.expanduser("~/.local/share/autonomous-agents")
    init_sh = Path(aa_home) / "init.sh"
    if not init_sh.exists():
        die(f"init.sh not found at {init_sh} — run install.sh first")

    runner = args.runner or _prompt_choice(
        "Which LLM runner should this project use?",
        options=["claude", "opencode"],
        default_index=0,
    )
    log(f"\n▶ Running init.sh --runner {runner}")
    proc = subprocess.run(
        ["bash", str(init_sh), "--runner", runner, str(target)],
        check=False,
    )
    if proc.returncode != 0:
        die(f"init.sh exited {proc.returncode}")

    # Optional: jump straight into discover
    os.chdir(target)
    have_idea = args.idea is not None or _prompt_yes_no(
        "\nDo you have a one-line product idea ready to turn into a spec?",
        default=True,
    )
    if have_idea:
        idea = args.idea or _prompt_text(
            "Product idea (one or two sentences):",
            validator=lambda v: None if len(v.strip()) >= 5 else "be more specific",
        )
        log("\n▶ Running discover...")
        discover_args = argparse.Namespace(
            idea=idea,
            target_dir=str(target),
            then_develop=False,
            interactive=args.interactive,
        )
        rc = cmd_discover(discover_args)
        if rc != 0:
            return rc
        log("\n✓ Spec generated. Review it with: $EDITOR docs/specs/epics/*.md")
        log("  When ready, run: aa-orchestrator sprint cycle")
    else:
        log("\n✓ Project bootstrapped. When you're ready:")
        log(f"  cd {target}")
        log('  aa-orchestrator discover "<your one-line product idea>"')
    return 0


def cmd_wizard(args: argparse.Namespace) -> int:
    """M12.5: top-level state-aware navigator.

    Detects current pipeline state and offers the next sensible action.
    Loops until the user exits.
    """
    _require_wizard()
    print("\nautonomous-agents wizard")
    print("=" * 60)
    print("Detects your current state and suggests the next step.")
    print("Press Ctrl-C anytime to exit.\n")

    while True:
        try:
            report = _detect_state(Path.cwd())
        except Exception as e:
            log(f"⚠ state detection failed: {type(e).__name__}: {e}")
            return 1

        print(f"State:       {report.state.value}")
        print(f"Summary:     {report.summary}")
        print(f"Next:        {report.next_action}")
        if report.command:
            print(f"Command:     {report.command}")
        else:
            print(f"Command:     (none needed)")
        print()

        if report.state == _PipelineState.ALL_COMPLETE:
            print("🎉 Project shipped. Nothing more to do.")
            return 0

        if report.command is None:
            return 0

        # M13.7: in NONINTERACTIVE mode, just print the suggestion and exit
        # (otherwise the loop runs the suggested command and re-enters forever)
        if os.environ.get("NONINTERACTIVE", "").strip().lower() in ("1", "true", "yes"):
            print(f"\n(noninteractive mode — run `{report.command}` to proceed)")
            return 0

        try:
            choice = _prompt_choice(
                "What would you like to do?",
                options=[
                    f"Run the suggested command: {report.command}",
                    "Show full status (aa-orchestrator status)",
                    "Exit",
                ],
                default_index=0,
            )
        except _WizardAborted:
            print("\naborted.")
            return 0

        if choice.startswith("Exit"):
            return 0
        if choice.startswith("Show full status"):
            cmd_status(argparse.Namespace())
            print()
            continue

        # Execute the suggested command. For multi-step commands we shell out.
        log(f"\n▶ Running: {report.command}\n")
        rc = subprocess.run(["bash", "-c", report.command], check=False).returncode
        if rc != 0:
            log(f"\n⚠ command exited {rc}")
            if not _prompt_yes_no("Continue wizard loop?", default=True):
                return rc
        print()


# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    """M21: Run a pipeline subcommand while exposing a JSON-RPC event stream.

    Events emitted from `log()` and `finalize_story()` are broadcast over a
    WebSocket on `args.host:args.port` as JSON-RPC 2.0 notifications.
    Read-only — subscribers cannot mutate state.

    Example:
        aa-orchestrator serve --port 8765 --cmd develop
        aa-orchestrator serve --port 8765 --cmd status
    """
    try:
        from event_stream import EventBus, WebSocketServer
    except ImportError as e:  # pragma: no cover — defensive; module ships with project
        die(f"event_stream module missing: {e}")

    global _EVENT_BUS
    bus = EventBus()
    server = WebSocketServer(bus, host=args.host, port=args.port)
    server.start()
    _EVENT_BUS = bus
    log(f"📡 Event stream: ws://{args.host}:{server.port}")

    try:
        inner = (getattr(args, "cmd_to_run", None) or "status").lower()
        dispatcher = {
            "develop": cmd_develop,
            "resume": cmd_resume,
            "status": cmd_status,
        }
        if inner not in dispatcher:
            die(f"unknown --cmd: {inner!r} (valid: {sorted(dispatcher)})")
        # Pad the inner command's expected fields with safe defaults so a
        # minimal `serve` namespace works for any inner.
        inner_args = argparse.Namespace(
            cmd=inner,
            spec=getattr(args, "spec", None),
            story=getattr(args, "story", None),
            from_story=getattr(args, "from_story", None),
            dry_run=getattr(args, "dry_run", False),
            force=getattr(args, "force", False),
        )
        return dispatcher[inner](inner_args)
    finally:
        _EVENT_BUS = None
        server.stop()
        # Use plain print — _EVENT_BUS is now None so log() won't fan out anyway
        print(f"[{now_iso()}] 📡 Event stream stopped.", flush=True)


def _format_duration(seconds: float) -> str:
    """M24: format an elapsed-seconds float as '4m32s' / '42s' / '1h12m'."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m{s:02d}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h{m:02d}m"


def _parse_iso_to_unix(ts: Optional[str]) -> Optional[float]:
    """M24: parse an ISO 'YYYY-MM-DDTHH:MM:SSZ' string to a Unix timestamp.
    Returns None for missing/malformed input so callers can skip the duration line."""
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):  # pragma: no cover — defensive against stale schemas
        return None


def _story_duration_sec(story: dict) -> Optional[float]:
    """Compute elapsed seconds for a story. Uses completed_at-started_at when
    both are set, otherwise now-started_at for in-progress stories."""
    start = _parse_iso_to_unix(story.get("started_at"))
    if start is None:
        return None
    end_ts = story.get("completed_at")
    if end_ts:
        end = _parse_iso_to_unix(end_ts)
        if end is None:
            return None
        return max(0.0, end - start)
    return max(0.0, datetime.now(timezone.utc).timestamp() - start)


def cmd_status(_: argparse.Namespace) -> int:
    data = read_progress()
    total = len(all_stories(data))
    by: dict[str, int] = {}
    bucketed: dict[str, list[dict]] = {}
    for s in all_stories(data):
        by[s["status"]] = by.get(s["status"], 0) + 1
        bucketed.setdefault(s["status"], []).append(s)
    print(f"Schema:      {data.get('schema_version')}")
    print(f"Status:      {data.get('status')}")
    print(f"Total:       {total} stories")
    for k in (
        "completed", "in_progress", "review_pass", "test_written",
        "implemented", "pending", "blocked", "failed",
    ):
        if k not in by:
            continue
        # M24: per-bucket detail lines for buckets where context matters.
        if k == "completed":
            durations = [
                (s, _story_duration_sec(s))
                for s in bucketed.get(k, [])
            ]
            durations = [(s, d) for s, d in durations if d is not None]
            if durations:
                avg = sum(d for _, d in durations) / len(durations)
                slowest = max(durations, key=lambda sd: sd[1])
                slow_sid = slowest[0]["id"]
                slow_dur = _format_duration(slowest[1])
                print(
                    f"  {k:14s} {by[k]}     "
                    f"avg {_format_duration(avg)}, slowest {slow_sid} ({slow_dur})"
                )
            else:
                print(f"  {k:14s} {by[k]}")
        elif k == "in_progress":
            print(f"  {k:14s} {by[k]}")
            for s in bucketed.get(k, []):
                dur = _story_duration_sec(s)
                if dur is not None:
                    print(f"    {s['id']} — {_format_duration(dur)} elapsed")
                else:
                    print(f"    {s['id']}")
        elif k in ("blocked", "failed"):
            print(f"  {k:14s} {by[k]}")
            for s in bucketed.get(k, []):
                reason = s.get("failure_reason") or "(no reason recorded)"
                dur = _story_duration_sec(s)
                dur_str = f" (ran {_format_duration(dur)})" if dur is not None else ""
                print(f"    {s['id']} — {reason}{dur_str}")
        else:
            print(f"  {k:14s} {by[k]}")
    print(f"Current:     {data.get('current_story_id')}")
    next_s = next_eligible_story(data)
    print(f"Next:        {next_s['id'] if next_s else '(none)'}")
    print(f"Updated:     {data.get('updated_at')}")
    # M3.2 cost summary
    tracking = data.get("cost_tracking", {})
    if _COST_TRACKING_AVAILABLE and tracking:
        print(f"Cost:        {_cost_format(tracking)}")
        by_agent = tracking.get("by_agent", {})
        if by_agent:
            top = sorted(by_agent.items(), key=lambda kv: -kv[1])[:3]
            top_str = ", ".join(f"{n}=${v:.4f}" for n, v in top)
            print(f"  top:       {top_str}")
    # M1.2 gate failures
    if data.get("status") == "gate_failed" and data.get("gate_failures"):
        print("Gate failures:")
        for f in data["gate_failures"]:
            print(f"  ✗ {f.splitlines()[0][:120]}")
    return 0


# ---------------------------------------------------------------------------
# Production gates (M1.2) — sanity checks after every story completes
# ---------------------------------------------------------------------------

EXIT_GATE_FAILED = 4


def _gate_clean_working_tree(cwd: Optional[str] = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=30, cwd=cwd,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, f"git status failed: {e}"
    if proc.returncode != 0:
        return False, f"git status returned {proc.returncode}: {proc.stderr.strip()[:200]}"
    if proc.stdout.strip():
        return False, f"working tree not clean:\n{proc.stdout.strip()[:600]}"
    return True, "clean"


def _gate_all_tests_pass(cwd: Optional[str] = None) -> tuple[bool, str]:
    cmd = detect_test_command()
    if cmd is None:
        return True, "no test runner detected (skipped)"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=cwd, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, f"test runner error: {e}"
    if proc.returncode != 0:
        tail = (proc.stdout + "\n" + proc.stderr).strip()[-1500:]
        return False, f"tests failed (rc={proc.returncode}):\n{tail}"
    return True, "all tests pass"


def _detect_build_command() -> Optional[list[str]]:
    if Path("package.json").exists():
        try:
            pkg = json.loads(Path("package.json").read_text(encoding="utf-8"))
            if isinstance(pkg.get("scripts"), dict) and "build" in pkg["scripts"]:
                return ["npm", "run", "build"]
        except (json.JSONDecodeError, OSError):  # pragma: no cover — defensive
            pass
    if Path("Cargo.toml").exists():
        return ["cargo", "build", "--release"]
    if Path("go.mod").exists():
        return ["go", "build", "./..."]
    if Path("pyproject.toml").exists():
        # python -m build requires the build package; fall back to compileall
        return ["python3", "-m", "compileall", "-q", "."]
    return None


def _gate_build_succeeds(cwd: Optional[str] = None) -> tuple[bool, str]:
    cmd = _detect_build_command()
    if cmd is None:
        return True, "no build command detected (skipped)"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, cwd=cwd, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, f"build error: {e}"
    if proc.returncode != 0:
        tail = (proc.stdout + "\n" + proc.stderr).strip()[-1500:]  # pragma: no cover — covered conceptually via test_gate_build_fails
        return False, f"build failed (rc={proc.returncode}):\n{tail}"
    return True, "build succeeds"


_GATE_REGISTRY: dict[str, Any] = {
    "clean_working_tree": _gate_clean_working_tree,
    "all_tests_pass": _gate_all_tests_pass,
    "build_succeeds": _gate_build_succeeds,
}


def run_production_gates(data: dict) -> tuple[bool, list[str]]:
    """Run the configured production-ready gates. Returns (ok, failure_messages)."""
    cfg = data.get("_config", {}) if isinstance(data.get("_config"), dict) else {}
    # Re-read config to pick up project-level overrides
    raw_cfg: dict = {}
    if CONFIG_FILE.exists():
        try:
            raw_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):  # pragma: no cover — defensive
            raw_cfg = {}
    gates_cfg = raw_cfg.get("production_gates", {})
    if not isinstance(gates_cfg, dict):  # pragma: no cover — defensive
        gates_cfg = {}
    if gates_cfg.get("enabled") is False:
        log("  production gates disabled in config — skipping")
        return True, []
    gate_names = gates_cfg.get("gates", ["clean_working_tree"])
    if not isinstance(gate_names, list) or not gate_names:
        return True, []

    failures: list[str] = []
    log("\nRunning production-ready gates...")
    for name in gate_names:
        fn = _GATE_REGISTRY.get(name)
        if fn is None:
            log(f"  ⚠ unknown gate: {name} (skipping)")
            continue
        try:
            ok, msg = fn()
        except Exception as e:
            failures.append(f"{name}: gate crashed: {type(e).__name__}: {e}")
            log(f"  ✗ {name}: gate crashed: {e}")
            continue
        if ok:
            log(f"  ✓ {name}: {msg.splitlines()[0][:120]}")
        else:
            failures.append(f"{name}: {msg}")
            log(f"  ✗ {name}: {msg.splitlines()[0][:120]}")
    return (len(failures) == 0, failures)


# ---------------------------------------------------------------------------
# @review-product feedback loop (M2.2)
# ---------------------------------------------------------------------------

def _max_product_review_cycles() -> int:
    if not CONFIG_FILE.exists():
        return 2
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):  # pragma: no cover — defensive
        return 2
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    try:
        return int(pipe.get("max_product_review_cycles", 2))
    except (TypeError, ValueError):
        return 2


def _product_review_enabled() -> bool:
    if not CONFIG_FILE.exists():
        return False  # opt-in by default — adds latency and cost
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    pipe = cfg.get("pipeline", {}) if isinstance(cfg, dict) else {}
    return bool(pipe.get("product_review_enabled", False))


_SPEC_BLOCK_RE = re.compile(
    r"```spec-block\s*\n(.*?)\n```",
    re.DOTALL,
)


def parse_review_product_output(out: str) -> dict:
    """Parse @review-product output into a structured verdict.

    Returns one of:
        {"verdict": "PASS_AS_IS"}
        {"verdict": "FOLLOW_UP_STORIES", "story_blocks": [str, ...]}
        {"verdict": "REOPEN", "story_ids": [str, ...]}
        {"verdict": "UNKNOWN", "raw": "<full output>"}
    """
    upper = out.upper()
    if "VERDICT: PASS_AS_IS" in upper or "VERDICT:PASS_AS_IS" in upper:
        return {"verdict": "PASS_AS_IS"}
    if "VERDICT: FOLLOW_UP_STORIES" in upper or "VERDICT:FOLLOW_UP_STORIES" in upper:
        blocks = [m.group(1).strip() for m in _SPEC_BLOCK_RE.finditer(out)]
        return {"verdict": "FOLLOW_UP_STORIES", "story_blocks": blocks}
    m = re.search(r"VERDICT:\s*REOPEN\s+([A-Z0-9_\-, ]+)", out, re.IGNORECASE)
    if m:
        ids = [s.strip() for s in m.group(1).split(",") if s.strip()]
        return {"verdict": "REOPEN", "story_ids": ids}
    return {"verdict": "UNKNOWN", "raw": out[-500:]}


def run_product_review(data: dict) -> tuple[str, dict]:
    """Invoke @review-product and return (verdict, parsed_output_dict)."""
    completed = [s for s in all_stories(data) if s.get("status") == "completed"]
    prompt = (
        "All stories completed and production-readiness gates passed. "
        "Review the finished product holistically.\n\n"
        "Completed stories:\n"
        + "\n".join(
            f"- {s['id']}: {s.get('title', '?')} "
            f"(files: {', '.join((s.get('artifacts') or {}).get('implementation_files', []) or ['n/a'])})"
            for s in completed
        )
        + "\n\nSee docs/specs/PROJECT_CONTEXT.md for the full history if needed."
    )
    out = call_agent("review-product", prompt)
    parsed = parse_review_product_output(out)
    log(f"  @review-product verdict: {parsed['verdict']}")
    return parsed["verdict"], parsed


def run_loop(
    data: dict, only_story: Optional[str] = None, from_story: Optional[str] = None
) -> int:
    global GLOBAL_PERSIST_STATE
    data["status"] = "in_progress"
    data = persist(data)
    GLOBAL_PERSIST_STATE = data
    deadline = time.monotonic() + OUTER_TIMEOUT_SEC
    started = False if from_story else True
    while True:
        if only_story:
            target = find_story(data, only_story)
            if not target:
                die(f"Story {only_story} not found")
            if target["status"] in ("completed", "blocked", "failed"):
                log(f"Story {only_story} already terminal ({target['status']}).")
                return 0
            data = run_story(data, target)
            GLOBAL_PERSIST_STATE = data
            return 0

        remaining = deadline - time.monotonic()
        if remaining < 120:
            log(
                f"\n⏱ Approaching outer timeout "
                f"({remaining:.0f}s remaining of {OUTER_TIMEOUT_SEC}s). "
                "Persisting state — run `resume` to continue."
            )
            data = persist(data)
            GLOBAL_PERSIST_STATE = data
            return EXIT_MORE_WORK

        next_story = next_eligible_story(data)
        if next_story is None:
            pending = [s for s in all_stories(data) if s["status"] == "pending"]
            if not pending:
                # M20: real status breakdown — the old "✓ All stories complete."
                # line lied when stories actually failed/blocked. Compute the
                # histogram and use ⚠ prefix + exit 2 whenever any story
                # ended in a non-completed terminal state.
                terminal = all_stories(data)
                n_completed = sum(1 for s in terminal if s["status"] == "completed")
                n_failed = sum(1 for s in terminal if s["status"] == "failed")
                n_blocked = sum(1 for s in terminal if s["status"] == "blocked")
                if n_failed or n_blocked:
                    log(
                        f"\n⚠ Pipeline finished with failures "
                        f"({n_completed} completed, {n_failed} failed, {n_blocked} blocked). "
                        "Run `aa-orchestrator status` for per-story details."
                    )
                    data["status"] = "failed"
                    persist(data)
                    cmd_status(argparse.Namespace())
                    return 2
                log(f"\n✓ Pipeline finished ({n_completed} completed).")
                gates_ok, gate_failures = run_production_gates(data)
                if not gates_ok:
                    data["status"] = "gate_failed"
                    data["gate_failures"] = gate_failures
                    persist(data)
                    log(
                        f"\n✗ Production gates failed ({len(gate_failures)} issue(s)). "
                        "Fix the issues, then re-run `aa-orchestrator develop`."
                    )
                    cmd_status(argparse.Namespace())
                    return EXIT_GATE_FAILED

                # M2.2: optional @review-product loop
                if _product_review_enabled():
                    cycle = data.get("product_review_cycles", 0)
                    max_cycles = _max_product_review_cycles()
                    if cycle < max_cycles:
                        verdict, parsed = run_product_review(data)
                        data["product_review_cycles"] = cycle + 1
                        if verdict == "REOPEN":
                            reopened = []
                            for sid in parsed.get("story_ids", []):
                                s = find_story(data, sid)
                                if s and s["status"] == "completed":
                                    s["status"] = "pending"
                                    if sid in data.get("completed_stories", []):
                                        data["completed_stories"].remove(sid)
                                    reopened.append(sid)
                            if reopened:
                                append_execution_log(
                                    data,
                                    f"product_review_reopen cycle={cycle + 1} stories={reopened}"
                                )
                                log(f"  ↻ reopening: {', '.join(reopened)}")
                                data = persist(data)
                                continue  # back into the main loop
                        elif verdict == "FOLLOW_UP_STORIES":
                            log(
                                "\n📋 @review-product suggested follow-up stories. "
                                "These are NOT auto-appended (review required)."
                            )
                            for i, block in enumerate(parsed.get("story_blocks", []), 1):
                                log(f"\n  --- suggested follow-up #{i} ---\n{block}\n")
                            data["product_review_suggestions"] = parsed.get("story_blocks", [])
                        # PASS_AS_IS or UNKNOWN → proceed to completion
                    else:
                        log(f"  ⚠ product review cycle cap reached ({max_cycles}) — proceeding to completion")

                data["status"] = "completed"
                persist(data)
                cmd_status(argparse.Namespace())
                return 0
            log(
                f"\n⏸ {len(pending)} pending story(ies) blocked on unmet deps. "
                "Inspect and retry."
            )
            data["status"] = "blocked"
            persist(data)
            return 2

        if not started:
            if next_story["id"] == from_story:
                started = True
            else:
                die(
                    f"--from {from_story} but next eligible story is {next_story['id']}; "
                    "cannot skip ahead"
                )

        try:
            data = run_story(data, next_story)
            GLOBAL_PERSIST_STATE = data
            # M8.3: deterministic watcher every story (cheap, no LLM)
            run_watcher(data)
        except AgentError as e:
            log(f"  ✗ agent failure during {next_story['id']}: {e}")
            data = read_progress()
            data = finalize_story(
                data,
                next_story["id"],
                "failed",
                f"agent_error:{e.agent}:{e.detail[:120]}",
            )
            GLOBAL_PERSIST_STATE = data
            continue
        except KeyboardInterrupt:  # pragma: no cover — user-triggered SIGINT
            raise
        except Exception as e:
            # Asymmetric with AgentError above (which `continue`s): AgentError is
            # an expected per-story failure mode and the pipeline should move on.
            # Any other exception is a bug — orchestrator state may be corrupted,
            # so we finalize this story as failed, persist, and halt the loop.
            import traceback
            log(f"  ✗ unexpected error during {next_story['id']}: {type(e).__name__}: {e}")
            log(traceback.format_exc())
            try:
                data = read_progress()
                data = finalize_story(
                    data,
                    next_story["id"],
                    "failed",
                    f"unexpected_error:{type(e).__name__}:{str(e)[:120]}",
                )
                GLOBAL_PERSIST_STATE = data
            except Exception as fexc:  # pragma: no cover — nested finalize failure
                log(f"  (also failed to finalize: {fexc})")
            return 1


def _graceful_shutdown(signum: int, frame: Any) -> None:
    sig_name = signal.Signals(signum).name
    log(f"\n⚠ Received {sig_name} — persisting state before exit")
    if GLOBAL_PERSIST_STATE is not None:
        try:
            persist(GLOBAL_PERSIST_STATE)
            log("  state persisted to progress.json")
        except Exception as exc:
            log(f"  persist failed: {exc}")
    sys.exit(EXIT_MORE_WORK)


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)


def main() -> int:
    _install_signal_handlers()

    parser = argparse.ArgumentParser(prog="orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    dev = sub.add_parser("develop", help="Run the full pipeline")
    dev.add_argument("--spec", default=None)
    dev.add_argument("--story", default=None)
    dev.add_argument("--from", dest="from_story", default=None)
    dev.add_argument("--dry-run", action="store_true")
    dev.add_argument("--force", action="store_true")
    dev.add_argument(
        "--spec-llm-fallback",
        action="store_true",
        help="Use the LLM @spec agent instead of deterministic parsing. "
             "For legacy/unstructured specs only.",
    )

    res = sub.add_parser("resume", help="Continue an interrupted run")
    res.add_argument("--retry-failed", action="store_true")
    res.add_argument("--retry-blocked", action="store_true")
    res.add_argument("--story", default=None)

    sub.add_parser("status", help="Show pipeline progress")
    sub.add_parser("validate", help="Pre-flight check: validate docs/specs/ without running")
    sub.add_parser("health-check", help="Verify runner CLI, config, agents, skills, and git state (M11.3)")

    sub.add_parser("setup", help="One-time machine setup wizard: Python, git, runner, PATH (M12.2)")
    sub.add_parser("wizard", help="Interactive navigator: detects state and suggests next action (M12.5)")

    new = sub.add_parser("new", help="Bootstrap a new project (mkdir + git init + init.sh + optional discover) (M12.3)")
    new.add_argument("name", nargs="?", default=None, help="Project name / directory")
    new.add_argument("--runner", choices=["claude", "opencode"], default=None, help="LLM runner for the new project")
    new.add_argument("--idea", default=None, help="One-line product idea — skips the interactive prompt")
    new.add_argument("--interactive", action="store_true",
                     help="Ask clarifying questions before generating the spec")

    rev = sub.add_parser("revisit", help="Reopen a terminal story for another pass (M2.1)")
    rev.add_argument("story", help="Story ID to revisit (e.g., STORY-login)")
    rev.add_argument("--reason", default=None, help="Why is this story being revisited?")
    rev.add_argument(
        "--cascade-dependents", action="store_true",
        help="Also reopen all stories that depend on this one (default: off)",
    )

    disc = sub.add_parser("discover", help="Generate a SCRUM spec tree from a one-line product idea (M5.1)")
    disc.add_argument("idea", help="Product idea (e.g., 'a todo app with email auth')")
    disc.add_argument("--target-dir", default=None, help="Where to write the spec (default: cwd)")
    disc.add_argument("--then-develop", action="store_true", help="Chain straight into `develop` on success")
    disc.add_argument("--interactive", action="store_true",
                     help="Ask clarifying questions (user / scale / tech stack) before generating the spec (M12.4)")

    sp = sub.add_parser("sprint", help="Sprint operations: plan|start|end|status|cycle (M6)")
    sp.add_argument("action", choices=["plan", "start", "end", "status", "cycle"])
    sp.add_argument("--interactive", action="store_true",
                    help="(cycle only) Pause between sprints for approval (M12.6)")

    adr = sub.add_parser("adr", help="Propose an Architecture Decision Record (M7.2)")
    adr.add_argument("question", help="The design question this ADR answers")
    adr.add_argument("--story", default=None, help="Related story ID (optional)")

    ref = sub.add_parser("refine", help="Split a large story into smaller ones via @architect (M7.3)")
    ref.add_argument("story", help="Story ID to refine (e.g., STORY-large)")

    sub.add_parser("rfc", help="Process open RFCs via @architect (M9.1)")

    ag = sub.add_parser("agent", help="Ad-hoc agent invocation with a named skill (M10.6)")
    ag.add_argument("agent", help="Agent name (e.g., engineer, architect)")
    ag.add_argument("--skill", default=None, help="Skill ID to dispatch")
    ag.add_argument("prompt", help="User prompt for the agent")

    srv = sub.add_parser("serve", help="Run a pipeline command while exposing a JSON-RPC event stream over WebSocket (M21)")
    srv.add_argument("--port", type=int, default=8765, help="Port to bind (default 8765; 0 = ephemeral)")
    srv.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1)")
    srv.add_argument("--cmd", dest="cmd_to_run", default="status",
                     choices=["develop", "resume", "status"],
                     help="Pipeline subcommand to run while the stream is open (default: status)")

    args = parser.parse_args()
    try:
        if args.cmd == "develop":
            return cmd_develop(args)
        if args.cmd == "resume":
            return cmd_resume(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "validate":
            return cmd_validate(args)
        if args.cmd == "health-check":
            return cmd_health_check(args)
        if args.cmd == "setup":
            return cmd_setup(args)
        if args.cmd == "new":
            return cmd_new(args)
        if args.cmd == "wizard":
            return cmd_wizard(args)
        if args.cmd == "revisit":
            return cmd_revisit(args)
        if args.cmd == "discover":
            return cmd_discover(args)
        if args.cmd == "sprint":
            return cmd_sprint(args)
        if args.cmd == "adr":
            return cmd_adr(args)
        if args.cmd == "refine":
            return cmd_refine(args)
        if args.cmd == "rfc":
            return cmd_rfc(args)
        if args.cmd == "agent":
            return cmd_agent(args)
        if args.cmd == "serve":
            return cmd_serve(args)
    except KeyboardInterrupt:
        log("\n⚠ Interrupted — state preserved in progress.json. Run `resume` to continue.")
        return EXIT_MORE_WORK
    except SystemExit:  # pragma: no cover — re-raise sys.exit() from die()
        raise
    except AgentError as e:
        # Budget-exceeded surfaces as AgentError with a recognizable detail (M3.2)
        if "budget exceeded" in e.detail.lower():
            log(f"\n✗ Pipeline halted — {e.detail}")
            return EXIT_BUDGET_EXCEEDED
        log(f"\n✗ Agent failed: {e}")
        return 1
    except Exception as exc:
        import traceback
        log(f"\n✗ Unhandled exception in cmd_{args.cmd}: {type(exc).__name__}: {exc}")
        log(traceback.format_exc())
        if GLOBAL_PERSIST_STATE is not None:
            try:
                persist(GLOBAL_PERSIST_STATE)
                log("  state persisted to progress.json — run `resume` to continue")
            except Exception as persist_exc:
                log(f"  (state persist also failed: {persist_exc})")
        return 1
    return 1  # pragma: no cover — unreachable fallback


if __name__ == "__main__":  # pragma: no cover — entry-point guard
    sys.exit(main())
