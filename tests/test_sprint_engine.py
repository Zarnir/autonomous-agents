"""Unit tests for the sprint engine (M6)."""

from __future__ import annotations

from pathlib import Path

from orchestrator import (
    STORY_POINTS,
    _sprint_size,
    compute_sprint_velocity,
    current_sprint,
    ensure_sprint_state,
    rolling_velocity_avg,
    sprint_for_story,
    story_points,
)


def _story(sid: str, status: str = "pending", complexity: str = "medium") -> dict:
    return {
        "id": sid,
        "title": sid,
        "status": status,
        "depends_on": [],
        "execution_wave": 1,
        "estimated_complexity": complexity,
        "acceptance_criteria": [],
        "tasks": [],
        "artifacts": {},
    }


def _data(stories: list[dict], sprints=None) -> dict:
    d = {
        "schema_version": "2.0",
        "version": 1,
        "epics": [{"id": "EPIC-x", "title": "x", "stories": stories}],
    }
    if sprints is not None:
        d["sprints"] = sprints
        d["current_sprint"] = sprints[-1]["number"] if sprints else 0
    return d


def test_story_points_small_medium_large():
    assert story_points(_story("s", complexity="small")) == 1
    assert story_points(_story("s", complexity="medium")) == 3
    assert story_points(_story("s", complexity="large")) == 5


def test_story_points_unknown_complexity_defaults_to_medium():
    assert story_points(_story("s", complexity="huge")) == 3
    assert story_points({"id": "s"}) == 3


def test_story_points_mapping_constants_match_plan():
    assert STORY_POINTS == {"small": 1, "medium": 3, "large": 5}


def test_sprint_size_default_when_no_config(project_root: Path):
    assert _sprint_size() == 5


def test_sprint_size_reads_from_config(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"sprint_size": 3}}', encoding="utf-8"
    )
    assert _sprint_size() == 3


def test_sprint_size_falls_back_on_bad_value(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"sprint_size": "five"}}', encoding="utf-8"
    )
    assert _sprint_size() == 5


def test_ensure_sprint_state_initializes_empty():
    data: dict = {}
    ensure_sprint_state(data)
    assert data["sprints"] == []
    assert data["current_sprint"] == 0


def test_ensure_sprint_state_preserves_existing():
    data = {"sprints": [{"number": 1}], "current_sprint": 1}
    ensure_sprint_state(data)
    assert data["sprints"] == [{"number": 1}]
    assert data["current_sprint"] == 1


def test_current_sprint_none_when_no_sprints():
    assert current_sprint(_data([])) is None


def test_current_sprint_returns_last_in_progress():
    sprints = [
        {"number": 1, "status": "completed", "story_ids": [], "velocity_points": 0},
        {"number": 2, "status": "in_progress", "story_ids": ["STORY-a"]},
    ]
    data = _data([_story("STORY-a")], sprints=sprints)
    cur = current_sprint(data)
    assert cur is not None
    assert cur["number"] == 2


def test_current_sprint_none_when_last_is_completed():
    sprints = [{"number": 1, "status": "completed", "story_ids": [], "velocity_points": 0}]
    data = _data([], sprints=sprints)
    assert current_sprint(data) is None


def test_sprint_for_story_finds_match():
    sprints = [
        {"number": 1, "status": "completed", "story_ids": ["STORY-a", "STORY-b"]},
        {"number": 2, "status": "in_progress", "story_ids": ["STORY-c"]},
    ]
    data = _data([_story("STORY-a"), _story("STORY-c")], sprints=sprints)
    assert sprint_for_story(data, "STORY-a") == 1
    assert sprint_for_story(data, "STORY-c") == 2


def test_sprint_for_story_none_when_unscheduled():
    data = _data([_story("STORY-x")], sprints=[])
    assert sprint_for_story(data, "STORY-x") is None


def test_compute_sprint_velocity_sums_completed_only():
    stories = [
        _story("STORY-a", status="completed", complexity="small"),
        _story("STORY-b", status="completed", complexity="medium"),
        _story("STORY-c", status="failed", complexity="large"),
        _story("STORY-d", status="completed", complexity="large"),
    ]
    sprints = [{"number": 1, "status": "completed",
                "story_ids": ["STORY-a", "STORY-b", "STORY-c", "STORY-d"]}]
    data = _data(stories, sprints=sprints)
    assert compute_sprint_velocity(data, 1) == 1 + 3 + 5


def test_compute_sprint_velocity_zero_for_unknown_sprint():
    data = _data([])
    assert compute_sprint_velocity(data, 99) == 0


def test_rolling_velocity_avg_zero_when_no_history():
    assert rolling_velocity_avg(_data([])) == 0.0


def test_rolling_velocity_avg_uses_last_n_sprints():
    sprints = [
        {"number": 1, "status": "completed", "velocity_points": 10, "story_ids": []},
        {"number": 2, "status": "completed", "velocity_points": 12, "story_ids": []},
        {"number": 3, "status": "completed", "velocity_points": 8, "story_ids": []},
        {"number": 4, "status": "completed", "velocity_points": 11, "story_ids": []},
    ]
    data = _data([], sprints=sprints)
    assert abs(rolling_velocity_avg(data, last_n=3) - (12 + 8 + 11) / 3) < 0.01


def test_rolling_velocity_avg_excludes_in_progress():
    sprints = [
        {"number": 1, "status": "completed", "velocity_points": 5, "story_ids": []},
        {"number": 2, "status": "in_progress", "velocity_points": 0, "story_ids": []},
    ]
    data = _data([], sprints=sprints)
    assert rolling_velocity_avg(data) == 5.0
