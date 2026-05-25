"""Unit tests for the worktree isolation layer (M3.1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator import (
    _auto_merge_enabled,
    _cleanup_worktrees_enabled,
    _in_git_repo,
    _worktree_enabled,
    setup_worktree,
    teardown_worktree,
)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    (path / ".gitignore").write_text(".opencode/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _make_story(sid: str = "STORY-x") -> dict:
    return {
        "id": sid,
        "title": "x",
        "status": "pending",
        "depends_on": [],
        "acceptance_criteria": [],
        "tasks": [],
        "artifacts": {},
    }


def _make_data(stories: list[dict]) -> dict:
    return {
        "schema_version": "2.0",
        "version": 1,
        "epics": [{"id": "EPIC-x", "title": "x", "stories": stories}],
    }


def test_in_git_repo_returns_false_outside_repo(project_root: Path):
    assert _in_git_repo() is False


def test_in_git_repo_returns_true_inside_repo(project_root: Path):
    _git_init(project_root)
    assert _in_git_repo() is True


def test_worktree_enabled_defaults_to_true_when_no_config(project_root: Path):
    assert _worktree_enabled() is True


def test_worktree_enabled_respects_explicit_false(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"worktree_isolation": false}}', encoding="utf-8"
    )
    assert _worktree_enabled() is False


def test_auto_merge_defaults_to_true(project_root: Path):
    assert _auto_merge_enabled() is True


def test_cleanup_worktrees_defaults_to_true(project_root: Path):
    assert _cleanup_worktrees_enabled() is True


def test_setup_worktree_returns_none_when_not_in_git_repo(project_root: Path):
    story = _make_story()
    data = _make_data([story])
    assert setup_worktree(data, story) is None


def test_setup_worktree_returns_none_when_disabled(project_root: Path):
    _git_init(project_root)
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"worktree_isolation": false}}', encoding="utf-8"
    )
    story = _make_story()
    data = _make_data([story])
    assert setup_worktree(data, story) is None


def test_setup_worktree_creates_worktree_on_new_branch(project_root: Path):
    _git_init(project_root)
    story = _make_story("STORY-feat-a")
    data = _make_data([story])
    result = setup_worktree(data, story)
    assert result is not None
    wt = Path(result)
    assert wt.exists() and wt.is_dir()
    branch_proc = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(wt), capture_output=True, text=True,
    )
    assert "feat/EPIC-x/STORY-feat-a" in branch_proc.stdout


def test_setup_worktree_idempotent_on_resume(project_root: Path):
    _git_init(project_root)
    story = _make_story("STORY-resume")
    data = _make_data([story])
    first = setup_worktree(data, story)
    second = setup_worktree(data, story)
    assert first == second


def test_teardown_worktree_no_op_when_no_path(project_root: Path):
    teardown_worktree({"artifacts": {}})  # should not raise


def test_teardown_worktree_removes_worktree_after_merge(project_root: Path):
    _git_init(project_root)
    story = _make_story("STORY-merge")
    data = _make_data([story])
    wt_path = setup_worktree(data, story)
    assert wt_path

    (Path(wt_path) / "x.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=wt_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feat"], cwd=wt_path, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=wt_path, capture_output=True, text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=wt_path, capture_output=True, text=True,
    ).stdout.strip()

    story["artifacts"]["worktree_path"] = wt_path
    story["artifacts"]["branch"] = branch
    story["artifacts"]["commit_hash"] = commit
    teardown_worktree(story)

    assert not Path(wt_path).exists()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root, capture_output=True, text=True,
    ).stdout.strip()
    assert head == commit


def test_teardown_worktree_preserves_on_merge_conflict(project_root: Path):
    """If fast-forward merge fails, worktree is left for manual inspection."""
    _git_init(project_root)
    story = _make_story("STORY-conflict")
    data = _make_data([story])
    wt_path = setup_worktree(data, story)
    assert wt_path

    (Path(wt_path) / "wt.txt").write_text("wt\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=wt_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "wt"], cwd=wt_path, check=True)
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=wt_path, capture_output=True, text=True,
    ).stdout.strip()

    (project_root / "main.txt").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "main"], cwd=project_root, check=True)

    story["artifacts"]["worktree_path"] = wt_path
    story["artifacts"]["branch"] = branch
    story["artifacts"]["commit_hash"] = "deadbeef"
    teardown_worktree(story)

    assert Path(wt_path).exists()
