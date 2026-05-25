"""M16 final: small-but-high-leverage coverage targets.

Pushes orchestrator from 87% toward 90%+.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import namedtuple
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


# ---------------------------------------------------------------------------
# run_tests_independently
# ---------------------------------------------------------------------------

def test_run_tests_independently_returns_false_when_no_runner_detected(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "detect_test_command", lambda: None)
    ok, detail = orch.run_tests_independently(["tests/test_x.py"])
    assert ok is False
    assert detail == "no_test_runner_detected"


def test_run_tests_independently_appends_pytest_files(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "detect_test_command", lambda: ["pytest"])

    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="all green\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    ok, _ = orch.run_tests_independently(["tests/test_a.py", "tests/test_b.py"])
    assert ok is True
    assert "tests/test_a.py" in captured["cmd"]
    assert "tests/test_b.py" in captured["cmd"]


def test_run_tests_independently_appends_npm_filter(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "detect_test_command", lambda: ["npm", "test"])

    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    orch.run_tests_independently(["tests/foo.test.ts"])
    cmd_str = " ".join(captured["cmd"])
    assert "--testPathPattern=" in cmd_str


def test_run_tests_independently_dies_on_filenotfound(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "detect_test_command", lambda: ["pytest"])

    def fake_run(cmd, **kw):
        raise FileNotFoundError("pytest")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit):
        orch.run_tests_independently(["x.py"])


def test_run_tests_independently_returns_false_on_timeout(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "detect_test_command", lambda: ["pytest"])

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 900)
    monkeypatch.setattr(subprocess, "run", fake_run)

    ok, detail = orch.run_tests_independently(["x.py"])
    assert ok is False
    assert "timeout" in detail.lower()


# ---------------------------------------------------------------------------
# _project_context_enabled / _project_context_max_entries
# ---------------------------------------------------------------------------

def test_project_context_enabled_handles_malformed_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("not json", encoding="utf-8")
    assert import_orch._project_context_enabled() is True


def test_project_context_max_entries_default(import_orch, project_root):
    assert import_orch._project_context_max_entries() == 10


def test_project_context_max_entries_reads_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"project_context_max_entries": 25}}), encoding="utf-8"
    )
    assert import_orch._project_context_max_entries() == 25


def test_project_context_max_entries_malformed_returns_default(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("bad", encoding="utf-8")
    assert import_orch._project_context_max_entries() == 10


def test_project_context_max_entries_invalid_value_returns_default(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"project_context_max_entries": "many"}}', encoding="utf-8"
    )
    assert import_orch._project_context_max_entries() == 10


# ---------------------------------------------------------------------------
# cmd_wizard execute-command branches
# ---------------------------------------------------------------------------

FakeReport = namedtuple("FakeReport", ["state", "summary", "next_action", "command"])


def test_cmd_wizard_exit_choice_returns_0(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)
    monkeypatch.setattr(orch, "_detect_state",
                        lambda root: FakeReport(orch._PipelineState.NOT_INITIALIZED,
                                                  "x", "y", "aa-orchestrator new"))
    monkeypatch.setattr(orch, "_prompt_choice", lambda *a, **kw: "Exit")
    rc = orch.cmd_wizard(argparse.Namespace())
    assert rc == 0


def test_cmd_wizard_show_status_loops_then_exits(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)

    state_calls = {"n": 0}
    def fake_detect(root):
        state_calls["n"] += 1
        return FakeReport(orch._PipelineState.NOT_INITIALIZED, "x", "y", "aa-orchestrator new")
    monkeypatch.setattr(orch, "_detect_state", fake_detect)

    answers = iter(["Show full status (aa-orchestrator status)", "Exit"])
    monkeypatch.setattr(orch, "_prompt_choice", lambda *a, **kw: next(answers))
    monkeypatch.setattr(orch, "cmd_status", lambda args: 0)

    rc = orch.cmd_wizard(argparse.Namespace())
    assert rc == 0
    assert state_calls["n"] == 2


def test_cmd_wizard_run_command_success_loops(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)

    state_calls = {"n": 0}
    def fake_detect(root):
        state_calls["n"] += 1
        if state_calls["n"] >= 2:
            return FakeReport(orch._PipelineState.ALL_COMPLETE, "done", "x", None)
        return FakeReport(orch._PipelineState.NOT_INITIALIZED, "x", "y", "echo hi")
    monkeypatch.setattr(orch, "_detect_state", fake_detect)

    monkeypatch.setattr(orch, "_prompt_choice",
                        lambda *a, **kw: "Run the suggested command: echo hi")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "ok", ""))

    rc = orch.cmd_wizard(argparse.Namespace())
    assert rc == 0
    assert state_calls["n"] >= 2


def test_cmd_wizard_run_command_failure_and_decline_continue(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)
    monkeypatch.setattr(orch, "_detect_state",
                        lambda root: FakeReport(orch._PipelineState.NOT_INITIALIZED,
                                                  "x", "y", "false"))
    monkeypatch.setattr(orch, "_prompt_choice",
                        lambda *a, **kw: "Run the suggested command: false")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "fail"))
    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: False)

    rc = orch.cmd_wizard(argparse.Namespace())
    assert rc == 1


def test_cmd_wizard_wizard_aborted_during_choice(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)
    monkeypatch.setattr(orch, "_detect_state",
                        lambda root: FakeReport(orch._PipelineState.NOT_INITIALIZED,
                                                  "x", "y", "echo hi"))

    def raise_abort(*a, **kw):
        raise orch._WizardAborted()
    monkeypatch.setattr(orch, "_prompt_choice", raise_abort)

    rc = orch.cmd_wizard(argparse.Namespace())
    assert rc == 0


# ---------------------------------------------------------------------------
# main() unhandled-exception persist
# ---------------------------------------------------------------------------

def test_main_unhandled_exception_attempts_persist(import_orch, monkeypatch):
    import sys as _sys
    orch = import_orch

    def boom(args):
        raise RuntimeError("crash")
    monkeypatch.setattr(orch, "cmd_status", boom)

    persist_called = {"n": 0}
    def fake_persist(data):
        persist_called["n"] += 1
        return data
    monkeypatch.setattr(orch, "persist", fake_persist)

    # monkeypatch.setattr resets the global on test teardown
    monkeypatch.setattr(orch, "GLOBAL_PERSIST_STATE", {"epics": [], "version": 1})
    monkeypatch.setattr(_sys, "argv", ["orchestrator", "status"])
    rc = orch.main()
    assert rc == 1
    assert persist_called["n"] >= 1


def test_main_unhandled_exception_persist_also_fails(import_orch, monkeypatch):
    import sys as _sys
    orch = import_orch

    def boom(args):
        raise RuntimeError("crash")
    monkeypatch.setattr(orch, "cmd_status", boom)

    def bad_persist(data):
        raise OSError("disk full")
    monkeypatch.setattr(orch, "persist", bad_persist)

    monkeypatch.setattr(orch, "GLOBAL_PERSIST_STATE", {"epics": [], "version": 1})
    monkeypatch.setattr(_sys, "argv", ["orchestrator", "status"])
    rc = orch.main()
    assert rc == 1
