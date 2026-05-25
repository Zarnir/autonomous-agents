"""M15.8 final: run_loop product-review branches + story-execution error paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _completed_story(sid, depends_on=None):
    return {
        "id": sid, "title": f"S {sid}", "status": "completed",
        "depends_on": depends_on or [], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {"commit_hash": f"hash-{sid}"},
    }


def _pending_story(sid):
    return {
        "id": sid, "title": f"S {sid}", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {},
    }


def _seed(project_root, stories, **extra):
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": stories}],
        "sprints": [], "current_sprint": 0,
        "completed_stories": [s["id"] for s in stories if s["status"] == "completed"],
    }
    data.update(extra)
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _enable_product_review(project_root):
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"product_review_enabled": True,
                                  "max_product_review_cycles": 2}}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# run_loop product review branches
# ---------------------------------------------------------------------------

def test_run_loop_product_review_pass_as_is(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root, [_completed_story("S1")])
    _enable_product_review(project_root)

    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))
    monkeypatch.setattr(orch, "run_product_review",
                        lambda d: ("PASS_AS_IS", {"verdict": "PASS_AS_IS"}))

    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == 0
    assert orch.read_progress()["status"] == "completed"


def test_run_loop_product_review_follow_up_stories(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root, [_completed_story("S1")])
    _enable_product_review(project_root)

    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))
    monkeypatch.setattr(orch, "run_product_review",
                        lambda d: ("FOLLOW_UP_STORIES",
                                   {"verdict": "FOLLOW_UP_STORIES",
                                    "story_blocks": ["block1", "block2"]}))

    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == 0
    after = orch.read_progress()
    assert after["status"] == "completed"
    assert after.get("product_review_suggestions") == ["block1", "block2"]


def test_run_loop_product_review_reopen_resets_stories(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root, [_completed_story("S1"), _completed_story("S2")])
    _enable_product_review(project_root)

    review_calls = {"n": 0}

    def fake_review(d):
        review_calls["n"] += 1
        if review_calls["n"] == 1:
            return ("REOPEN", {"verdict": "REOPEN", "story_ids": ["S1"]})
        return ("PASS_AS_IS", {"verdict": "PASS_AS_IS"})

    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))
    monkeypatch.setattr(orch, "run_product_review", fake_review)

    def fake_run_story(data, story):
        for ep in data["epics"]:
            for s in ep["stories"]:
                if s["id"] == story["id"]:
                    s["status"] = "completed"
                    s.setdefault("artifacts", {})["commit_hash"] = "rerun-hash"
        return data

    monkeypatch.setattr(orch, "run_story", fake_run_story)
    monkeypatch.setattr(orch, "run_watcher", lambda d: 0)

    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == 0
    assert review_calls["n"] == 2


def test_run_loop_product_review_cycle_cap(import_orch, project_root, monkeypatch):
    orch = import_orch
    stories = [_completed_story("S1")]
    _seed(project_root, stories, product_review_cycles=5)
    _enable_product_review(project_root)

    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))
    called = {"n": 0}

    def fake_review(d):
        called["n"] += 1
        return ("PASS_AS_IS", {})
    monkeypatch.setattr(orch, "run_product_review", fake_review)

    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == 0
    assert called["n"] == 0
    assert orch.read_progress()["status"] == "completed"


# ---------------------------------------------------------------------------
# run_loop story-execution error paths
# ---------------------------------------------------------------------------

def test_run_loop_agent_error_finalizes_story_and_continues(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root, [_pending_story("S1"), _pending_story("S2")])

    def fake_run_story(data, story):
        if story["id"] == "S1":
            raise orch.AgentError("make", "timeout")
        for ep in data["epics"]:
            for s in ep["stories"]:
                if s["id"] == "S2":
                    s["status"] = "completed"
                    s.setdefault("artifacts", {})["commit_hash"] = "abc"
        return data

    monkeypatch.setattr(orch, "run_story", fake_run_story)
    monkeypatch.setattr(orch, "run_watcher", lambda d: 0)
    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))

    data = orch.read_progress()
    rc = orch.run_loop(data)
    # M20: any story failing → exit code 2 (was 0 pre-M20). The loop still
    # continues past S1 and runs S2, but the overall run reports failure.
    assert rc == 2
    final = orch.read_progress()
    assert final["status"] == "failed"
    statuses = {s["id"]: s["status"] for s in final["epics"][0]["stories"]}
    assert statuses["S1"] == "failed"
    assert statuses["S2"] == "completed"


def test_run_loop_unexpected_exception_halts_with_rc1(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root, [_pending_story("S1")])

    def boom(data, story):
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(orch, "run_story", boom)
    monkeypatch.setattr(orch, "run_watcher", lambda d: 0)

    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == 1
    final = orch.read_progress()
    assert final["epics"][0]["stories"][0]["status"] == "failed"


def test_run_loop_from_story_skip_ahead_dies(import_orch, project_root):
    orch = import_orch
    _seed(project_root, [_pending_story("S1"), _pending_story("S2")])

    data = orch.read_progress()
    with pytest.raises(SystemExit):
        orch.run_loop(data, from_story="S2")


def test_run_loop_from_story_matches_starts_pipeline(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed(project_root, [_pending_story("S1")])

    def fake_run_story(data, story):
        for ep in data["epics"]:
            for s in ep["stories"]:
                if s["id"] == story["id"]:
                    s["status"] = "completed"
                    s.setdefault("artifacts", {})["commit_hash"] = "x"
        return data

    monkeypatch.setattr(orch, "run_story", fake_run_story)
    monkeypatch.setattr(orch, "run_watcher", lambda d: 0)
    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))

    data = orch.read_progress()
    rc = orch.run_loop(data, from_story="S1")
    assert rc == 0
