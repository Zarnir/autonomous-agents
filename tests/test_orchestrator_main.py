"""M15.8 final: main() argparse + runner select edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


# ---------------------------------------------------------------------------
# main() — argparse dispatch
# ---------------------------------------------------------------------------

def test_main_dispatches_to_status(import_orch, monkeypatch):
    called = {}
    def fake(args):
        called["status"] = True
        return 0
    monkeypatch.setattr(import_orch, "cmd_status", fake)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "status"])
    rc = import_orch.main()
    assert rc == 0
    assert called.get("status")


def test_main_dispatches_to_validate(import_orch, monkeypatch):
    called = {}
    def fake(args):
        called["v"] = True
        return 0
    monkeypatch.setattr(import_orch, "cmd_validate", fake)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "validate"])
    assert import_orch.main() == 0
    assert called.get("v")


def test_main_dispatches_to_adr(import_orch, monkeypatch):
    called = {}
    def fake(args):
        called["question"] = args.question
        return 0
    monkeypatch.setattr(import_orch, "cmd_adr", fake)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "adr", "use sqlite vs postgres"])
    assert import_orch.main() == 0
    assert called["question"] == "use sqlite vs postgres"


def test_main_dispatches_to_sprint_plan(import_orch, monkeypatch):
    called = {}
    def fake(args):
        called["action"] = args.action
        return 0
    monkeypatch.setattr(import_orch, "cmd_sprint", fake)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "sprint", "plan"])
    assert import_orch.main() == 0
    assert called["action"] == "plan"


def test_main_dispatches_to_agent(import_orch, monkeypatch):
    called = {}
    def fake(args):
        called["agent"] = args.agent
        called["skill"] = args.skill
        called["prompt"] = args.prompt
        return 0
    monkeypatch.setattr(import_orch, "cmd_agent", fake)
    monkeypatch.setattr(sys, "argv", [
        "orchestrator", "agent", "engineer", "--skill", "fix-bug", "test prompt"
    ])
    assert import_orch.main() == 0
    assert called["agent"] == "engineer"
    assert called["skill"] == "fix-bug"
    assert called["prompt"] == "test prompt"


def test_main_handles_keyboard_interrupt(import_orch, monkeypatch):
    def boom(args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(import_orch, "cmd_status", boom)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "status"])
    rc = import_orch.main()
    assert rc == import_orch.EXIT_MORE_WORK


def test_main_handles_budget_exceeded(import_orch, monkeypatch):
    def boom(args):
        raise import_orch.AgentError("agent", "budget exceeded: $5.00 > $1.00")
    monkeypatch.setattr(import_orch, "cmd_status", boom)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "status"])
    rc = import_orch.main()
    assert rc == import_orch.EXIT_BUDGET_EXCEEDED


def test_main_handles_agent_error(import_orch, monkeypatch):
    def boom(args):
        raise import_orch.AgentError("agent", "some failure")
    monkeypatch.setattr(import_orch, "cmd_status", boom)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "status"])
    rc = import_orch.main()
    assert rc == 1


def test_main_handles_unexpected_exception(import_orch, monkeypatch):
    def boom(args):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(import_orch, "cmd_status", boom)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "status"])
    rc = import_orch.main()
    assert rc == 1


# ---------------------------------------------------------------------------
# select_runner edge cases
# ---------------------------------------------------------------------------

def test_select_runner_no_path_raises(monkeypatch):
    from runners import select_runner
    monkeypatch.delenv("AA_RUNNER", raising=False)
    monkeypatch.delenv("OPENCODE_AGENT_CMD", raising=False)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(RuntimeError):
        select_runner(None)


def test_select_runner_falls_back_to_opencode_when_only_opencode_on_path(monkeypatch):
    from runners import select_runner, OpenCodeRunner
    monkeypatch.delenv("AA_RUNNER", raising=False)
    monkeypatch.delenv("OPENCODE_AGENT_CMD", raising=False)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/" + cmd if cmd == "opencode" else None)
    runner = select_runner(None)
    assert isinstance(runner, OpenCodeRunner)


# ---------------------------------------------------------------------------
# OpenCodeRunner skill injection (simple mode)
# ---------------------------------------------------------------------------

def test_opencode_runner_skill_injection_in_simple_mode(tmp_path, monkeypatch):
    from runners import OpenCodeRunner
    import subprocess

    agents = tmp_path / ".opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "eng.md").write_text(
        "---\n"
        "description: eng\n"
        "permission:\n"
        "  edit: deny\n"
        "skills:\n"
        "  - id: fix-bug\n"
        "    description: Fix a known bug\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    runner = OpenCodeRunner(agent_cmd="opencode-bin run --agent", use_pty=False)
    captured = {}

    def fake_run(cmd, **kw):
        captured["input"] = kw.get("input", "")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner.run("eng", "user prompt", timeout=5, skill="fix-bug")
    assert "Skill: fix-bug" in captured["input"]
    assert "user prompt" in captured["input"]


def test_opencode_runner_skill_injection_swallows_missing_agent(tmp_path, monkeypatch):
    """If agent file is missing, OpenCodeRunner should still run (no preamble)."""
    from runners import OpenCodeRunner
    import subprocess

    monkeypatch.chdir(tmp_path)

    runner = OpenCodeRunner(agent_cmd="opencode-bin run --agent", use_pty=False)
    captured = {}

    def fake_run(cmd, **kw):
        captured["input"] = kw.get("input", "")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner.run("nonexistent_agent", "user prompt", timeout=5, skill="fix-bug")
    assert captured["input"] == "user prompt"
