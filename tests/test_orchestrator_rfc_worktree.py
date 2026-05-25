"""M16 followup: process_rfc_files action branches + worktree config + watcher config."""

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


def _seed_progress(project_root, stories=None, **extra):
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": stories or []}],
        "sprints": [], "completed_stories": [],
        "failed_stories": [], "blocked_stories": [],
    }
    data.update(extra)
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# process_rfc_files action branches
# ---------------------------------------------------------------------------

def test_process_rfc_files_reopen_resets_target_story(import_orch, project_root, monkeypatch):
    orch = import_orch
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-test.md").write_text(
        "# RFC-0001\n\nStatus: open\n\n## Detail\nstale story\n",
        encoding="utf-8",
    )

    story = {
        "id": "STORY-a", "title": "t", "status": "completed",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {"commit_hash": "abc"},
    }
    _seed_progress(project_root, stories=[story], completed_stories=["STORY-a"])

    monkeypatch.setattr(
        orch, "call_agent",
        lambda *a, **kw: "Recommendation: REOPEN STORY-a\nVERDICT: RFC_RESOLVED\n",
    )

    data = orch.read_progress()
    rc = orch.process_rfc_files(data)
    assert rc == 0
    after = orch.read_progress()
    reopened = after["epics"][0]["stories"][0]
    assert reopened["status"] == "pending"
    assert "STORY-a" not in after["completed_stories"]


def test_process_rfc_files_escalate_returns_needs_human(import_orch, project_root, monkeypatch):
    orch = import_orch
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0002-escalate.md").write_text(
        "# RFC-0002\n\nStatus: open\n", encoding="utf-8",
    )
    _seed_progress(project_root)

    monkeypatch.setattr(
        orch, "call_agent",
        lambda *a, **kw: "Recommendation: ESCALATE — needs human\nVERDICT: RFC_RESOLVED\n",
    )

    data = orch.read_progress()
    rc = orch.process_rfc_files(data)
    assert rc == orch.EXIT_RFC_NEEDS_HUMAN


def test_process_rfc_files_marks_resolved_after_apply(import_orch, project_root, monkeypatch):
    orch = import_orch
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    rfc_path = rfc_dir / "0003-marked.md"
    rfc_path.write_text(
        "# RFC-0003\n\nStatus: open\n", encoding="utf-8",
    )
    _seed_progress(project_root)

    monkeypatch.setattr(
        orch, "call_agent",
        lambda *a, **kw: "Recommendation: NONE\nVERDICT: RFC_RESOLVED\n",
    )

    data = orch.read_progress()
    orch.process_rfc_files(data)
    assert "Status: resolved" in rfc_path.read_text()


# ---------------------------------------------------------------------------
# Worktree config helpers
# ---------------------------------------------------------------------------

def test_worktree_enabled_defaults_true(import_orch, project_root):
    assert import_orch._worktree_enabled() is True


def test_worktree_enabled_reads_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"worktree_isolation": False}}), encoding="utf-8"
    )
    assert import_orch._worktree_enabled() is False


def test_worktree_enabled_malformed_config_returns_true(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("not json", encoding="utf-8")
    assert import_orch._worktree_enabled() is True


def test_auto_merge_defaults_true(import_orch, project_root):
    assert import_orch._auto_merge_enabled() is True


def test_auto_merge_reads_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"auto_merge": False}}), encoding="utf-8"
    )
    assert import_orch._auto_merge_enabled() is False


def test_cleanup_worktrees_defaults_true(import_orch, project_root):
    assert import_orch._cleanup_worktrees_enabled() is True


def test_cleanup_worktrees_reads_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"cleanup_worktrees": False}}), encoding="utf-8"
    )
    assert import_orch._cleanup_worktrees_enabled() is False


# ---------------------------------------------------------------------------
# setup_worktree / teardown_worktree
# ---------------------------------------------------------------------------

def test_setup_worktree_returns_none_when_disabled(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"worktree_isolation": False}}), encoding="utf-8"
    )
    assert import_orch.setup_worktree({}, {"id": "S1", "title": "t"}) is None


def test_setup_worktree_returns_none_when_not_in_git_repo(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "_in_git_repo", lambda: False)
    result = orch.setup_worktree({}, {"id": "S1", "title": "t"})
    assert result is None


def test_setup_worktree_handles_branch_create_failure(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "_in_git_repo", lambda: True)

    def fake_run(cmd, **kw):
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no such branch")
        if cmd[:3] == ["git", "worktree", "add"]:
            raise subprocess.CalledProcessError(1, cmd, stderr="worktree add failed")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(orch.subprocess, "run", fake_run)

    _seed_progress(project_root, stories=[{
        "id": "S1", "title": "test story", "status": "in_progress",
        "depends_on": [], "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }])
    data = orch.read_progress()
    result = orch.setup_worktree(data, data["epics"][0]["stories"][0])
    assert result is None


def test_teardown_worktree_no_op_when_no_worktree_path(import_orch, project_root):
    orch = import_orch
    story = {"id": "S1", "artifacts": {}}
    orch.teardown_worktree(story)


def test_teardown_worktree_no_op_when_path_missing(import_orch, project_root):
    orch = import_orch
    story = {"id": "S1", "artifacts": {"worktree_path": str(project_root / "ghost")}}
    orch.teardown_worktree(story)


# ---------------------------------------------------------------------------
# _watcher_config
# ---------------------------------------------------------------------------

def test_watcher_config_defaults_when_no_file(import_orch, project_root):
    cfg = import_orch._watcher_config()
    assert cfg["enabled"] is True
    assert cfg["stall_threshold_sec"] == 1800
    assert cfg["max_blocked"] == 3


def test_watcher_config_reads_overrides(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {
            "watcher_enabled": False,
            "watcher_stall_threshold_sec": 600,
            "watcher_max_blocked": 5,
        }}),
        encoding="utf-8",
    )
    cfg = import_orch._watcher_config()
    assert cfg["enabled"] is False
    assert cfg["stall_threshold_sec"] == 600
    assert cfg["max_blocked"] == 5


def test_watcher_config_malformed_returns_defaults(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("garbage", encoding="utf-8")
    cfg = import_orch._watcher_config()
    assert cfg["enabled"] is True
