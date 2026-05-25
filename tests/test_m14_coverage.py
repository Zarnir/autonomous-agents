"""Coverage tests for high-value gaps identified by the M13/M14 audit (M14.6 + M14.7).

Targets:
- cmd_status: untested user-facing summary
- cmd_resume: missing-current-story edge case
- ValidationReport.render(): output format
- parse_agent_file: webfetch/websearch permissions, malformed skill termination
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


# ---------------------------------------------------------------------------
# M14.6: cmd_status
# ---------------------------------------------------------------------------

def test_cmd_status_prints_counts_and_next(import_orch, project_root, capsys):
    """cmd_status formats the per-status counts + current/next story IDs."""
    orch = import_orch
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0",
        "version": 1,
        "status": "in_progress",
        "current_story_id": "STORY-active",
        "epics": [{"id": "E", "stories": [
            {"id": "STORY-active", "status": "in_progress", "depends_on": [], "execution_wave": 1,
             "estimated_complexity": "small", "acceptance_criteria": [], "tasks": [], "artifacts": {}},
            {"id": "STORY-done", "status": "completed", "depends_on": [], "execution_wave": 1,
             "estimated_complexity": "small", "acceptance_criteria": [], "tasks": [], "artifacts": {"commit_hash": "abc"}},
            {"id": "STORY-pending", "status": "pending", "depends_on": [], "execution_wave": 1,
             "estimated_complexity": "small", "acceptance_criteria": [], "tasks": [], "artifacts": {}},
        ]}],
        "updated_at": "2026-05-17T00:00:00Z",
    }), encoding="utf-8")

    rc = orch.cmd_status(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Schema:" in out and "2.0" in out
    assert "Total:" in out and "3 stories" in out
    assert "completed" in out
    assert "in_progress" in out
    assert "pending" in out
    assert "Current:" in out and "STORY-active" in out
    assert "Updated:" in out


def test_cmd_status_shows_cost_when_tracking_present(import_orch, project_root, capsys):
    """cmd_status renders the cost summary when cost_tracking is populated."""
    orch = import_orch
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0",
        "version": 1,
        "status": "in_progress",
        "current_story_id": None,
        "epics": [{"id": "E", "stories": []}],
        "cost_tracking": {
            "total_usd": 1.2345,
            "total_input_tokens": 100000,
            "total_output_tokens": 50000,
            "calls": 12,
            "by_agent": {"make": 0.8, "check": 0.4},
        },
        "updated_at": "2026-05-17T00:00:00Z",
    }), encoding="utf-8")

    orch.cmd_status(argparse.Namespace())
    out = capsys.readouterr().out
    assert "Cost:" in out
    assert "$1.23" in out
    assert "make" in out
    assert "check" in out


def test_cmd_status_renders_gate_failures(import_orch, project_root, capsys):
    """When status=gate_failed, the gate_failures list is printed."""
    orch = import_orch
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "gate_failed",
        "gate_failures": ["clean_working_tree: 3 uncommitted files", "all_tests_pass: 2 failures"],
        "epics": [{"id": "E", "stories": []}],
        "current_story_id": None, "updated_at": "2026-05-17T00:00:00Z",
    }), encoding="utf-8")

    orch.cmd_status(argparse.Namespace())
    out = capsys.readouterr().out
    assert "Gate failures:" in out
    assert "clean_working_tree" in out


# ---------------------------------------------------------------------------
# M14.6: cmd_resume missing-current-story
# ---------------------------------------------------------------------------

def test_cmd_resume_handles_missing_current_story(import_orch, project_root, monkeypatch):
    """If current_story_id points to a story that no longer exists, resume must not crash."""
    orch = import_orch
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "current_story_id": "STORY-ghost",
        "epics": [{"id": "E", "stories": [
            {"id": "STORY-real", "status": "pending", "depends_on": [], "execution_wave": 1,
             "estimated_complexity": "small", "acceptance_criteria": [], "tasks": [], "artifacts": {}},
        ]}],
        "sprints": [],
        "updated_at": "2026-05-17T00:00:00Z",
    }), encoding="utf-8")

    monkeypatch.setattr(orch, "run_loop", lambda data, only_story=None, from_story=None: 0)

    rc = orch.cmd_resume(argparse.Namespace(retry_failed=False, retry_blocked=False, story=None))
    assert rc == 0


# ---------------------------------------------------------------------------
# M14.7: ValidationReport.render()
# ---------------------------------------------------------------------------

def test_validation_report_render_clean():
    """Empty report → renders OK."""
    from spec_parser import ValidationReport
    r = ValidationReport()
    out = r.render()
    assert "OK" in out


def test_validation_report_render_with_errors_and_warnings():
    """Render produces ERROR + WARN lines."""
    from spec_parser import ValidationReport
    r = ValidationReport(errors=["missing id", "duplicate STORY-x"], warnings=["AC1 is vague"])
    out = r.render()
    assert "ERROR: missing id" in out
    assert "ERROR: duplicate STORY-x" in out
    assert "WARN:" in out
    assert "AC1 is vague" in out


def test_validation_report_ok_property_reflects_errors():
    from spec_parser import ValidationReport
    assert ValidationReport().ok is True
    assert ValidationReport(errors=["x"]).ok is False
    assert ValidationReport(warnings=["x"]).ok is True


# ---------------------------------------------------------------------------
# M14.7: parse_agent_file edge cases
# ---------------------------------------------------------------------------

def test_parse_agent_file_webfetch_websearch_permissions(tmp_path):
    """webfetch/websearch permission parsing was uncovered."""
    from runners import parse_agent_file
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "fetch.md").write_text(
        """---
description: Fetches the web
permission:
  edit: deny
  write: deny
  webfetch: allow
  websearch: deny
  bash:
    "ls": allow
---

body
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("fetch", agents_dir=agents)
    assert agent.webfetch_allowed is True
    assert agent.websearch_allowed is False
    assert agent.edit_allowed is False
    assert agent.write_allowed is False


def test_parse_agent_file_skill_block_terminated_by_top_level_key(tmp_path):
    """A skill section followed by another top-level key resets parsing state."""
    from runners import parse_agent_file
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "weird.md").write_text(
        """---
description: An agent
skills:
  - id: ok-skill
    description: A skill
permission:
  edit: allow
  bash:
    "ls": allow
---

body
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("weird", agents_dir=agents)
    assert len(agent.skills) == 1
    assert agent.skills[0].id == "ok-skill"
    assert agent.edit_allowed is True
    assert "ls" in agent.bash_allow


def test_parse_agent_file_inline_imports_with_quoted_names(tmp_path):
    """Inline `imports: [a, "b", c]` should strip quotes."""
    from runners import parse_agent_file
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "q.md").write_text(
        """---
description: A
imports: [skill-a, "skill-b", 'skill-c']
permission:
  edit: deny
---

body
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("q", agents_dir=agents)
    assert set(agent.imports) == {"skill-a", "skill-b", "skill-c"}
