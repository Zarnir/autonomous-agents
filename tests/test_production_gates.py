"""Unit tests for the production-ready gates (M1.2).

Each test creates a real but tiny git repo in tmp_path so the git-based gates
actually run. No subprocess mocking — that's the point of these tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import orchestrator
from orchestrator import (
    _gate_all_tests_pass,
    _gate_build_succeeds,
    _gate_clean_working_tree,
    run_production_gates,
)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    # Pre-ignore .opencode/ so test config files don't dirty the tree
    (path / ".gitignore").write_text(".opencode/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


# ---------------------------------------------------------------------------
# clean_working_tree
# ---------------------------------------------------------------------------

def test_clean_working_tree_passes_on_clean_repo(project_root: Path):
    _git_init(project_root)
    ok, msg = _gate_clean_working_tree()
    assert ok, f"expected clean, got: {msg}"


def test_clean_working_tree_fails_with_uncommitted_change(project_root: Path):
    _git_init(project_root)
    (project_root / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    ok, msg = _gate_clean_working_tree()
    assert not ok
    assert "dirty.txt" in msg


def test_clean_working_tree_fails_outside_git_repo(project_root: Path):
    ok, _ = _gate_clean_working_tree()
    assert not ok


# ---------------------------------------------------------------------------
# all_tests_pass
# ---------------------------------------------------------------------------

def test_all_tests_pass_skips_when_no_runner_detected(project_root: Path):
    ok, msg = _gate_all_tests_pass()
    assert ok
    assert "skipped" in msg.lower()


# ---------------------------------------------------------------------------
# build_succeeds
# ---------------------------------------------------------------------------

def test_build_succeeds_skips_when_no_build_command_detected(project_root: Path):
    ok, msg = _gate_build_succeeds()
    assert ok
    assert "skipped" in msg.lower()


def test_build_succeeds_runs_python_compileall_for_pyproject(project_root: Path):
    (project_root / "pyproject.toml").write_text(
        "[project]\nname = 'x'\nversion = '0'\n", encoding="utf-8"
    )
    ok, msg = _gate_build_succeeds()
    assert ok, msg


# ---------------------------------------------------------------------------
# run_production_gates orchestration
# ---------------------------------------------------------------------------

def test_run_production_gates_respects_enabled_false(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"production_gates": {"enabled": false}}', encoding="utf-8"
    )
    ok, failures = run_production_gates({})
    assert ok
    assert failures == []


def test_run_production_gates_runs_only_listed_gates(project_root: Path):
    _git_init(project_root)
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        '{"production_gates": {"enabled": true, "gates": ["clean_working_tree"]}}',
        encoding="utf-8",
    )
    ok, failures = run_production_gates({})
    assert ok, f"failures: {failures}"


def test_run_production_gates_reports_failures(project_root: Path):
    _git_init(project_root)
    (project_root / "dirty.py").write_text("# uncommitted\n", encoding="utf-8")
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        '{"production_gates": {"enabled": true, "gates": ["clean_working_tree"]}}',
        encoding="utf-8",
    )
    ok, failures = run_production_gates({})
    assert not ok
    assert len(failures) == 1
    assert "clean_working_tree" in failures[0]
    assert "dirty.py" in failures[0]


def test_run_production_gates_skips_unknown_gate(project_root: Path, capsys):
    _git_init(project_root)
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        '{"production_gates": {"enabled": true, "gates": ["clean_working_tree", "nonexistent_gate"]}}',
        encoding="utf-8",
    )
    ok, failures = run_production_gates({})
    assert ok, f"failures: {failures}"
    captured = capsys.readouterr()
    assert "unknown gate" in captured.out


def test_exit_code_4_constant_defined():
    assert orchestrator.EXIT_GATE_FAILED == 4
