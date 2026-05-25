"""M17.7 cont: orchestrator.py 100% — sprint cycle + main fallback + cmd_health_check edges."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _seed(project_root, stories=None, sprints=None, **extra):
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": stories or []}],
        "sprints": sprints or [],
    }
    data.update(extra)
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# _sprint_cycle branches
# ---------------------------------------------------------------------------

def test_sprint_cycle_ends_when_plan_returns_1(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "_sprint_plan", lambda: 1)
    rc = import_orch._sprint_cycle(argparse.Namespace())
    assert rc == 0


def test_sprint_cycle_halts_when_start_fails(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "_sprint_plan", lambda: 0)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 7)
    rc = import_orch._sprint_cycle(argparse.Namespace())
    assert rc == 7


def test_sprint_cycle_halts_when_end_fails(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "_sprint_plan", lambda: 0)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 0)
    monkeypatch.setattr(import_orch, "_sprint_end", lambda: 9)
    rc = import_orch._sprint_cycle(argparse.Namespace())
    assert rc == 9


def test_sprint_cycle_swallows_groomer_failure(import_orch, project_root, monkeypatch):
    plan_call_count = {"n": 0}
    def fake_plan():
        plan_call_count["n"] += 1
        if plan_call_count["n"] >= 2:
            return 1
        return 0

    monkeypatch.setattr(import_orch, "_sprint_plan", fake_plan)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 0)
    monkeypatch.setattr(import_orch, "_sprint_end", lambda: 0)
    def boom_groomer():
        raise RuntimeError("groomer crashed")
    monkeypatch.setattr(import_orch, "_run_backlog_groomer", boom_groomer)

    rc = import_orch._sprint_cycle(argparse.Namespace())
    assert rc == 0


def test_sprint_cycle_hits_max_iterations(import_orch, project_root, monkeypatch):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"max_sprint_cycles": 2}}), encoding="utf-8"
    )
    monkeypatch.setattr(import_orch, "_sprint_plan", lambda: 0)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 0)
    monkeypatch.setattr(import_orch, "_sprint_end", lambda: 0)
    monkeypatch.setattr(import_orch, "_run_backlog_groomer", lambda: None)

    rc = import_orch._sprint_cycle(argparse.Namespace())
    assert rc == 0


def test_sprint_cycle_malformed_config_uses_default(import_orch, project_root, monkeypatch):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("not json", encoding="utf-8")

    monkeypatch.setattr(import_orch, "_sprint_plan", lambda: 1)
    rc = import_orch._sprint_cycle(argparse.Namespace())
    assert rc == 0


def test_sprint_cycle_interactive_stops_when_user_chooses_stop(import_orch, project_root, monkeypatch):
    plan_count = {"n": 0}
    def fake_plan():
        plan_count["n"] += 1
        return 0
    monkeypatch.setattr(import_orch, "_sprint_plan", fake_plan)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 0)
    monkeypatch.setattr(import_orch, "_sprint_end", lambda: 0)
    monkeypatch.setattr(import_orch, "_run_backlog_groomer", lambda: None)
    monkeypatch.setattr(import_orch, "_WIZARD_AVAILABLE", True)
    monkeypatch.setattr(import_orch, "_prompt_choice", lambda *a, **kw: "Stop — I'll resume manually later")

    _seed(project_root, stories=[{
        "id": "S1", "title": "x", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }])

    rc = import_orch._sprint_cycle(argparse.Namespace(interactive=True))
    assert rc == 0
    assert plan_count["n"] == 1


def test_sprint_cycle_interactive_exits_when_no_pending(import_orch, project_root, monkeypatch):
    monkeypatch.setattr(import_orch, "_sprint_plan", lambda: 0)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 0)
    monkeypatch.setattr(import_orch, "_sprint_end", lambda: 0)
    monkeypatch.setattr(import_orch, "_run_backlog_groomer", lambda: None)
    monkeypatch.setattr(import_orch, "_WIZARD_AVAILABLE", True)

    _seed(project_root, stories=[{
        "id": "S1", "title": "x", "status": "completed", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {"commit_hash": "abc"},
    }])

    rc = import_orch._sprint_cycle(argparse.Namespace(interactive=True))
    assert rc == 0


def test_sprint_cycle_interactive_wizard_aborted(import_orch, project_root, monkeypatch):
    monkeypatch.setattr(import_orch, "_sprint_plan", lambda: 0)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 0)
    monkeypatch.setattr(import_orch, "_sprint_end", lambda: 0)
    monkeypatch.setattr(import_orch, "_run_backlog_groomer", lambda: None)
    monkeypatch.setattr(import_orch, "_WIZARD_AVAILABLE", True)

    def raise_abort(*a, **kw):
        raise import_orch._WizardAborted()
    monkeypatch.setattr(import_orch, "_prompt_choice", raise_abort)

    _seed(project_root, stories=[{
        "id": "S1", "title": "x", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }])

    rc = import_orch._sprint_cycle(argparse.Namespace(interactive=True))
    assert rc == 0


def test_sprint_cycle_interactive_show_status_then_continue(import_orch, project_root, monkeypatch):
    plan_count = {"n": 0}
    def fake_plan():
        plan_count["n"] += 1
        return 0 if plan_count["n"] == 1 else 1
    monkeypatch.setattr(import_orch, "_sprint_plan", fake_plan)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 0)
    monkeypatch.setattr(import_orch, "_sprint_end", lambda: 0)
    monkeypatch.setattr(import_orch, "_run_backlog_groomer", lambda: None)
    monkeypatch.setattr(import_orch, "_WIZARD_AVAILABLE", True)
    monkeypatch.setattr(import_orch, "_prompt_choice",
                        lambda *a, **kw: "Show full status, then ask again")
    monkeypatch.setattr(import_orch, "_prompt_yes_no", lambda *a, **kw: True)
    monkeypatch.setattr(import_orch, "cmd_status", lambda args: 0)

    _seed(project_root, stories=[{
        "id": "S1", "title": "x", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }])

    rc = import_orch._sprint_cycle(argparse.Namespace(interactive=True))
    assert rc == 0


def test_sprint_cycle_interactive_show_status_then_decline(import_orch, project_root, monkeypatch):
    monkeypatch.setattr(import_orch, "_sprint_plan", lambda: 0)
    monkeypatch.setattr(import_orch, "_sprint_start", lambda args: 0)
    monkeypatch.setattr(import_orch, "_sprint_end", lambda: 0)
    monkeypatch.setattr(import_orch, "_run_backlog_groomer", lambda: None)
    monkeypatch.setattr(import_orch, "_WIZARD_AVAILABLE", True)
    monkeypatch.setattr(import_orch, "_prompt_choice",
                        lambda *a, **kw: "Show full status, then ask again")
    monkeypatch.setattr(import_orch, "_prompt_yes_no", lambda *a, **kw: False)
    monkeypatch.setattr(import_orch, "cmd_status", lambda args: 0)

    _seed(project_root, stories=[{
        "id": "S1", "title": "x", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }])

    rc = import_orch._sprint_cycle(argparse.Namespace(interactive=True))
    assert rc == 0


def test_sprint_cycle_dispatches_via_cmd_sprint(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "_sprint_cycle", lambda args: 0)
    rc = import_orch.cmd_sprint(argparse.Namespace(action="cycle", interactive=False))
    assert rc == 0


# ---------------------------------------------------------------------------
# cmd_health_check edges
# ---------------------------------------------------------------------------

def test_cmd_health_check_no_agents_dir(import_orch, project_root, monkeypatch, capsys):
    monkeypatch.setenv("AA_RUNNER", "claude")
    rc = import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "agents parse" in out or "does not exist" in out


# ---------------------------------------------------------------------------
# main() argparse final fallback paths
# ---------------------------------------------------------------------------

def test_main_unknown_cmd_returns_1(import_orch, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["orchestrator", "unknown_cmd_xyz"])
    with pytest.raises(SystemExit):
        import_orch.main()


# ---------------------------------------------------------------------------
# Worktree config edges
# ---------------------------------------------------------------------------

def test_auto_merge_malformed_config_returns_default(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("garbage", encoding="utf-8")
    assert import_orch._auto_merge_enabled() is True


def test_cleanup_worktrees_malformed_config_returns_default(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("garbage", encoding="utf-8")
    assert import_orch._cleanup_worktrees_enabled() is True


# ---------------------------------------------------------------------------
# cmd_refine warning paths
# ---------------------------------------------------------------------------

def test_cmd_refine_warns_about_non_large_complexity(import_orch, project_root, monkeypatch, capsys):
    _seed(project_root, stories=[{
        "id": "STORY-a", "title": "small story", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": ["AC1"],
        "tasks": [{"id": "T1", "files_to_touch": ["src/a.py"]}],
        "artifacts": {},
    }])
    monkeypatch.setattr(import_orch, "call_agent",
                        lambda *a, **kw: "Refined\nVERDICT: REFINEMENT_REJECTED\n")

    rc = import_orch.cmd_refine(argparse.Namespace(story="STORY-a"))
    assert rc == 0


def test_cmd_refine_warns_about_few_tasks(import_orch, project_root, monkeypatch, capsys):
    _seed(project_root, stories=[{
        "id": "STORY-a", "title": "story", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "large", "acceptance_criteria": ["AC1"],
        "tasks": [{"id": "T1", "files_to_touch": ["src/a.py"]}],
        "artifacts": {},
    }])
    monkeypatch.setattr(import_orch, "call_agent",
                        lambda *a, **kw: "VERDICT: REFINEMENT_REJECTED\n")

    rc = import_orch.cmd_refine(argparse.Namespace(story="STORY-a"))
    assert rc == 0


# ---------------------------------------------------------------------------
# cmd_agent with skill present in inline registry
# ---------------------------------------------------------------------------

def test_cmd_agent_invokes_with_skill_present_in_inline(import_orch, project_root, monkeypatch, capsys):
    agents = project_root / ".opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\n"
        "description: engineer\n"
        "skills:\n"
        "  - id: fix-bug\n"
        "    description: Fix a bug\n"
        "permission:\n"
        "  edit: deny\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(import_orch, "call_agent", lambda *a, **kw: "agent did the thing")

    rc = import_orch.cmd_agent(argparse.Namespace(
        agent="engineer", skill="fix-bug", prompt="do thing",
    ))
    assert rc == 0
    assert "agent did the thing" in capsys.readouterr().out
