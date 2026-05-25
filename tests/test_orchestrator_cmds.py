"""M15.3: Coverage tests for cmd_adr, cmd_refine, cmd_rfc, cmd_agent, cmd_develop, cmd_validate."""

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


def _seed_progress(project_root: Path, stories=None, status="in_progress"):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": status,
        "epics": [{"id": "EPIC-x", "stories": stories or []}],
        "sprints": [],
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# cmd_adr
# ---------------------------------------------------------------------------

def test_cmd_adr_writes_adr_when_architect_succeeds(import_orch, project_root, monkeypatch):
    orch = import_orch

    def fake_call(name, prompt, **kw):
        adr_dir = project_root / "docs" / "adr"
        adr_dir.mkdir(parents=True, exist_ok=True)
        (adr_dir / "0001-test-question.md").write_text(
            "# ADR-0001\n\nStatus: proposed\n\nVERDICT: ADR_PROPOSED\n",
            encoding="utf-8",
        )
        return "VERDICT: ADR_PROPOSED\n"

    monkeypatch.setattr(orch, "call_agent", fake_call)
    rc = orch.cmd_adr(argparse.Namespace(question="test question", story=None))
    assert rc == 0
    assert (project_root / "docs" / "adr" / "0001-test-question.md").exists()


def test_cmd_adr_dies_when_architect_emits_no_verdict(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "I forgot the verdict line.")
    with pytest.raises(SystemExit):
        orch.cmd_adr(argparse.Namespace(question="some question", story=None))


def test_cmd_adr_dies_when_architect_fails(import_orch, project_root, monkeypatch):
    orch = import_orch

    def boom(*a, **kw):
        raise orch.AgentError("architect", "rate limited")
    monkeypatch.setattr(orch, "call_agent", boom)
    with pytest.raises(SystemExit):
        orch.cmd_adr(argparse.Namespace(question="another question", story=None))


def test_cmd_adr_dies_when_verdict_but_no_file_written(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "VERDICT: ADR_PROPOSED\n")
    with pytest.raises(SystemExit):
        orch.cmd_adr(argparse.Namespace(question="ghost adr", story=None))


# ---------------------------------------------------------------------------
# cmd_refine
# ---------------------------------------------------------------------------

def test_cmd_refine_dies_when_story_not_found(import_orch, project_root):
    _seed_progress(project_root, stories=[])
    with pytest.raises(SystemExit):
        import_orch.cmd_refine(argparse.Namespace(story="STORY-ghost"))


def test_cmd_refine_returns_0_when_architect_rejects(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed_progress(project_root, stories=[{
        "id": "STORY-a", "title": "Big", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "large", "acceptance_criteria": ["AC1"],
        "tasks": [{"id": "T1", "files_to_touch": ["src/a.py"]}],
        "artifacts": {},
    }])
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "Cannot meaningfully split.\nVERDICT: REFINEMENT_REJECTED\n")
    rc = orch.cmd_refine(argparse.Namespace(story="STORY-a"))
    assert rc == 0


def test_cmd_refine_dies_when_no_verdict_emitted(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed_progress(project_root, stories=[{
        "id": "STORY-a", "title": "Big", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "large", "acceptance_criteria": ["AC1"],
        "tasks": [], "artifacts": {},
    }])
    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "Some text without verdict.")
    with pytest.raises(SystemExit):
        orch.cmd_refine(argparse.Namespace(story="STORY-a"))


def test_cmd_refine_dies_when_architect_fails(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed_progress(project_root, stories=[{
        "id": "STORY-a", "title": "Big", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "large", "acceptance_criteria": ["AC1"],
        "tasks": [], "artifacts": {},
    }])

    def boom(*a, **kw):
        raise orch.AgentError("architect", "timeout")
    monkeypatch.setattr(orch, "call_agent", boom)
    with pytest.raises(SystemExit):
        orch.cmd_refine(argparse.Namespace(story="STORY-a"))


# ---------------------------------------------------------------------------
# cmd_rfc
# ---------------------------------------------------------------------------

def test_cmd_rfc_no_open_rfcs_returns_0(import_orch, project_root):
    _seed_progress(project_root)
    rc = import_orch.cmd_rfc(argparse.Namespace())
    assert rc == 0


def test_cmd_rfc_resolved_returns_0(import_orch, project_root, monkeypatch):
    orch = import_orch
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-foo.md").write_text(
        "# RFC-0001\n\nStatus: open\n\n## Detail\nx\n", encoding="utf-8"
    )
    _seed_progress(project_root)

    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "**Recommendation:** NONE\nVERDICT: RFC_RESOLVED\n")
    rc = orch.cmd_rfc(argparse.Namespace())
    assert rc == 0


def test_cmd_rfc_needs_human_returns_6(import_orch, project_root, monkeypatch):
    orch = import_orch
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0002-bar.md").write_text(
        "# RFC-0002\n\nStatus: open\n\n## Detail\nx\n", encoding="utf-8"
    )
    _seed_progress(project_root)

    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "I cannot decide.\nVERDICT: NEEDS_HUMAN\n")
    rc = orch.cmd_rfc(argparse.Namespace())
    assert rc == orch.EXIT_RFC_NEEDS_HUMAN


# ---------------------------------------------------------------------------
# cmd_agent
# ---------------------------------------------------------------------------

def _seed_minimal_agent(project_root: Path, name="engineer"):
    agents = project_root / ".opencode" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{name}.md").write_text(
        "---\n"
        "description: A test agent\n"
        "permission:\n"
        "  edit: deny\n"
        "  write: deny\n"
        "  bash:\n"
        '    "ls": allow\n'
        "---\n\n"
        f"Body of @{name}.\n",
        encoding="utf-8",
    )


def test_cmd_agent_dies_when_agent_not_found(import_orch, project_root):
    with pytest.raises(SystemExit):
        import_orch.cmd_agent(argparse.Namespace(
            agent="nonexistent_agent_xyz", skill=None, prompt="hi"
        ))


def test_cmd_agent_invokes_call_agent_without_skill(import_orch, project_root, monkeypatch, capsys):
    orch = import_orch
    _seed_minimal_agent(project_root, "engineer")

    captured = {}
    def fake_call(name, prompt, **kw):
        captured["name"] = name
        captured["skill"] = kw.get("skill")
        return "agent output here"

    monkeypatch.setattr(orch, "call_agent", fake_call)

    rc = orch.cmd_agent(argparse.Namespace(agent="engineer", skill=None, prompt="do thing"))
    assert rc == 0
    assert captured["name"] == "engineer"
    assert captured["skill"] is None
    assert "agent output here" in capsys.readouterr().out


def test_cmd_agent_dies_when_skill_not_imported(import_orch, project_root):
    _seed_minimal_agent(project_root, "engineer")
    with pytest.raises(SystemExit):
        import_orch.cmd_agent(argparse.Namespace(
            agent="engineer", skill="unknown-skill-zzz", prompt="hi"
        ))


def test_cmd_agent_returns_1_when_call_agent_raises(import_orch, project_root, monkeypatch):
    orch = import_orch
    _seed_minimal_agent(project_root, "engineer")

    def boom(*a, **kw):
        raise orch.AgentError("engineer", "model not available")
    monkeypatch.setattr(orch, "call_agent", boom)

    rc = orch.cmd_agent(argparse.Namespace(agent="engineer", skill=None, prompt="hi"))
    assert rc == 1


# ---------------------------------------------------------------------------
# cmd_develop
# ---------------------------------------------------------------------------

def test_cmd_develop_returns_0_when_already_completed(import_orch, project_root, capsys):
    _seed_progress(project_root, status="completed")
    args = argparse.Namespace(
        force=False, dry_run=False, spec=None, story=None, from_story=None,
        spec_llm_fallback=False,
    )
    rc = import_orch.cmd_develop(args)
    assert rc == 0
    assert "already complete" in capsys.readouterr().out


def test_cmd_develop_returns_1_when_in_progress_without_force(import_orch, project_root, capsys):
    _seed_progress(project_root, status="in_progress")
    args = argparse.Namespace(
        force=False, dry_run=False, spec=None, story=None, from_story=None,
        spec_llm_fallback=False,
    )
    rc = import_orch.cmd_develop(args)
    assert rc == 1
    assert "resume" in capsys.readouterr().out.lower()


def test_cmd_develop_dry_run_skips_execution(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "phase_spec_and_plan",
                        lambda spec=None, use_llm_spec=False: {"schema_version": "2.0", "epics": []})
    ran = {"n": 0}

    def fake_loop(*a, **kw):
        ran["n"] += 1
        return 0
    monkeypatch.setattr(orch, "run_loop", fake_loop)

    rc = orch.cmd_develop(argparse.Namespace(
        force=False, dry_run=True, spec=None, story=None, from_story=None,
        spec_llm_fallback=False,
    ))
    assert rc == 0
    assert ran["n"] == 0


def test_cmd_develop_force_invokes_pipeline(import_orch, project_root, monkeypatch):
    """--force on an in-progress plan should re-run via phase_spec_and_plan + run_loop."""
    orch = import_orch
    _seed_progress(project_root, status="in_progress")

    monkeypatch.setattr(orch, "phase_spec_and_plan",
                        lambda spec=None, use_llm_spec=False: {"schema_version": "2.0", "epics": []})
    ran = {"n": 0}

    def fake_loop(*a, **kw):
        ran["n"] += 1
        return 0
    monkeypatch.setattr(orch, "run_loop", fake_loop)

    rc = orch.cmd_develop(argparse.Namespace(
        force=True, dry_run=False, spec=None, story=None, from_story=None,
        spec_llm_fallback=False,
    ))
    assert rc == 0
    assert ran["n"] == 1


# ---------------------------------------------------------------------------
# cmd_validate
# ---------------------------------------------------------------------------

def test_cmd_validate_returns_0_when_clean(import_orch, project_root, monkeypatch):
    orch = import_orch
    from spec_parser import ValidationReport
    monkeypatch.setattr(orch, "validate_specs", lambda root: ValidationReport())
    rc = orch.cmd_validate(argparse.Namespace())
    assert rc == 0


def test_cmd_validate_returns_1_when_errors_present(import_orch, project_root, monkeypatch, capsys):
    orch = import_orch
    from spec_parser import ValidationReport
    monkeypatch.setattr(orch, "validate_specs",
                        lambda root: ValidationReport(errors=["bad spec"]))
    rc = orch.cmd_validate(argparse.Namespace())
    assert rc == 1
    assert "bad spec" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# parse_rfc_resolution (additional coverage)
# ---------------------------------------------------------------------------

def test_parse_rfc_resolution_escalate_action(import_orch):
    text = "Recommendation: ESCALATE — too risky\nVERDICT: NEEDS_HUMAN\n"
    parsed = import_orch.parse_rfc_resolution(text)
    assert parsed["action"] == "ESCALATE"
    assert parsed["verdict"] == "NEEDS_HUMAN"


def test_parse_rfc_resolution_reopen_with_target(import_orch):
    text = "Recommendation: REOPEN STORY-foo\nVERDICT: RFC_RESOLVED\n"
    parsed = import_orch.parse_rfc_resolution(text)
    assert parsed["action"] == "REOPEN"
    assert parsed["target_story_id"] == "STORY-foo"


# ---------------------------------------------------------------------------
# next_adr_number edge cases
# ---------------------------------------------------------------------------

def test_next_adr_number_skips_malformed_filenames(import_orch, project_root):
    orch = import_orch
    adr_dir = project_root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-real.md").write_text("# ADR-1\n")
    (adr_dir / "notes.md").write_text("not an ADR\n")
    (adr_dir / "0003-another.md").write_text("# ADR-3\n")
    assert orch.next_adr_number() == 4


def test_next_adr_number_when_dir_missing(import_orch, project_root):
    assert import_orch.next_adr_number() == 1
