"""M17.7 micro: final targeted tests for remaining easy branches."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _seed(project_root, stories=None):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": stories or []}],
        "sprints": [],
    }), encoding="utf-8")


def test_cmd_refine_succeeds_when_architect_refines_and_spec_validates(
    import_orch, project_root, monkeypatch
):
    _seed(project_root, stories=[{
        "id": "STORY-a", "title": "Big", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "large", "acceptance_criteria": ["AC1"],
        "tasks": [{"id": "T1", "files_to_touch": ["src/a.py"]}],
        "artifacts": {},
    }])
    monkeypatch.setattr(import_orch, "call_agent",
                        lambda *a, **kw: "Refined\nVERDICT: EPIC_REFINED\n")
    from spec_parser import ValidationReport
    monkeypatch.setattr(import_orch, "validate_specs",
                        lambda root: ValidationReport())

    rc = import_orch.cmd_refine(argparse.Namespace(story="STORY-a"))
    assert rc == 0


def test_cmd_refine_dies_when_spec_parser_unavailable(import_orch, project_root, monkeypatch):
    _seed(project_root, stories=[{
        "id": "STORY-a", "title": "Big", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "large", "acceptance_criteria": ["AC1"],
        "tasks": [{"id": "T1", "files_to_touch": ["src/a.py"]}],
        "artifacts": {},
    }])
    monkeypatch.setattr(import_orch, "call_agent",
                        lambda *a, **kw: "VERDICT: EPIC_REFINED\n")
    monkeypatch.setattr(import_orch, "validate_specs", None)
    with pytest.raises(SystemExit):
        import_orch.cmd_refine(argparse.Namespace(story="STORY-a"))


def test_cmd_adr_with_story_id_includes_in_prompt(import_orch, project_root, monkeypatch):
    captured = {}
    def fake_call(name, prompt, **kw):
        captured["prompt"] = prompt
        adr_dir = project_root / "docs" / "adr"
        adr_dir.mkdir(parents=True, exist_ok=True)
        (adr_dir / "0001-test.md").write_text("VERDICT: ADR_PROPOSED\n", encoding="utf-8")
        return "VERDICT: ADR_PROPOSED\n"
    monkeypatch.setattr(import_orch, "call_agent", fake_call)

    rc = import_orch.cmd_adr(argparse.Namespace(question="test", story="STORY-x"))
    assert rc == 0
    assert "Related story: STORY-x" in captured["prompt"]


def test_run_watcher_with_no_signals_returns_zero(import_orch, project_root):
    data = {"epics": [{"id": "E", "stories": []}], "execution_log": []}
    assert import_orch.run_watcher(data) == 0


def test_cmd_agent_dies_on_permission_conflict(import_orch, project_root, monkeypatch):
    agents = project_root / ".opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\ndescription: engineer\nimports: [edit-skill]\npermission:\n  edit: deny\n---\nbody\n",
        encoding="utf-8",
    )
    skills_dir = project_root / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "edit-skill.md").write_text(
        "---\nid: edit-skill\ndescription: x\napplicable_agents: [engineer]\n"
        "requires:\n  edit: true\n---\nbody\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        import_orch.cmd_agent(argparse.Namespace(
            agent="engineer", skill="edit-skill", prompt="hi",
        ))


def test_run_release_with_auto_tag_enabled(import_orch, project_root, monkeypatch):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"auto_tag": True, "auto_push_tags": False}}),
        encoding="utf-8",
    )

    tag_called = {"n": 0}
    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "tag"]:
            tag_called["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(import_orch.subprocess, "run", fake_run)
    monkeypatch.setattr(import_orch, "call_agent",
                        lambda *a, **kw: "release notes\nVERDICT: RELEASE_NOTED\n")

    story = {
        "id": "STORY-a", "status": "completed",
        "artifacts": {
            "commit_hash": "abc",
            "implementation_files": ["src/a.py"],
            "test_files": [],
        },
    }
    _seed(project_root, stories=[story])
    data = import_orch.read_progress()
    sprint = {"number": 3, "goal": "g", "story_ids": ["STORY-a"], "velocity_points": 1}

    import_orch._run_release(data, sprint)
    assert tag_called["n"] == 1


def test_run_loop_product_review_unknown_proceeds(import_orch, project_root, monkeypatch):
    _seed(project_root, stories=[{
        "id": "S1", "title": "t", "status": "completed",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {"commit_hash": "abc"},
    }])
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"product_review_enabled": True}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(import_orch, "run_production_gates", lambda d: (True, []))
    monkeypatch.setattr(import_orch, "run_product_review",
                        lambda d: ("UNKNOWN", {"verdict": "UNKNOWN"}))

    data = import_orch.read_progress()
    rc = import_orch.run_loop(data)
    assert rc == 0
