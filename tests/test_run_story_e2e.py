"""End-to-end test for the run_story 6-phase pipeline (M13.6).

Stubs the phase functions (run_review_loop, run_test_writer,
run_implementation_with_verification, run_commit) to drive the state machine
through three scenarios:
  - happy path: all phases pass → status=completed with commit_hash
  - design-review block: short-circuits at phase 3b → status=blocked
  - implementation failure: phase 3d fails GREEN_VERIFIED → status=failed
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    """chdir-only fixture — see test_cmd_wizard_integration.py for rationale."""
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _seed_progress(project_root: Path, story_id: str = "STORY-x") -> dict:
    (project_root / ".opencode").mkdir()
    data = {
        "schema_version": "2.0",
        "version": 1,
        "updated_at": "2026-05-12T00:00:00Z",
        "status": "in_progress",
        "current_story_id": None,
        "completed_stories": [],
        "failed_stories": [],
        "blocked_stories": [],
        "epics": [{
            "id": "EPIC-x",
            "title": "Test epic",
            "stories": [{
                "id": story_id,
                "title": "Test story",
                "status": "pending",
                "depends_on": [],
                "execution_wave": 1,
                "estimated_complexity": "small",
                "acceptance_criteria": ["AC1: the thing works"],
                "tasks": [{"id": "TASK-x", "files_to_touch": ["src/foo.py"], "type": "create"}],
                "artifacts": {},
            }],
        }],
        "execution_log": [],
    }
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    return data


def _stub_all_phases_pass(orch, monkeypatch):
    """Wire stubs so every phase reports success — used by the happy-path test."""
    monkeypatch.setattr(orch, "setup_worktree", lambda data, story: None)
    monkeypatch.setattr(orch, "teardown_worktree", lambda story: None)
    monkeypatch.setattr(
        orch, "run_review_loop",
        lambda story, mode, impl_files, cwd=None: ("PASS", {"check": "ok", "simplify": "ok"}),
    )
    monkeypatch.setattr(
        orch, "run_test_writer",
        lambda story, prior_findings, cwd=None: {
            "status": "OK",
            "test_files": ["tests/test_foo.py"],
            "criterion_test_mapping": {"AC1": ["tests/test_foo.py::test_works"]},
        },
    )
    monkeypatch.setattr(
        orch, "run_implementation_with_verification",
        lambda story, cwd=None: {
            "status": "GREEN_VERIFIED",
            "files": ["src/foo.py"],
            "test_evidence_hash": "abc123",
            "detail": "ok",
        },
    )
    monkeypatch.setattr(
        orch, "run_commit",
        lambda story, cwd=None: {
            "ok": True,
            "hash": "deadbeef1234567",
            "branch": "feat/EPIC-x/STORY-x-test-story",
            "detail": "committed",
        },
    )


def test_run_story_happy_path_completes_with_commit(import_orch, project_root, monkeypatch):
    """All phases pass → status=completed, artifacts populated, story moves to completed_stories."""
    orch = import_orch
    _seed_progress(project_root)
    _stub_all_phases_pass(orch, monkeypatch)

    data = orch.read_progress()
    story = orch.find_story(data, "STORY-x")
    result = orch.run_story(data, story)

    final_story = orch.find_story(result, "STORY-x")
    assert final_story["status"] == "completed", f"story not completed: {final_story}"
    assert "STORY-x" in result.get("completed_stories", [])
    arts = final_story["artifacts"]
    assert arts["test_files"] == ["tests/test_foo.py"]
    assert arts["implementation_files"] == ["src/foo.py"]
    assert arts["commit_hash"] == "deadbeef1234567"
    assert arts["branch"].startswith("feat/")


def test_run_story_design_review_block_marks_blocked(import_orch, project_root, monkeypatch):
    """If design review returns BLOCK, story is marked blocked WITHOUT calling later phases."""
    orch = import_orch
    _seed_progress(project_root)

    monkeypatch.setattr(orch, "setup_worktree", lambda data, story: None)
    monkeypatch.setattr(orch, "teardown_worktree", lambda story: None)

    monkeypatch.setattr(
        orch, "run_review_loop",
        lambda story, mode, impl_files, cwd=None: ("BLOCK", {}),
    )

    def canary(*a, **kw):
        raise AssertionError("phase should not run after design review BLOCK")

    monkeypatch.setattr(orch, "run_test_writer", canary)
    monkeypatch.setattr(orch, "run_implementation_with_verification", canary)
    monkeypatch.setattr(orch, "run_commit", canary)

    data = orch.read_progress()
    story = orch.find_story(data, "STORY-x")
    result = orch.run_story(data, story)

    final_story = orch.find_story(result, "STORY-x")
    assert final_story["status"] == "blocked"
    assert "STORY-x" in result.get("blocked_stories", [])
    assert any("design_review" in entry.get("msg", "") for entry in result.get("execution_log", []))


def test_run_story_impl_failure_marks_failed(import_orch, project_root, monkeypatch):
    """If implementation phase returns non-GREEN, story is marked failed."""
    orch = import_orch
    _seed_progress(project_root)

    monkeypatch.setattr(orch, "setup_worktree", lambda data, story: None)
    monkeypatch.setattr(orch, "teardown_worktree", lambda story: None)
    monkeypatch.setattr(
        orch, "run_review_loop",
        lambda story, mode, impl_files, cwd=None: ("PASS", {"check": "ok", "simplify": "ok"}),
    )
    monkeypatch.setattr(
        orch, "run_test_writer",
        lambda story, prior_findings, cwd=None: {
            "status": "OK",
            "test_files": ["tests/test_foo.py"],
            "criterion_test_mapping": {"AC1": ["tests/test_foo.py::test_works"]},
        },
    )
    monkeypatch.setattr(
        orch, "run_implementation_with_verification",
        lambda story, cwd=None: {"status": "FAIL", "detail": "guard_out_of_scope"},
    )

    def canary(*a, **kw):
        raise AssertionError("@commit should not run after impl failure")

    monkeypatch.setattr(orch, "run_commit", canary)

    data = orch.read_progress()
    story = orch.find_story(data, "STORY-x")
    result = orch.run_story(data, story)

    final_story = orch.find_story(result, "STORY-x")
    assert final_story["status"] == "failed"
    assert "STORY-x" in result.get("failed_stories", [])
    assert any(
        "guard_out_of_scope" in entry.get("msg", "")
        for entry in result.get("execution_log", [])
    )
