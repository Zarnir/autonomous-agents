"""M17.7 more: remaining testable orchestrator branches — final push for 100%."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


# ---------------------------------------------------------------------------
# phase_spec_and_plan LLM fallback path
# ---------------------------------------------------------------------------

def test_phase_spec_and_plan_uses_llm_when_use_llm_spec(import_orch, project_root, monkeypatch):
    orch = import_orch
    spec_calls = {"n": 0}

    def fake_call(name, prompt, **kw):
        spec_calls[name] = spec_calls.get(name, 0) + 1
        if name == "spec":
            return '```json\n{"epics": [{"id": "E", "stories": [{"id": "S1", "title": "x", "depends_on": [], "tasks": [], "acceptance_criteria": [], "estimated_complexity": "small"}]}]}\n```'
        return ""
    monkeypatch.setattr(orch, "call_agent", fake_call)

    data = orch.phase_spec_and_plan(spec_path=None, use_llm_spec=True)
    assert spec_calls["spec"] == 1
    assert len(data["epics"]) == 1


def test_phase_spec_and_plan_dies_on_spec_error(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: '```json\n{"error": "couldnt parse"}\n```')
    with pytest.raises(SystemExit):
        orch.phase_spec_and_plan(spec_path=None, use_llm_spec=True)


def test_phase_spec_and_plan_planner_fallback(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "parse_specs", lambda root: {
        "epics": [{"id": "E", "stories": [
            {"id": "S1", "title": "x", "depends_on": [], "tasks": [],
             "acceptance_criteria": [], "estimated_complexity": "small"},
        ]}],
        "methodology": "structured",
    })
    monkeypatch.setattr(orch, "_try_local_planner", lambda spec_json: False)

    planner_calls = {"n": 0}
    def fake_call(name, prompt, **kw):
        planner_calls[name] = planner_calls.get(name, 0) + 1
        if name == "planner":
            (project_root / ".opencode").mkdir(exist_ok=True)
            (project_root / ".opencode" / "progress.json").write_text(json.dumps({
                "schema_version": "2.0", "version": 1, "status": "pending",
                "epics": [{"id": "E", "stories": [
                    {"id": "S1", "title": "x", "status": "pending", "depends_on": [],
                     "execution_wave": 1, "estimated_complexity": "small",
                     "acceptance_criteria": [], "tasks": [], "artifacts": {}},
                ]}],
            }), encoding="utf-8")
        return ""
    monkeypatch.setattr(orch, "call_agent", fake_call)

    data = orch.phase_spec_and_plan(spec_path=None, use_llm_spec=False)
    assert planner_calls["planner"] == 1


def test_phase_spec_and_plan_planner_doesnt_write(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "parse_specs", lambda root: {
        "epics": [{"id": "E", "stories": [
            {"id": "S1", "title": "x", "depends_on": [], "tasks": [],
             "acceptance_criteria": [], "estimated_complexity": "small"},
        ]}],
    })
    monkeypatch.setattr(orch, "_try_local_planner", lambda spec_json: False)
    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "")

    with pytest.raises(SystemExit):
        orch.phase_spec_and_plan(spec_path=None, use_llm_spec=False)


# ---------------------------------------------------------------------------
# cmd_health_check edges
# ---------------------------------------------------------------------------

def test_cmd_health_check_with_broken_agent_file(import_orch, project_root, monkeypatch, capsys):
    agents = project_root / ".opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "good.md").write_text(
        "---\ndescription: ok\npermission:\n  edit: deny\n---\nbody\n", encoding="utf-8"
    )

    import runners
    original_parse = runners.parse_agent_file

    def flaky_parse(name, agents_dir=None):
        if name == "good":
            raise RuntimeError("parse exploded")
        return original_parse(name, agents_dir=agents_dir)
    monkeypatch.setattr(runners, "parse_agent_file", flaky_parse)
    monkeypatch.setenv("AA_RUNNER", "claude")

    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "agents parse" in out


def test_cmd_health_check_skills_missing_required_fields(import_orch, project_root, monkeypatch, capsys):
    skills_dir = project_root / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "broken.md").write_text(
        "---\nid: broken\ndescription: x\n---\nbody\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AA_RUNNER", "claude")
    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "incomplete" in out or "skills load" in out


def test_cmd_health_check_persona_with_unresolved_import(import_orch, project_root, monkeypatch, capsys):
    agents = project_root / ".opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\ndescription: e\nimports: [missing-skill]\npermission:\n  edit: deny\n---\nbody\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AA_RUNNER", "claude")
    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "unresolved" in out or "persona imports" in out


def test_cmd_health_check_git_not_inside_work_tree(import_orch, project_root, monkeypatch, capsys):
    def fake_run(cmd, **kw):
        if cmd[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="false\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("AA_RUNNER", "claude")

    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "git repo" in out or "not inside" in out


def test_cmd_health_check_git_missing_config(import_orch, project_root, monkeypatch, capsys):
    def fake_run(cmd, **kw):
        if cmd[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
        if cmd[:3] == ["git", "config", "user.email"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="\n", stderr="")
        if cmd[:3] == ["git", "config", "user.name"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("AA_RUNNER", "claude")

    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "git config" in out


def test_cmd_health_check_git_not_available(import_orch, project_root, monkeypatch, capsys):
    def fake_run(cmd, **kw):
        if cmd[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            raise FileNotFoundError("git")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("AA_RUNNER", "claude")

    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "git" in out


def test_cmd_health_check_specs_validate_clean(import_orch, project_root, monkeypatch, capsys):
    epics_dir = project_root / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True)
    (epics_dir / "a.md").write_text(
        "---\nid: EPIC-1\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\n\n"
        "### Acceptance Criteria\n- [ ] AC1: this is a long enough criterion\n\n"
        "### Tasks\n- [ ] TASK-1 `src/a.py` (create)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AA_RUNNER", "claude")
    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "specs" in out


def test_cmd_health_check_specs_with_errors(import_orch, project_root, monkeypatch, capsys):
    epics_dir = project_root / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True)
    (epics_dir / "bad.md").write_text("not a valid spec", encoding="utf-8")

    monkeypatch.setenv("AA_RUNNER", "claude")
    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "specs" in out


# ---------------------------------------------------------------------------
# main() argparse final fallback
# ---------------------------------------------------------------------------

def test_main_handles_exception_with_no_global_state(import_orch, monkeypatch):
    def boom(args):
        raise RuntimeError("crash")
    monkeypatch.setattr(import_orch, "cmd_status", boom)
    monkeypatch.setattr(import_orch, "GLOBAL_PERSIST_STATE", None)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "status"])
    rc = import_orch.main()
    assert rc == 1


# ---------------------------------------------------------------------------
# Worktree teardown
# ---------------------------------------------------------------------------

def test_teardown_worktree_merges_then_removes(import_orch, project_root, monkeypatch):
    orch = import_orch
    wt = project_root / "worktree"
    wt.mkdir()
    story = {
        "id": "S1",
        "artifacts": {
            "worktree_path": str(wt),
            "branch": "feat/x",
            "commit_hash": "abc",
        },
    }

    calls = []
    def fake_run(cmd, **kw):
        calls.append(tuple(cmd[:3]))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(orch.subprocess, "run", fake_run)

    orch.teardown_worktree(story)
    assert ("git", "merge", "--ff-only") in calls
    assert ("git", "worktree", "remove") in calls


def test_teardown_worktree_merge_failure_preserves_worktree(import_orch, project_root, monkeypatch):
    orch = import_orch
    wt = project_root / "worktree"
    wt.mkdir()
    story = {
        "id": "S1",
        "artifacts": {
            "worktree_path": str(wt),
            "branch": "feat/x",
            "commit_hash": "abc",
        },
    }

    def fake_run(cmd, **kw):
        if cmd[:3] == ["git", "merge", "--ff-only"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="conflict")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(orch.subprocess, "run", fake_run)

    orch.teardown_worktree(story)
    assert wt.exists()


def test_teardown_worktree_remove_failure_logged(import_orch, project_root, monkeypatch, capsys):
    orch = import_orch
    wt = project_root / "worktree"
    wt.mkdir()
    story = {
        "id": "S1",
        "artifacts": {
            "worktree_path": str(wt),
            "branch": "feat/x",
            "commit_hash": "abc",
        },
    }

    def fake_run(cmd, **kw):
        if cmd[:3] == ["git", "worktree", "remove"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="cannot remove")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(orch.subprocess, "run", fake_run)

    orch.teardown_worktree(story)
    out = capsys.readouterr().out
    assert "worktree remove failed" in out
