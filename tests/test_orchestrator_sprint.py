"""M15.2: Sprint-command coverage tests.

Exercises _sprint_plan, _sprint_end, _run_release, _run_backlog_groomer,
_sprint_status, cmd_sprint dispatcher, and helper extractors. Stubs call_agent
so no real LLM is invoked.
"""

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


def _story(sid, status="pending", complexity="small", acs=None, files=None, deps=None):
    return {
        "id": sid,
        "title": f"Story {sid}",
        "description": "do the thing",
        "status": status,
        "depends_on": deps or [],
        "execution_wave": 1,
        "estimated_complexity": complexity,
        "acceptance_criteria": acs or ["AC1: ok"],
        "tasks": [{"id": f"TASK-{sid}", "files_to_touch": files or [f"src/{sid}.py"], "type": "create"}],
        "artifacts": {},
    }


def _seed(project_root: Path, stories, sprints=None, current_sprint=0, extra=None):
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "EPIC-x", "stories": stories}],
        "sprints": sprints or [],
        "current_sprint": current_sprint,
    }
    if extra:
        data.update(extra)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# _extract_sprint_goal
# ---------------------------------------------------------------------------

def test_extract_sprint_goal_handles_goal_prefix(import_orch):
    out = "Goal: Ship the login flow\nVERDICT: SPRINT_PLANNED\n"
    assert import_orch._extract_sprint_goal(out) == "Ship the login flow"


def test_extract_sprint_goal_handles_bold_marker(import_orch):
    out = "**Goal:** Reduce flakiness in CI\nVERDICT: SPRINT_PLANNED\n"
    assert import_orch._extract_sprint_goal(out) == "Reduce flakiness in CI"


def test_extract_sprint_goal_fallback_to_first_paragraph(import_orch):
    out = "# Heading\n\nFirst real line here\nVERDICT: SPRINT_PLANNED\n"
    assert import_orch._extract_sprint_goal(out) == "First real line here"


def test_extract_sprint_goal_returns_none_for_empty(import_orch):
    assert import_orch._extract_sprint_goal("") is None


# ---------------------------------------------------------------------------
# _sprint_size
# ---------------------------------------------------------------------------

def test_sprint_size_default_when_no_config(import_orch):
    assert import_orch._sprint_size() == 5


def test_sprint_size_reads_from_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"sprint_size": 3}}), encoding="utf-8"
    )
    assert import_orch._sprint_size() == 3


def test_sprint_size_falls_back_on_malformed_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"sprint_size": "not-an-int"}}', encoding="utf-8"
    )
    assert import_orch._sprint_size() == 5


# ---------------------------------------------------------------------------
# _sprint_plan
# ---------------------------------------------------------------------------

def test_sprint_plan_picks_eligible_stories(import_orch, project_root, monkeypatch, capsys):
    _seed(project_root, [
        _story("STORY-a"), _story("STORY-b"), _story("STORY-c"),
    ])
    monkeypatch.setattr(import_orch, "call_agent",
                        lambda *a, **kw: "Goal: Ship STORY-a, b, c\nVERDICT: SPRINT_PLANNED\n")
    monkeypatch.setattr(import_orch, "_sprint_size", lambda: 3)

    rc = import_orch._sprint_plan()
    assert rc == 0
    data = import_orch.read_progress()
    assert len(data["sprints"]) == 1
    assert data["sprints"][0]["status"] == "planned"
    assert set(data["sprints"][0]["story_ids"]) == {"STORY-a", "STORY-b", "STORY-c"}
    assert (project_root / "docs" / "sprints" / "01-plan.md").exists()


def test_sprint_plan_returns_1_when_already_in_progress(import_orch, project_root, monkeypatch):
    _seed(
        project_root,
        [_story("STORY-a", status="pending")],
        sprints=[{"number": 1, "status": "in_progress", "story_ids": ["STORY-a"], "velocity_points": 0}],
        current_sprint=1,
    )
    rc = import_orch._sprint_plan()
    assert rc == 1


def test_sprint_plan_returns_1_when_no_eligible(import_orch, project_root, monkeypatch):
    _seed(project_root, [_story("STORY-done", status="completed")])
    rc = import_orch._sprint_plan()
    assert rc == 1


def test_sprint_plan_falls_back_on_agent_error(import_orch, project_root, monkeypatch):
    _seed(project_root, [_story("STORY-a")])

    def boom(*a, **kw):
        raise import_orch.AgentError("sprint-planner", "model unavailable")
    monkeypatch.setattr(import_orch, "call_agent", boom)
    monkeypatch.setattr(import_orch, "_sprint_size", lambda: 1)

    rc = import_orch._sprint_plan()
    assert rc == 0
    data = import_orch.read_progress()
    assert data["sprints"][0]["status"] == "planned"


# ---------------------------------------------------------------------------
# _sprint_end
# ---------------------------------------------------------------------------

def test_sprint_end_no_sprints_returns_1(import_orch, project_root, capsys):
    _seed(project_root, [])
    rc = import_orch._sprint_end()
    assert rc == 1


def test_sprint_end_already_ended_returns_0(import_orch, project_root):
    _seed(
        project_root,
        [_story("STORY-a", status="completed")],
        sprints=[{
            "number": 1, "status": "completed", "story_ids": ["STORY-a"],
            "velocity_points": 1,
        }],
    )
    rc = import_orch._sprint_end()
    assert rc == 0


def test_sprint_end_writes_retro_and_marks_completed(import_orch, project_root, monkeypatch):
    story = _story("STORY-a", status="completed")
    story["artifacts"] = {"commit_hash": "abc1234", "implementation_files": ["src/a.py"], "test_files": []}
    _seed(
        project_root, [story],
        sprints=[{
            "number": 1, "status": "in_progress", "goal": "Test goal",
            "started_at": "2026-05-17T00:00:00Z", "story_ids": ["STORY-a"],
            "velocity_points": 0,
        }],
        current_sprint=1,
    )

    def fake_call(name, prompt, **kw):
        if name == "retro":
            return "# Retro\n\nWent well: speed\nVERDICT: RETRO_COMPLETE\n"
        return "# Release v0.1\n\nshipped\nVERDICT: RELEASE_NOTED\n"

    monkeypatch.setattr(import_orch, "call_agent", fake_call)

    rc = import_orch._sprint_end()
    assert rc == 0
    data = import_orch.read_progress()
    assert data["sprints"][0]["status"] == "completed"
    assert data["sprints"][0]["velocity_points"] == 1
    assert (project_root / "docs" / "sprints" / "01-retro.md").exists()


def test_sprint_end_retro_agent_error_writes_fallback(import_orch, project_root, monkeypatch):
    _seed(
        project_root, [_story("STORY-a", status="completed")],
        sprints=[{
            "number": 1, "status": "in_progress", "goal": "Test goal",
            "story_ids": ["STORY-a"], "velocity_points": 0,
        }],
        current_sprint=1,
    )

    def boom(*a, **kw):
        raise import_orch.AgentError("retro", "model unavailable")
    monkeypatch.setattr(import_orch, "call_agent", boom)

    rc = import_orch._sprint_end()
    assert rc == 0
    text = (project_root / "docs" / "sprints" / "01-retro.md").read_text()
    assert "deterministic fallback" in text or "@retro agent error" in text


def test_sprint_end_counts_open_impediments(import_orch, project_root, monkeypatch):
    """An existing impediments.md with `Status: open` lines should be counted."""
    _seed(
        project_root, [_story("STORY-a", status="completed")],
        sprints=[{"number": 1, "status": "in_progress", "goal": "g",
                  "story_ids": ["STORY-a"], "velocity_points": 0}],
        current_sprint=1,
    )
    (project_root / "docs").mkdir(exist_ok=True)
    (project_root / "docs" / "impediments.md").write_text(
        "# Impediments\n\n- IMP-1\n  Status: open\n- IMP-2\n  Status: resolved\n- IMP-3\n  Status: open\n"
    )

    captured = {}
    def fake_call(name, prompt, **kw):
        captured["prompt"] = prompt
        return "VERDICT: RETRO_COMPLETE\n"
    monkeypatch.setattr(import_orch, "call_agent", fake_call)

    import_orch._sprint_end()
    assert "Open impediments:** 2" in captured["prompt"]


# ---------------------------------------------------------------------------
# _run_release
# ---------------------------------------------------------------------------

def test_run_release_writes_release_note(import_orch, project_root, monkeypatch):
    story = _story("STORY-a", status="completed")
    story["artifacts"] = {
        "commit_hash": "deadbeef1234567",
        "implementation_files": ["src/a.py"],
        "test_files": ["tests/test_a.py"],
    }
    _seed(project_root, [story])
    data = import_orch.read_progress()
    sprint = {
        "number": 1, "goal": "ship", "story_ids": ["STORY-a"],
        "status": "completed", "velocity_points": 1,
    }

    monkeypatch.setattr(import_orch, "call_agent",
                        lambda *a, **kw: "# Release\nVERDICT: RELEASE_NOTED\n")

    import_orch._run_release(data, sprint)
    assert (project_root / "docs" / "releases" / "v0.1.md").exists()
    assert sprint["release_version"] == "v0.1"


def test_run_release_skips_when_no_completed_commits(import_orch, project_root, capsys):
    _seed(project_root, [_story("STORY-pending", status="pending")])
    data = import_orch.read_progress()
    sprint = {
        "number": 1, "goal": "ship", "story_ids": ["STORY-pending"],
        "status": "completed", "velocity_points": 0,
    }
    import_orch._run_release(data, sprint)
    rel_dir = project_root / "docs" / "releases"
    assert not rel_dir.exists() or not any(rel_dir.iterdir())


def test_run_release_fallback_when_agent_fails(import_orch, project_root, monkeypatch):
    story = _story("STORY-a", status="completed")
    story["artifacts"] = {"commit_hash": "deadbeef", "implementation_files": ["src/a.py"], "test_files": []}
    _seed(project_root, [story])
    data = import_orch.read_progress()
    sprint = {"number": 2, "goal": "g", "story_ids": ["STORY-a"], "status": "completed", "velocity_points": 1}

    def boom(*a, **kw):
        raise import_orch.AgentError("release", "broken")
    monkeypatch.setattr(import_orch, "call_agent", boom)

    import_orch._run_release(data, sprint)
    txt = (project_root / "docs" / "releases" / "v0.2.md").read_text()
    assert "Stories shipped" in txt or "VERDICT: RELEASE_NOTED" in txt


# ---------------------------------------------------------------------------
# _run_backlog_groomer
# ---------------------------------------------------------------------------

def test_backlog_groomer_skips_when_no_pending(import_orch, project_root, monkeypatch):
    _seed(project_root, [_story("STORY-done", status="completed")])
    called = {"n": 0}

    def counter(*a, **kw):
        called["n"] += 1
        return ""
    monkeypatch.setattr(import_orch, "call_agent", counter)
    import_orch._run_backlog_groomer()
    assert called["n"] == 0


def test_backlog_groomer_writes_advisory_doc(import_orch, project_root, monkeypatch):
    _seed(
        project_root, [_story("STORY-pending")],
        sprints=[{"number": 1, "status": "completed", "story_ids": [],
                  "velocity_points": 0, "retro_path": ""}],
    )
    monkeypatch.setattr(import_orch, "call_agent",
                        lambda *a, **kw: "# Grooming notes\n\nNothing surprising\n")
    import_orch._run_backlog_groomer()
    assert (project_root / "docs" / "sprints" / "01-grooming.md").exists()


def test_backlog_groomer_silent_on_agent_failure(import_orch, project_root, monkeypatch):
    _seed(project_root, [_story("STORY-pending")])

    def boom(*a, **kw):
        raise import_orch.AgentError("backlog-groomer", "rate limited")
    monkeypatch.setattr(import_orch, "call_agent", boom)
    import_orch._run_backlog_groomer()  # must not raise


# ---------------------------------------------------------------------------
# _sprint_status
# ---------------------------------------------------------------------------

def test_sprint_status_no_sprints(import_orch, project_root, capsys):
    _seed(project_root, [])
    rc = import_orch._sprint_status()
    out = capsys.readouterr().out
    assert rc == 0
    assert "No sprints" in out


def test_sprint_status_shows_active_sprint(import_orch, project_root, capsys):
    _seed(
        project_root, [_story("STORY-a")],
        sprints=[{
            "number": 1, "status": "in_progress", "goal": "Ship login",
            "story_ids": ["STORY-a"], "velocity_points": 0,
        }],
        current_sprint=1,
    )
    rc = import_orch._sprint_status()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Active sprint: #1" in out
    assert "Ship login" in out


def test_sprint_status_shows_last_completed_sprint(import_orch, project_root, capsys):
    _seed(
        project_root, [_story("STORY-a", status="completed")],
        sprints=[{
            "number": 1, "status": "completed", "goal": "g",
            "story_ids": ["STORY-a"], "velocity_points": 1,
        }],
    )
    rc = import_orch._sprint_status()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Last sprint: #1" in out


# ---------------------------------------------------------------------------
# cmd_sprint dispatcher
# ---------------------------------------------------------------------------

def test_cmd_sprint_dispatches_to_plan(import_orch, monkeypatch):
    called = {}

    def fake_plan():
        called["plan"] = True
        return 0
    monkeypatch.setattr(import_orch, "_sprint_plan", fake_plan)
    rc = import_orch.cmd_sprint(argparse.Namespace(action="plan"))
    assert rc == 0
    assert called.get("plan")


def test_cmd_sprint_dispatches_to_end(import_orch, monkeypatch):
    called = {}

    def fake_end():
        called["end"] = True
        return 0
    monkeypatch.setattr(import_orch, "_sprint_end", fake_end)
    rc = import_orch.cmd_sprint(argparse.Namespace(action="end"))
    assert rc == 0
    assert called.get("end")


def test_cmd_sprint_dispatches_to_status(import_orch, monkeypatch):
    called = {}

    def fake_status():
        called["status"] = True
        return 0
    monkeypatch.setattr(import_orch, "_sprint_status", fake_status)
    rc = import_orch.cmd_sprint(argparse.Namespace(action="status"))
    assert rc == 0
    assert called.get("status")


def test_cmd_sprint_unknown_action_dies(import_orch):
    with pytest.raises(SystemExit):
        import_orch.cmd_sprint(argparse.Namespace(action="unknown_action_xyz"))


# ---------------------------------------------------------------------------
# Velocity / sprint state helpers
# ---------------------------------------------------------------------------

def test_compute_sprint_velocity_sums_completed_only(import_orch):
    data = {
        "epics": [{"id": "E", "stories": [
            _story("S1", status="completed", complexity="small"),
            _story("S2", status="pending", complexity="large"),
            _story("S3", status="completed", complexity="medium"),
        ]}],
        "sprints": [{"number": 1, "status": "in_progress",
                     "story_ids": ["S1", "S2", "S3"], "velocity_points": 0}],
    }
    # small=1, medium=3, pending S2 excluded → 4
    assert import_orch.compute_sprint_velocity(data, 1) == 4


def test_compute_sprint_velocity_missing_sprint(import_orch):
    assert import_orch.compute_sprint_velocity({"sprints": []}, 999) == 0


def test_rolling_velocity_avg_returns_zero_when_no_completed(import_orch):
    assert import_orch.rolling_velocity_avg({"sprints": []}) == 0.0


def test_rolling_velocity_avg_averages_last_3(import_orch):
    data = {"sprints": [
        {"number": 1, "status": "completed", "velocity_points": 10},
        {"number": 2, "status": "completed", "velocity_points": 20},
        {"number": 3, "status": "completed", "velocity_points": 30},
        {"number": 4, "status": "completed", "velocity_points": 40},
    ]}
    assert import_orch.rolling_velocity_avg(data) == 30.0


def test_current_sprint_returns_only_in_progress(import_orch):
    data = {"sprints": [
        {"number": 1, "status": "completed"},
        {"number": 2, "status": "planned"},
    ]}
    assert import_orch.current_sprint(data) is None
    data["sprints"].append({"number": 3, "status": "in_progress"})
    assert import_orch.current_sprint(data)["number"] == 3


def test_sprint_for_story_finds_assignment(import_orch):
    data = {"sprints": [
        {"number": 1, "story_ids": ["A", "B"]},
        {"number": 2, "story_ids": ["C"]},
    ]}
    assert import_orch.sprint_for_story(data, "B") == 1
    assert import_orch.sprint_for_story(data, "C") == 2
    assert import_orch.sprint_for_story(data, "ZZZ") is None
