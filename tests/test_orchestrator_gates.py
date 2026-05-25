"""M16 final: production gates branch coverage."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


# ---------------------------------------------------------------------------
# _gate_clean_working_tree
# ---------------------------------------------------------------------------

def test_gate_clean_working_tree_clean(import_orch, monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""))
    ok, msg = import_orch._gate_clean_working_tree()
    assert ok is True
    assert msg == "clean"


def test_gate_clean_working_tree_dirty(import_orch, monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, " M src/foo.py\n", ""))
    ok, msg = import_orch._gate_clean_working_tree()
    assert ok is False
    assert "not clean" in msg


def test_gate_clean_working_tree_git_failed(import_orch, monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 128, "", "bad repo"))
    ok, msg = import_orch._gate_clean_working_tree()
    assert ok is False
    assert "returned 128" in msg


def test_gate_clean_working_tree_filenotfound(import_orch, monkeypatch):
    def fake_run(*a, **kw):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = import_orch._gate_clean_working_tree()
    assert ok is False
    assert "git status failed" in msg


def test_gate_clean_working_tree_timeout(import_orch, monkeypatch):
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 30)
    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = import_orch._gate_clean_working_tree()
    assert ok is False
    assert "git status failed" in msg


# ---------------------------------------------------------------------------
# _gate_all_tests_pass
# ---------------------------------------------------------------------------

def test_gate_all_tests_pass_skipped_when_no_runner(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "detect_test_command", lambda: None)
    ok, msg = import_orch._gate_all_tests_pass()
    assert ok is True
    assert "no test runner" in msg


def test_gate_all_tests_pass_passes(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "detect_test_command", lambda: ["pytest"])
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "ok", ""))
    ok, _ = import_orch._gate_all_tests_pass()
    assert ok is True


def test_gate_all_tests_pass_fails(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "detect_test_command", lambda: ["pytest"])
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "1 failed", ""))
    ok, msg = import_orch._gate_all_tests_pass()
    assert ok is False
    assert "tests failed" in msg


def test_gate_all_tests_pass_runner_missing(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "detect_test_command", lambda: ["pytest"])
    def boom(*a, **kw):
        raise FileNotFoundError("pytest")
    monkeypatch.setattr(subprocess, "run", boom)
    ok, msg = import_orch._gate_all_tests_pass()
    assert ok is False
    assert "test runner error" in msg


# ---------------------------------------------------------------------------
# _detect_build_command
# ---------------------------------------------------------------------------

def test_detect_build_command_package_json_with_build(import_orch, project_root):
    (project_root / "package.json").write_text(
        json.dumps({"scripts": {"build": "tsc"}}), encoding="utf-8",
    )
    assert import_orch._detect_build_command() == ["npm", "run", "build"]


def test_detect_build_command_package_json_without_build(import_orch, project_root):
    (project_root / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8",
    )
    assert import_orch._detect_build_command() is None


def test_detect_build_command_cargo(import_orch, project_root):
    (project_root / "Cargo.toml").write_text("[package]\nname = 'x'\n", encoding="utf-8")
    assert import_orch._detect_build_command() == ["cargo", "build", "--release"]


def test_detect_build_command_go(import_orch, project_root):
    (project_root / "go.mod").write_text("module test\n", encoding="utf-8")
    assert import_orch._detect_build_command() == ["go", "build", "./..."]


def test_detect_build_command_pyproject(import_orch, project_root):
    (project_root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    cmd = import_orch._detect_build_command()
    assert cmd is not None and "compileall" in cmd


def test_detect_build_command_none(import_orch, project_root):
    assert import_orch._detect_build_command() is None


# ---------------------------------------------------------------------------
# _gate_build_succeeds
# ---------------------------------------------------------------------------

def test_gate_build_skipped_when_no_command(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "_detect_build_command", lambda: None)
    ok, msg = import_orch._gate_build_succeeds()
    assert ok is True
    assert "no build" in msg


def test_gate_build_passes(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "_detect_build_command", lambda: ["npm", "run", "build"])
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "built", ""))
    ok, _ = import_orch._gate_build_succeeds()
    assert ok is True


def test_gate_build_fails(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "_detect_build_command", lambda: ["npm", "run", "build"])
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "", "syntax error"))
    ok, msg = import_orch._gate_build_succeeds()
    assert ok is False
    assert "build failed" in msg


def test_gate_build_runner_error(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "_detect_build_command", lambda: ["npm", "run", "build"])
    def boom(*a, **kw):
        raise FileNotFoundError("npm")
    monkeypatch.setattr(subprocess, "run", boom)
    ok, msg = import_orch._gate_build_succeeds()
    assert ok is False
    assert "build error" in msg


# ---------------------------------------------------------------------------
# run_production_gates dispatch
# ---------------------------------------------------------------------------

def test_run_production_gates_disabled(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"production_gates": {"enabled": False}}), encoding="utf-8",
    )
    ok, failures = import_orch.run_production_gates({})
    assert ok is True
    assert failures == []


def test_run_production_gates_empty_gates_list_passes(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"production_gates": {"gates": []}}), encoding="utf-8",
    )
    ok, _ = import_orch.run_production_gates({})
    assert ok is True


def test_run_production_gates_unknown_gate_skipped(import_orch, project_root, capsys):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"production_gates": {"gates": ["nonexistent_gate"]}}), encoding="utf-8",
    )
    ok, _ = import_orch.run_production_gates({})
    assert ok is True
    out = capsys.readouterr().out
    assert "unknown gate" in out


def test_run_production_gates_crashing_gate_reports_failure(import_orch, project_root, monkeypatch):
    orch = import_orch
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"production_gates": {"gates": ["clean_working_tree"]}}), encoding="utf-8",
    )
    def crashing_gate(cwd=None):
        raise RuntimeError("gate logic broken")
    monkeypatch.setitem(orch._GATE_REGISTRY, "clean_working_tree", crashing_gate)

    ok, failures = orch.run_production_gates({})
    assert ok is False
    assert any("gate crashed" in f for f in failures)


def test_run_production_gates_failure_propagates(import_orch, project_root, monkeypatch):
    orch = import_orch
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"production_gates": {"gates": ["clean_working_tree"]}}), encoding="utf-8",
    )
    monkeypatch.setitem(orch._GATE_REGISTRY, "clean_working_tree",
                        lambda cwd=None: (False, "tree dirty"))
    ok, failures = orch.run_production_gates({})
    assert ok is False
    assert any("tree dirty" in f for f in failures)
