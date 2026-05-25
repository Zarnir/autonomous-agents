"""M16 followup: cmd_setup branch coverage + cmd_wizard interactive branches."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest


# sys.version_info instances can't be created; use a namedtuple stand-in
VersionInfo = namedtuple("VersionInfo", ["major", "minor", "micro", "releaselevel", "serial"])


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _mock_subprocess_all_ok(monkeypatch, email="dev@example.com", name="Dev Person"):
    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="git version 2.40.0\n", stderr="")
        if cmd[:4] == ["git", "config", "--global", "user.email"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=email + "\n", stderr="")
        if cmd[:4] == ["git", "config", "--global", "user.name"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=name + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)


# ---------------------------------------------------------------------------
# cmd_setup happy path
# ---------------------------------------------------------------------------

def test_cmd_setup_returns_0_when_everything_is_fine(import_orch, monkeypatch, capsys):
    orch = import_orch
    fake_ver = VersionInfo(3, 12, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)
    _mock_subprocess_all_ok(monkeypatch)
    monkeypatch.setattr(shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd in ("claude", "aa-orchestrator") else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "looks good" in out


# ---------------------------------------------------------------------------
# cmd_setup individual failure detections
# ---------------------------------------------------------------------------

def test_cmd_setup_flags_old_python(import_orch, monkeypatch, capsys):
    orch = import_orch
    fake_ver = VersionInfo(3, 8, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)
    _mock_subprocess_all_ok(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "Python 3.10" in out or "need Python" in out


def test_cmd_setup_flags_missing_git(import_orch, monkeypatch):
    orch = import_orch
    fake_ver = VersionInfo(3, 12, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "--version"]:
            raise FileNotFoundError("git")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: False)

    rc = orch.cmd_setup(argparse.Namespace())
    assert rc == 1


def test_cmd_setup_git_returns_nonzero(import_orch, monkeypatch):
    orch = import_orch
    fake_ver = VersionInfo(3, 12, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "--version"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="bad git")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: False)

    rc = orch.cmd_setup(argparse.Namespace())
    assert rc == 1


def test_cmd_setup_missing_git_user_declined_fix(import_orch, monkeypatch, capsys):
    orch = import_orch
    fake_ver = VersionInfo(3, 12, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)
    _mock_subprocess_all_ok(monkeypatch, email="", name="")
    monkeypatch.setattr(shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd != "opencode" else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: False)

    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "user.email" in out


def test_cmd_setup_missing_git_user_accepted_fix(import_orch, monkeypatch, capsys):
    orch = import_orch
    fake_ver = VersionInfo(3, 12, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)
    _mock_subprocess_all_ok(monkeypatch, email="", name="")
    monkeypatch.setattr(shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd in ("claude", "aa-orchestrator") else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: True)

    answers = iter(["new@example.com", "New Person"])
    monkeypatch.setattr(orch, "_prompt_text", lambda *a, **kw: next(answers))

    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "New Person" in out


def test_cmd_setup_flags_no_runner_cli(import_orch, monkeypatch, capsys):
    orch = import_orch
    fake_ver = VersionInfo(3, 12, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)
    _mock_subprocess_all_ok(monkeypatch)
    monkeypatch.setattr(shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd == "aa-orchestrator" else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "claude nor opencode" in out


def test_cmd_setup_warns_when_no_api_key(import_orch, monkeypatch, capsys):
    orch = import_orch
    fake_ver = VersionInfo(3, 12, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)
    _mock_subprocess_all_ok(monkeypatch)
    monkeypatch.setattr(shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd in ("claude", "aa-orchestrator") else None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "ANTHROPIC_API_KEY not set" in out


def test_cmd_setup_flags_missing_aa_orchestrator_on_path(import_orch, monkeypatch, capsys):
    orch = import_orch
    fake_ver = VersionInfo(3, 12, 0, "final", 0)
    monkeypatch.setattr(orch.sys, "version_info", fake_ver)
    _mock_subprocess_all_ok(monkeypatch)
    monkeypatch.setattr(shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd == "claude" else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "aa-orchestrator" in out


# ---------------------------------------------------------------------------
# cmd_wizard interactive branches
# ---------------------------------------------------------------------------

def test_cmd_wizard_noninteractive_mode_prints_and_exits(import_orch, project_root, monkeypatch, capsys):
    orch = import_orch
    monkeypatch.setenv("NONINTERACTIVE", "1")

    from collections import namedtuple
    FakeReport = namedtuple("FakeReport", ["state", "summary", "next_action", "command"])
    monkeypatch.setattr(orch, "_detect_state",
                        lambda root: FakeReport(orch._PipelineState.NOT_INITIALIZED,
                                                  "fresh dir", "run init",
                                                  "aa-orchestrator new myproj"))

    rc = orch.cmd_wizard(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "noninteractive mode" in out
    assert "aa-orchestrator new myproj" in out


def test_cmd_wizard_all_complete_exits_0(import_orch, project_root, monkeypatch, capsys):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)

    from collections import namedtuple
    FakeReport = namedtuple("FakeReport", ["state", "summary", "next_action", "command"])
    monkeypatch.setattr(orch, "_detect_state",
                        lambda root: FakeReport(orch._PipelineState.ALL_COMPLETE,
                                                  "done", "nothing", None))

    rc = orch.cmd_wizard(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "shipped" in out.lower() or "🎉" in out


def test_cmd_wizard_no_command_exits_0(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)

    from collections import namedtuple
    FakeReport = namedtuple("FakeReport", ["state", "summary", "next_action", "command"])
    monkeypatch.setattr(orch, "_detect_state",
                        lambda root: FakeReport(orch._PipelineState.OPEN_RFCS,
                                                  "rfcs", "review", None))

    rc = orch.cmd_wizard(argparse.Namespace())
    assert rc == 0


def test_cmd_wizard_state_detection_failure_returns_1(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)

    def boom(root):
        raise RuntimeError("state detection broken")
    monkeypatch.setattr(orch, "_detect_state", boom)

    rc = orch.cmd_wizard(argparse.Namespace())
    assert rc == 1
