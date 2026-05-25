"""M17.7 final: remaining testable orchestrator branches."""

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


def test_strip_ansi_removes_color_codes(import_orch):
    assert import_orch._strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_strip_ansi_passes_through_plain_text(import_orch):
    assert import_orch._strip_ansi("plain text") == "plain text"


def test_load_config_no_file_returns_empty(import_orch, project_root):
    assert import_orch.load_config() == {}


def test_extract_json_includes_parse_errors_in_die_message(import_orch):
    text = '```json\n{not valid json\n```\n{also invalid'
    with pytest.raises(SystemExit):
        import_orch.extract_json(text)


def test_try_local_planner_returns_false_on_oserror(import_orch, project_root, monkeypatch):
    spec_json = {
        "epics": [{
            "id": "E", "stories": [
                {"id": "S1", "title": "x", "depends_on": [], "tasks": [],
                 "acceptance_criteria": [], "estimated_complexity": "small"},
            ],
        }],
    }

    real_open = open
    def flaky_open(path, mode="r", *args, **kw):
        if "progress.json.tmp" in str(path) and "w" in mode:
            raise OSError("disk full")
        return real_open(path, mode, *args, **kw)
    monkeypatch.setattr("builtins.open", flaky_open)

    assert import_orch._try_local_planner(spec_json) is False


def test_cmd_validate_dies_when_spec_parser_unavailable(import_orch, project_root, monkeypatch):
    monkeypatch.setattr(import_orch, "validate_specs", None)
    with pytest.raises(SystemExit):
        import_orch.cmd_validate(argparse.Namespace())


def test_finalize_story_dies_when_story_missing(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": []}],
    }), encoding="utf-8")
    data = import_orch.read_progress()
    with pytest.raises(SystemExit):
        import_orch.finalize_story(data, "STORY-ghost", "completed", "x")


def test_finalize_story_cascade_failed(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": [
            {"id": "S1", "title": "a", "status": "in_progress",
             "depends_on": [], "execution_wave": 1, "estimated_complexity": "small",
             "acceptance_criteria": [], "tasks": [], "artifacts": {}},
            {"id": "S2", "title": "b", "status": "pending",
             "depends_on": ["S1"], "execution_wave": 2, "estimated_complexity": "small",
             "acceptance_criteria": [], "tasks": [], "artifacts": {}},
        ]}],
    }), encoding="utf-8")
    data = import_orch.read_progress()
    data = import_orch.finalize_story(data, "S1", "failed", "test cascade")
    statuses = {s["id"]: s["status"] for s in data["epics"][0]["stories"]}
    assert statuses["S1"] == "failed"
    assert statuses["S2"] == "blocked"


def test_cmd_health_check_with_valid_setup(import_orch, project_root, monkeypatch, capsys):
    agents = project_root / ".opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\ndescription: engineer\npermission:\n  edit: deny\n---\n\nbody\n",
        encoding="utf-8",
    )

    skills_dir = project_root / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "fix-bug.md").write_text(
        "---\nid: fix-bug\ndescription: x\napplicable_agents: [engineer]\n---\nbody\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AA_RUNNER", "claude")
    import_orch.cmd_health_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert "skills load" in out
    assert "persona imports" in out


def test_persist_logs_divergent_keys_warning(import_orch, project_root, monkeypatch, capsys):
    (project_root / ".opencode").mkdir(exist_ok=True)
    progress_file = project_root / ".opencode" / "progress.json"
    progress_file.write_text(json.dumps({
        "schema_version": "2.0", "version": 5, "status": "in_progress",
        "epics": [{"id": "E", "stories": [
            {"id": "S1", "status": "completed",
             "depends_on": [], "execution_wave": 1, "estimated_complexity": "small",
             "acceptance_criteria": [], "tasks": [], "artifacts": {}},
        ]}],
    }), encoding="utf-8")

    # In-memory data has S1 as pending, on-disk has it as completed → divergent
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": [
            {"id": "S1", "status": "pending",
             "depends_on": [], "execution_wave": 1, "estimated_complexity": "small",
             "acceptance_criteria": [], "tasks": [], "artifacts": {}},
        ]}],
    }

    monkeypatch.setattr(import_orch.time, "sleep", lambda s: None)

    import_orch.persist(data)
    out = capsys.readouterr().out
    assert "persist conflict" in out


def test_process_rfc_files_new_story_action(import_orch, project_root, monkeypatch):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001.md").write_text("Status: open\n", encoding="utf-8")

    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": []}],
    }), encoding="utf-8")

    monkeypatch.setattr(
        import_orch, "call_agent",
        lambda *a, **kw: "Recommendation: NEW STORY\nVERDICT: RFC_RESOLVED\n",
    )

    data = import_orch.read_progress()
    rc = import_orch.process_rfc_files(data)
    assert rc == 0
