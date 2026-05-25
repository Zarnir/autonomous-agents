"""M16 final: timeout branches in run_loop and _sprint_start + only_story run path."""

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


def _seed_progress(project_root, stories=None, sprints=None):
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": stories or []}],
        "sprints": sprints or [], "current_sprint": 0,
    }
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# run_loop timeout
# ---------------------------------------------------------------------------

def test_run_loop_returns_more_work_when_deadline_near(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed_progress(project_root, stories=[{
        "id": "S1", "title": "t", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }])

    # First call sets the deadline at t=0; second call (in the loop) is far past
    calls = [0]
    def fake_mono():
        calls[0] += 1
        return 0.0 if calls[0] == 1 else 1e9
    monkeypatch.setattr(orch.time, "monotonic", fake_mono)

    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == orch.EXIT_MORE_WORK


def test_run_loop_only_story_runs_pending_story(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed_progress(project_root, stories=[{
        "id": "S1", "title": "t", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }])

    ran = {"n": 0}
    def fake_run_story(data, story):
        ran["n"] += 1
        return data
    monkeypatch.setattr(orch, "run_story", fake_run_story)

    data = orch.read_progress()
    rc = orch.run_loop(data, only_story="S1")
    assert rc == 0
    assert ran["n"] == 1


# ---------------------------------------------------------------------------
# _sprint_start timeout
# ---------------------------------------------------------------------------

def test_sprint_start_returns_more_work_on_timeout(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed_progress(
        project_root,
        stories=[{
            "id": "S1", "title": "t", "status": "pending", "depends_on": [],
            "execution_wave": 1, "estimated_complexity": "small",
            "acceptance_criteria": [], "tasks": [], "artifacts": {},
        }],
        sprints=[{
            "number": 1, "status": "planned", "story_ids": ["S1"],
            "velocity_points": 0,
        }],
    )

    calls = [0]
    def fake_mono():
        calls[0] += 1
        return 0.0 if calls[0] == 1 else 1e9
    monkeypatch.setattr(orch.time, "monotonic", fake_mono)

    rc = orch._sprint_start(argparse.Namespace())
    assert rc == orch.EXIT_MORE_WORK
