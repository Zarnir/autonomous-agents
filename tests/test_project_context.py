"""Unit tests for cross-story memory (M2.3)."""

from __future__ import annotations

from pathlib import Path

from orchestrator import (
    PROJECT_CONTEXT_FILE,
    load_project_context,
    update_project_context,
)


def _story(sid: str, title: str = "title", commit: str = "abc123") -> dict:
    return {
        "id": sid,
        "title": title,
        "acceptance_criteria": ["AC1: do x"],
        "artifacts": {
            "implementation_files": ["src/a.py"],
            "test_files": ["tests/test_a.py"],
            "commit_hash": commit,
        },
    }


def test_update_project_context_creates_file_with_header(project_root: Path):
    (project_root / "docs" / "specs").mkdir(parents=True)
    update_project_context({}, _story("STORY-1"))
    assert PROJECT_CONTEXT_FILE.exists()
    content = PROJECT_CONTEXT_FILE.read_text(encoding="utf-8")
    assert "# Project Context" in content
    assert "STORY-1" in content


def test_update_project_context_appends_multiple_stories(project_root: Path):
    (project_root / "docs" / "specs").mkdir(parents=True)
    update_project_context({}, _story("STORY-A", title="A"))
    update_project_context({}, _story("STORY-B", title="B"))
    content = PROJECT_CONTEXT_FILE.read_text(encoding="utf-8")
    assert "STORY-A" in content
    assert "STORY-B" in content
    assert content.index("STORY-A") < content.index("STORY-B")


def test_update_records_files_and_commit(project_root: Path):
    (project_root / "docs" / "specs").mkdir(parents=True)
    update_project_context({}, _story("STORY-z", commit="deadbeef"))
    content = PROJECT_CONTEXT_FILE.read_text(encoding="utf-8")
    assert "src/a.py" in content
    assert "tests/test_a.py" in content
    assert "deadbeef" in content


def test_load_project_context_empty_when_no_file(project_root: Path):
    assert load_project_context() == ""


def test_load_project_context_returns_last_n_entries(project_root: Path):
    (project_root / "docs" / "specs").mkdir(parents=True)
    for i in range(5):
        update_project_context({}, _story(f"STORY-{i}", title=f"s{i}"))
    text = load_project_context(max_entries=2)
    assert "STORY-3" in text
    assert "STORY-4" in text
    assert "STORY-0" not in text
    assert "STORY-1" not in text


def test_load_project_context_disabled_returns_empty(project_root: Path):
    (project_root / "docs" / "specs").mkdir(parents=True)
    update_project_context({}, _story("STORY-x"))
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"project_context_enabled": false}}', encoding="utf-8"
    )
    assert load_project_context() == ""


def test_update_handles_missing_artifacts_gracefully(project_root: Path):
    (project_root / "docs" / "specs").mkdir(parents=True)
    minimal = {"id": "STORY-min", "title": "min", "acceptance_criteria": []}
    update_project_context({}, minimal)
    content = PROJECT_CONTEXT_FILE.read_text(encoding="utf-8")
    assert "STORY-min" in content
    assert "(none)" in content
