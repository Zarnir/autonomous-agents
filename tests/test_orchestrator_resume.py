"""M15.8 final: cmd_resume + cmd_revisit branch coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _story(sid, status="pending", artifacts=None, depends_on=None):
    return {
        "id": sid,
        "title": f"Story {sid}",
        "description": "x",
        "status": status,
        "depends_on": depends_on or [],
        "execution_wave": 1,
        "estimated_complexity": "small",
        "acceptance_criteria": ["AC1: ok"],
        "tasks": [],
        "artifacts": artifacts or {},
    }


def _seed(project_root, stories=None, current_story_id=None, **extra):
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": stories or []}],
        "sprints": [], "current_sprint": 0,
        "current_story_id": current_story_id,
        "completed_stories": [], "failed_stories": [], "blocked_stories": [],
    }
    data.update(extra)
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# cmd_resume
# ---------------------------------------------------------------------------

def test_cmd_resume_retry_failed_resets_failed_stories(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root,
          stories=[_story("S1", status="failed"), _story("S2", status="completed")],
          failed_stories=["S1"])

    monkeypatch.setattr(orch, "run_loop", lambda data, only_story=None, from_story=None: 0)

    rc = orch.cmd_resume(argparse.Namespace(retry_failed=True, retry_blocked=False, story=None))
    assert rc == 0
    data = orch.read_progress()
    failed = [s for s in data["epics"][0]["stories"] if s["id"] == "S1"][0]
    assert failed["status"] == "pending"
    assert data["failed_stories"] == []


def test_cmd_resume_retry_blocked_resets_blocked_stories(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root,
          stories=[_story("S1", status="blocked")],
          blocked_stories=["S1"])

    monkeypatch.setattr(orch, "run_loop", lambda data, only_story=None, from_story=None: 0)

    rc = orch.cmd_resume(argparse.Namespace(retry_failed=False, retry_blocked=True, story=None))
    assert rc == 0
    data = orch.read_progress()
    assert data["epics"][0]["stories"][0]["status"] == "pending"
    assert data["blocked_stories"] == []


def test_cmd_resume_clears_intermediate_states(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root, stories=[
        _story("S1", status="in_progress"),
        _story("S2", status="review_pass"),
        _story("S3", status="test_written"),
    ])

    monkeypatch.setattr(orch, "run_loop", lambda data, only_story=None, from_story=None: 0)

    orch.cmd_resume(argparse.Namespace(retry_failed=False, retry_blocked=False, story=None))
    data = orch.read_progress()
    for s in data["epics"][0]["stories"]:
        assert s["status"] == "pending"


def test_cmd_resume_handles_current_story_after_reset(import_orch, project_root, monkeypatch):
    """After resume resets intermediate stories, current_story_id check completes
    without crash regardless of outcome."""
    orch = import_orch
    _seed(project_root,
          stories=[_story("S1", status="in_progress")],
          current_story_id="S1")
    monkeypatch.setattr(orch, "run_loop", lambda data, only_story=None, from_story=None: 0)

    orch.cmd_resume(argparse.Namespace(retry_failed=False, retry_blocked=False, story=None))
    data = orch.read_progress()
    # Story was stuck → reset to pending
    assert data["epics"][0]["stories"][0]["status"] == "pending"


def test_cmd_resume_reruns_tests_for_implemented_story(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root,
          stories=[_story("S1", status="implemented",
                          artifacts={"test_files": ["tests/test_x.py"]})],
          current_story_id="S1")

    monkeypatch.setattr(orch, "run_tests_independently", lambda files, cwd=None: (False, "fail"))
    monkeypatch.setattr(orch, "run_loop", lambda data, only_story=None, from_story=None: 0)

    orch.cmd_resume(argparse.Namespace(retry_failed=False, retry_blocked=False, story=None))
    data = orch.read_progress()
    assert data["epics"][0]["stories"][0]["status"] == "review_pass"


# ---------------------------------------------------------------------------
# cmd_revisit
# ---------------------------------------------------------------------------

def test_cmd_revisit_dies_when_story_not_found(import_orch, project_root):
    _seed(project_root, stories=[])
    with pytest.raises(SystemExit):
        import_orch.cmd_revisit(argparse.Namespace(
            story="STORY-ghost", reason=None, cascade_dependents=False,
        ))


def test_cmd_revisit_dies_when_story_in_progress(import_orch, project_root):
    _seed(project_root, stories=[_story("S1", status="in_progress")])
    with pytest.raises(SystemExit):
        import_orch.cmd_revisit(argparse.Namespace(
            story="S1", reason=None, cascade_dependents=False,
        ))


def test_cmd_revisit_resets_completed_story_to_pending(import_orch, project_root):
    orch = import_orch
    _seed(project_root,
          stories=[_story("S1", status="completed",
                          artifacts={"commit_hash": "abc", "test_files": ["x"]})],
          completed_stories=["S1"])

    rc = orch.cmd_revisit(argparse.Namespace(
        story="S1", reason="needs polish", cascade_dependents=False,
    ))
    assert rc == 0
    data = orch.read_progress()
    story = data["epics"][0]["stories"][0]
    assert story["status"] == "pending"
    assert story["artifacts"]["commit_hash"] is None
    assert len(story["artifacts"]["previous"]) == 1
    assert story["artifacts"]["previous"][0]["revisit_reason"] == "needs polish"
    assert "S1" not in data["completed_stories"]


def test_cmd_revisit_cascade_reopens_dependents(import_orch, project_root):
    orch = import_orch
    _seed(project_root, stories=[
        _story("S1", status="completed", artifacts={"commit_hash": "a"}),
        _story("S2", status="completed", artifacts={"commit_hash": "b"}, depends_on=["S1"]),
        _story("S3", status="completed", artifacts={"commit_hash": "c"}),
    ], completed_stories=["S1", "S2", "S3"])

    rc = orch.cmd_revisit(argparse.Namespace(
        story="S1", reason="bug", cascade_dependents=True,
    ))
    assert rc == 0
    data = orch.read_progress()
    statuses = {s["id"]: s["status"] for s in data["epics"][0]["stories"]}
    assert statuses["S1"] == "pending"
    assert statuses["S2"] == "pending"
    assert statuses["S3"] == "completed"
