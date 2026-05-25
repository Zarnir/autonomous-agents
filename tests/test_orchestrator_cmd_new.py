"""M16.1: cmd_new branch coverage — bootstrap path with mocked subprocess + init.sh."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


@pytest.fixture
def fake_aa_home(tmp_path, monkeypatch):
    aa_home = tmp_path / "aa_home"
    aa_home.mkdir()
    (aa_home / "init.sh").write_text("#!/bin/bash\necho init.sh ran\nexit 0\n")
    monkeypatch.setenv("AA_HOME", str(aa_home))
    return aa_home


def _stub_subprocess_run_ok(monkeypatch):
    """All subprocess.run calls succeed."""
    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "-C"] and "config" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)


# ---------------------------------------------------------------------------
# cmd_new happy paths
# ---------------------------------------------------------------------------

def test_cmd_new_creates_project_without_idea(import_orch, project_root, fake_aa_home, monkeypatch):
    orch = import_orch
    _stub_subprocess_run_ok(monkeypatch)
    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: False)

    args = argparse.Namespace(name="myproj", runner="claude", idea=None, interactive=False)
    rc = orch.cmd_new(args)
    assert rc == 0
    assert (project_root / "myproj").exists()


def test_cmd_new_creates_project_with_idea_chains_discover(
    import_orch, project_root, fake_aa_home, monkeypatch
):
    orch = import_orch
    _stub_subprocess_run_ok(monkeypatch)

    discover_called = {"n": 0}

    def fake_discover(args):
        discover_called["n"] += 1
        discover_called["idea"] = args.idea
        return 0

    monkeypatch.setattr(orch, "cmd_discover", fake_discover)

    args = argparse.Namespace(
        name="myproj2", runner="claude", idea="a todo app with auth", interactive=False,
    )
    rc = orch.cmd_new(args)
    assert rc == 0
    assert discover_called["n"] == 1
    assert discover_called["idea"] == "a todo app with auth"


def test_cmd_new_propagates_discover_failure(
    import_orch, project_root, fake_aa_home, monkeypatch
):
    orch = import_orch
    _stub_subprocess_run_ok(monkeypatch)
    monkeypatch.setattr(orch, "cmd_discover", lambda args: 7)

    args = argparse.Namespace(name="myproj3", runner="claude", idea="x x x x x x", interactive=False)
    rc = orch.cmd_new(args)
    assert rc == 7


# ---------------------------------------------------------------------------
# cmd_new error paths
# ---------------------------------------------------------------------------

def test_cmd_new_dies_when_git_init_fails(import_orch, project_root, fake_aa_home, monkeypatch):
    orch = import_orch

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "init"]:
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: bad")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    args = argparse.Namespace(name="myproj4", runner="claude", idea=None, interactive=False)
    with pytest.raises(SystemExit):
        orch.cmd_new(args)


def test_cmd_new_dies_when_init_sh_missing(import_orch, project_root, monkeypatch, tmp_path):
    orch = import_orch
    empty_aa = tmp_path / "empty_aa"
    empty_aa.mkdir()
    monkeypatch.setenv("AA_HOME", str(empty_aa))
    _stub_subprocess_run_ok(monkeypatch)

    args = argparse.Namespace(name="myproj5", runner="claude", idea=None, interactive=False)
    with pytest.raises(SystemExit):
        orch.cmd_new(args)


def test_cmd_new_dies_when_init_sh_fails(import_orch, project_root, fake_aa_home, monkeypatch):
    orch = import_orch

    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "bash":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="init failed")
        if cmd[:2] == ["git", "init"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="user@example.com", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    args = argparse.Namespace(name="myproj6", runner="claude", idea=None, interactive=False)
    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: False)
    with pytest.raises(SystemExit):
        orch.cmd_new(args)


def test_cmd_new_aborts_when_target_nonempty_and_user_declines(
    import_orch, project_root, fake_aa_home, monkeypatch
):
    orch = import_orch
    target = project_root / "existing-dir"
    target.mkdir()
    (target / "preexisting.txt").write_text("don't touch me\n")

    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: False)

    args = argparse.Namespace(name="existing-dir", runner="claude", idea=None, interactive=False)
    rc = orch.cmd_new(args)
    assert rc == 1
    assert (target / "preexisting.txt").read_text() == "don't touch me\n"


def test_cmd_new_continues_when_target_nonempty_and_user_accepts(
    import_orch, project_root, fake_aa_home, monkeypatch
):
    orch = import_orch
    target = project_root / "existing-dir-2"
    target.mkdir()
    (target / "preexisting.txt").write_text("hi\n")

    _stub_subprocess_run_ok(monkeypatch)
    monkeypatch.setattr(orch, "_prompt_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr(orch, "cmd_discover", lambda a: 0)

    args = argparse.Namespace(
        name="existing-dir-2", runner="claude", idea="some idea here", interactive=False,
    )
    rc = orch.cmd_new(args)
    assert rc == 0
