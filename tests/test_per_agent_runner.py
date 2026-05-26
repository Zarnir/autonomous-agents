"""M25 — per-agent runner dispatch tests.

Covers:
  - `_get_runner_for_agent(name)` resolution order (per-agent env > config →
    project-wide env > config → auto-detect).
  - Per-runner-name caching (calling for two agents with the same runner name
    returns the SAME cached instance; calling for different runners returns
    two distinct instances).
  - `load_config()` reads `pipeline.agent_runners`, validates each value, and
    populates the module-level `_AGENT_RUNNERS` dict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    # Reset state across tests
    orchestrator._RUNNER_INSTANCES = {}
    orchestrator._AGENT_RUNNERS = {}
    orchestrator._CONFIG_RUNNER = None
    yield orchestrator
    # Cleanup
    orchestrator._RUNNER_INSTANCES = {}
    orchestrator._AGENT_RUNNERS = {}
    orchestrator._CONFIG_RUNNER = None


class _StubRunner:
    """Stub Runner whose `.name` records the preference it was constructed with."""
    def __init__(self, name: str = "stub"):
        self.name = name


def _patch_select_runner(monkeypatch, orch) -> list:
    """Patch `orch.select_runner` to return a fresh _StubRunner per call.
    Returns the list of `preference` args passed for assertions."""
    calls: list = []
    def fake_select(preference=None):
        calls.append(preference)
        return _StubRunner(name=preference or "auto")
    monkeypatch.setattr(orch, "select_runner", fake_select)
    return calls


# ---------------------------------------------------------------------------
# Resolution order
# ---------------------------------------------------------------------------

def test_get_runner_for_agent_uses_explicit_agent_runners_mapping(import_orch, monkeypatch):
    orch = import_orch
    orch._AGENT_RUNNERS = {"planner": "claude"}
    calls = _patch_select_runner(monkeypatch, orch)
    monkeypatch.delenv("AA_RUNNER", raising=False)
    monkeypatch.delenv("AA_RUNNER_PLANNER", raising=False)

    runner = orch._get_runner_for_agent("planner")

    assert runner.name == "claude"
    assert calls == ["claude"]


def test_get_runner_for_agent_falls_back_to_config_runner_default(import_orch, monkeypatch):
    orch = import_orch
    orch._AGENT_RUNNERS = {}  # no entry for "make"
    orch._CONFIG_RUNNER = "opencode"
    calls = _patch_select_runner(monkeypatch, orch)
    monkeypatch.delenv("AA_RUNNER", raising=False)
    monkeypatch.delenv("AA_RUNNER_MAKE", raising=False)

    runner = orch._get_runner_for_agent("make")

    assert runner.name == "opencode"
    assert calls == ["opencode"]


def test_get_runner_for_agent_falls_back_to_env_var_when_no_config(import_orch, monkeypatch):
    orch = import_orch
    orch._AGENT_RUNNERS = {}
    orch._CONFIG_RUNNER = None
    monkeypatch.setenv("AA_RUNNER", "claude")
    monkeypatch.delenv("AA_RUNNER_CHECK", raising=False)
    calls = _patch_select_runner(monkeypatch, orch)

    runner = orch._get_runner_for_agent("check")

    assert runner.name == "claude"
    assert calls == ["claude"]


def test_per_agent_env_override_wins_over_config(import_orch, monkeypatch):
    """`AA_RUNNER_<NAME>` env beats `pipeline.agent_runners` — escape hatch for testing."""
    orch = import_orch
    orch._AGENT_RUNNERS = {"planner": "claude"}
    monkeypatch.setenv("AA_RUNNER_PLANNER", "opencode")
    calls = _patch_select_runner(monkeypatch, orch)

    runner = orch._get_runner_for_agent("planner")

    assert runner.name == "opencode"
    assert calls == ["opencode"]


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------

def test_get_runner_for_agent_caches_per_runner_name(import_orch, monkeypatch):
    """Two agents both mapped to 'claude' → select_runner called once; both get the same cached instance."""
    orch = import_orch
    orch._AGENT_RUNNERS = {"planner": "claude", "architect": "claude"}
    calls = _patch_select_runner(monkeypatch, orch)
    monkeypatch.delenv("AA_RUNNER", raising=False)
    monkeypatch.delenv("AA_RUNNER_PLANNER", raising=False)
    monkeypatch.delenv("AA_RUNNER_ARCHITECT", raising=False)

    r1 = orch._get_runner_for_agent("planner")
    r2 = orch._get_runner_for_agent("architect")

    assert r1 is r2, "two agents on the same runner name must share the cached instance"
    assert calls == ["claude"], f"expected one select_runner call, got {calls}"


def test_get_runner_for_agent_caches_separately_for_different_runners(import_orch, monkeypatch):
    orch = import_orch
    orch._AGENT_RUNNERS = {"planner": "claude", "make": "opencode"}
    calls = _patch_select_runner(monkeypatch, orch)
    monkeypatch.delenv("AA_RUNNER", raising=False)
    monkeypatch.delenv("AA_RUNNER_PLANNER", raising=False)
    monkeypatch.delenv("AA_RUNNER_MAKE", raising=False)

    r1 = orch._get_runner_for_agent("planner")
    r2 = orch._get_runner_for_agent("make")

    assert r1 is not r2
    assert r1.name == "claude" and r2.name == "opencode"
    assert sorted(calls) == ["claude", "opencode"]


# ---------------------------------------------------------------------------
# load_config integration (Part B)
# ---------------------------------------------------------------------------

def _write_config(project_root: Path, pipeline: dict) -> None:
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": pipeline}),
        encoding="utf-8",
    )


def test_load_config_populates_agent_runners(import_orch, project_root, monkeypatch):
    """`pipeline.agent_runners` is read into `_AGENT_RUNNERS` for each valid runner-name."""
    orch = import_orch
    _write_config(project_root, {
        "agent_runners": {
            "planner": "claude",
            "make": "opencode",
        },
    })
    monkeypatch.setattr(orch, "CONFIG_FILE", project_root / ".opencode" / "config.json")

    orch.load_config()

    assert orch._AGENT_RUNNERS.get("planner") == "claude"
    assert orch._AGENT_RUNNERS.get("make") == "opencode"


def test_load_config_validates_unknown_runner_name(import_orch, project_root, monkeypatch, capsys):
    """An unknown runner value (e.g. 'bogus') is rejected — warning + skipped, NOT added."""
    orch = import_orch
    _write_config(project_root, {
        "agent_runners": {
            "planner": "bogus",
            "make": "opencode",  # this one's valid, should still land
        },
    })
    monkeypatch.setattr(orch, "CONFIG_FILE", project_root / ".opencode" / "config.json")

    orch.load_config()

    assert "planner" not in orch._AGENT_RUNNERS, "unknown runner names must be skipped, not added"
    assert orch._AGENT_RUNNERS.get("make") == "opencode"
    err = capsys.readouterr().err
    assert ("bogus" in err.lower() or "planner" in err.lower() or "agent_runners" in err.lower())


def test_load_config_agent_runners_absent_keeps_empty_default(import_orch, project_root, monkeypatch):
    """When `agent_runners` is absent from config, `_AGENT_RUNNERS` stays empty (legacy behavior preserved)."""
    orch = import_orch
    _write_config(project_root, {"runner": "opencode"})  # no agent_runners
    monkeypatch.setattr(orch, "CONFIG_FILE", project_root / ".opencode" / "config.json")

    orch.load_config()

    assert orch._AGENT_RUNNERS == {}
