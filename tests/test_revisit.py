"""Unit tests for cmd_revisit (M2.1)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from orchestrator import cmd_revisit


def _seed_progress(progress_file: Path, stories: list[dict]) -> dict:
    data = {
        "schema_version": "2.0",
        "version": 1,
        "updated_at": "2026-05-12T00:00:00Z",
        "status": "in_progress",
        "current_story_id": None,
        "completed_stories": [s["id"] for s in stories if s.get("status") == "completed"],
        "failed_stories": [s["id"] for s in stories if s.get("status") == "failed"],
        "blocked_stories": [s["id"] for s in stories if s.get("status") == "blocked"],
        "epics": [{"id": "EPIC-x", "title": "x", "stories": stories}],
        "execution_log": [],
    }
    progress_file.write_text(json.dumps(data), encoding="utf-8")
    return data


def _completed_story(sid: str, hash: str = "abc123") -> dict:
    return {
        "id": sid,
        "title": sid,
        "status": "completed",
        "depends_on": [],
        "execution_wave": 1,
        "acceptance_criteria": [],
        "tasks": [],
        "artifacts": {
            "test_files": ["t.py"],
            "implementation_files": ["src.py"],
            "commit_hash": hash,
            "branch": "feat/x",
        },
    }


def _args(story: str, reason: str = "wrong build", cascade: bool = False):
    return argparse.Namespace(story=story, reason=reason, cascade_dependents=cascade)


def test_revisit_completed_story_resets_to_pending(progress_file: Path):
    _seed_progress(progress_file, [_completed_story("STORY-a")])
    rc = cmd_revisit(_args("STORY-a"))
    assert rc == 0
    data = json.loads(progress_file.read_text(encoding="utf-8"))
    story = data["epics"][0]["stories"][0]
    assert story["status"] == "pending"
    assert "STORY-a" not in data["completed_stories"]
    prev = story["artifacts"]["previous"]
    assert len(prev) == 1
    assert prev[0]["status"] == "completed"
    assert prev[0]["commit_hash"] == "abc123"
    assert prev[0]["revisit_reason"] == "wrong build"
    assert story["artifacts"]["commit_hash"] is None
    assert story["artifacts"]["test_files"] == []


def test_revisit_failed_story_resets_to_pending(progress_file: Path):
    failed = _completed_story("STORY-bad", hash="dead")
    failed["status"] = "failed"
    _seed_progress(progress_file, [failed])
    rc = cmd_revisit(_args("STORY-bad"))
    assert rc == 0
    data = json.loads(progress_file.read_text(encoding="utf-8"))
    assert data["epics"][0]["stories"][0]["status"] == "pending"
    assert "STORY-bad" not in data["failed_stories"]


def test_revisit_in_progress_story_dies(progress_file: Path):
    inprog = _completed_story("STORY-busy")
    inprog["status"] = "in_progress"
    _seed_progress(progress_file, [inprog])
    with pytest.raises(SystemExit):
        cmd_revisit(_args("STORY-busy"))


def test_revisit_unknown_story_dies(progress_file: Path):
    _seed_progress(progress_file, [_completed_story("STORY-a")])
    with pytest.raises(SystemExit):
        cmd_revisit(_args("STORY-nonexistent"))


def test_revisit_cascade_reopens_direct_dependents(progress_file: Path):
    root = _completed_story("STORY-root")
    child = _completed_story("STORY-child")
    child["depends_on"] = ["STORY-root"]
    _seed_progress(progress_file, [root, child])

    rc = cmd_revisit(_args("STORY-root", cascade=True))
    assert rc == 0
    data = json.loads(progress_file.read_text(encoding="utf-8"))
    stories = {s["id"]: s for s in data["epics"][0]["stories"]}
    assert stories["STORY-root"]["status"] == "pending"
    assert stories["STORY-child"]["status"] == "pending"


def test_revisit_without_cascade_leaves_dependents_alone(progress_file: Path):
    root = _completed_story("STORY-r2")
    dependent = _completed_story("STORY-dep")
    dependent["depends_on"] = ["STORY-r2"]
    _seed_progress(progress_file, [root, dependent])

    cmd_revisit(_args("STORY-r2"))
    data = json.loads(progress_file.read_text(encoding="utf-8"))
    stories = {s["id"]: s for s in data["epics"][0]["stories"]}
    assert stories["STORY-r2"]["status"] == "pending"
    assert stories["STORY-dep"]["status"] == "completed"
