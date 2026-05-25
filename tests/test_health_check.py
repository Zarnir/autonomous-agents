"""Unit tests for `aa-orchestrator health-check` (M11.3)."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def _make_args() -> argparse.Namespace:
    return argparse.Namespace()


def _stub_runner_path(monkeypatch, name: str = "claude") -> None:
    """Make select_runner succeed by pretending the named CLI is on PATH."""
    import runners

    def fake_select(preference=None):
        if name == "claude":
            return runners.ClaudeCodeRunner()
        return runners.OpenCodeRunner()

    monkeypatch.setattr(runners, "select_runner", fake_select)


def _init_git(path: Path, user_email: str = "test@example.com", user_name: str = "Test") -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    if user_email:
        subprocess.run(["git", "config", "user.email", user_email], cwd=path, check=True)
    if user_name:
        subprocess.run(["git", "config", "user.name", user_name], cwd=path, check=True)


def _import_cmd():
    import orchestrator
    return orchestrator.cmd_health_check


def test_health_check_missing_runner(project_root: Path, monkeypatch, capsys):
    """Without a runner CLI on PATH, the report flags it."""
    import runners

    def fake_select(preference=None):
        raise RuntimeError("No agent runner available")

    monkeypatch.setattr(runners, "select_runner", fake_select)
    rc = _import_cmd()(_make_args())
    out = capsys.readouterr().out
    assert "✗ runner CLI" in out
    assert "No agent runner available" in out
    assert rc == 1


def test_health_check_clean_repo_with_minimal_config(project_root: Path, monkeypatch, capsys):
    """A clean project with a runner + git config — basics should pass."""
    _stub_runner_path(monkeypatch)
    _init_git(project_root)

    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text("{}", encoding="utf-8")

    _import_cmd()(_make_args())
    out = capsys.readouterr().out

    assert "✓ config.json" in out
    assert "✓ git config" in out
    # No agents/skills dirs → those checks flag (expected)
    assert "agents parse" in out


def test_health_check_corrupt_config(project_root: Path, monkeypatch, capsys):
    _stub_runner_path(monkeypatch)
    _init_git(project_root)
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text("{ not valid json }", encoding="utf-8")

    rc = _import_cmd()(_make_args())
    out = capsys.readouterr().out
    assert "✗ config.json" in out
    assert rc == 1


def test_health_check_missing_git_config(project_root: Path, monkeypatch, capsys):
    _stub_runner_path(monkeypatch)
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    # Force empty user.email/name in repo-local config
    subprocess.run(["git", "config", "user.email", ""], cwd=project_root, check=True)
    subprocess.run(["git", "config", "user.name", ""], cwd=project_root, check=True)

    rc = _import_cmd()(_make_args())
    out = capsys.readouterr().out
    assert "✗ git config" in out
    assert "@commit will fail" in out
    assert rc == 1


def test_health_check_loads_real_repo_personas(project_root: Path, monkeypatch, capsys):
    """When the test runs against the real repo agents/skills, personas resolve."""
    _stub_runner_path(monkeypatch)
    _init_git(project_root)

    repo_root = Path(__file__).resolve().parent.parent
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "agents").symlink_to(repo_root / ".opencode" / "agents")
    (project_root / ".opencode" / "skills").symlink_to(repo_root / ".opencode" / "skills")

    rc = _import_cmd()(_make_args())
    out = capsys.readouterr().out
    assert "✓ agents parse" in out
    assert "✓ skills load" in out
    assert "✓ persona imports" in out
    assert "OVERALL: ✓ healthy" in out
    assert rc == 0


def test_health_check_exit_zero_when_empty_but_well_formed(project_root: Path, monkeypatch, capsys):
    """Empty but well-formed project → all green → exit 0."""
    _stub_runner_path(monkeypatch)
    _init_git(project_root)
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text("{}", encoding="utf-8")
    (project_root / ".opencode" / "agents").mkdir()
    (project_root / ".opencode" / "skills").mkdir()

    rc = _import_cmd()(_make_args())
    out = capsys.readouterr().out
    assert "✓ healthy" in out
    assert rc == 0


def test_health_check_exit_one_on_runner_missing(project_root: Path, monkeypatch, capsys):
    """One failed check → exit 1 even if others pass."""
    import runners

    def fake_select(preference=None):
        raise RuntimeError("CLI gone")

    monkeypatch.setattr(runners, "select_runner", fake_select)
    _init_git(project_root)
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text("{}", encoding="utf-8")

    rc = _import_cmd()(_make_args())
    out = capsys.readouterr().out
    assert "✗ issues detected" in out
    assert rc == 1
