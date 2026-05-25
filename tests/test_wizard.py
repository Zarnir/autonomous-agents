"""Unit tests for lib/wizard.py (M12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wizard import (
    PipelineState,
    WizardAborted,
    detect_state,
    prompt_choice,
    prompt_text,
    prompt_yes_no,
)


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def test_prompt_yes_no_default_on_enter(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert prompt_yes_no("ok?", default=True) is True
    assert prompt_yes_no("ok?", default=False) is False


def test_prompt_yes_no_accepts_y_and_n(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert prompt_yes_no("ok?", default=False) is True
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert prompt_yes_no("ok?", default=True) is False


def test_prompt_yes_no_reprompts_on_invalid(monkeypatch, capsys):
    responses = iter(["maybe", "yes"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    result = prompt_yes_no("ok?", default=False)
    assert result is True
    out = capsys.readouterr().out
    assert "please answer y or n" in out


def test_prompt_yes_no_noninteractive(monkeypatch):
    monkeypatch.setenv("NONINTERACTIVE", "1")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("input() was called"))
    assert prompt_yes_no("ok?", default=True) is True
    assert prompt_yes_no("ok?", default=False) is False


def test_prompt_text_returns_user_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "hello world")
    assert prompt_text("name:") == "hello world"


def test_prompt_text_falls_back_to_default(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert prompt_text("name:", default="alice") == "alice"


def test_prompt_text_validator_reprompts(monkeypatch, capsys):
    responses = iter(["", "valid"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    result = prompt_text(
        "thing:",
        validator=lambda v: None if v.strip() else "must not be empty",
    )
    assert result == "valid"
    assert "must not be empty" in capsys.readouterr().out


def test_prompt_choice_picks_by_number(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "2")
    assert prompt_choice("pick:", ["a", "b", "c"], default_index=0) == "b"


def test_prompt_choice_default_on_enter(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert prompt_choice("pick:", ["a", "b", "c"], default_index=1) == "b"


def test_prompt_choice_reprompts_on_out_of_range(monkeypatch, capsys):
    responses = iter(["99", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    assert prompt_choice("pick:", ["a", "b", "c"]) == "b"
    assert "please enter a number between 1 and 3" in capsys.readouterr().out


def test_prompt_choice_empty_options_raises():
    with pytest.raises(ValueError):
        prompt_choice("x", [], default_index=0)


def test_prompt_aborts_on_eof(monkeypatch):
    def raise_eof(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", raise_eof)
    with pytest.raises(WizardAborted):
        prompt_yes_no("ok?", default=True)


def test_prompt_aborts_on_keyboard_interrupt(monkeypatch):
    def raise_kbd(_):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", raise_kbd)
    with pytest.raises(WizardAborted):
        prompt_text("name:")


# ---------------------------------------------------------------------------
# detect_state
# ---------------------------------------------------------------------------

def test_detect_state_not_initialized(project_root: Path):
    report = detect_state(project_root)
    assert report.state == PipelineState.NOT_INITIALIZED


def test_detect_state_bootstrapped_no_spec(project_root: Path):
    (project_root / ".opencode").mkdir()
    report = detect_state(project_root)
    assert report.state == PipelineState.BOOTSTRAPPED_NO_SPEC
    assert "discover" in report.command


def test_detect_state_spec_written_no_plan(project_root: Path):
    (project_root / ".opencode").mkdir()
    epics = project_root / "docs" / "specs" / "epics"
    epics.mkdir(parents=True)
    (epics / "01-test.md").write_text(
        "---\nid: EPIC-x\ntitle: x\n---\n", encoding="utf-8"
    )
    report = detect_state(project_root)
    assert report.state == PipelineState.SPEC_WRITTEN_NO_PLAN


def _write_progress(project_root: Path, payload: dict) -> Path:
    p = project_root / ".opencode"
    p.mkdir(exist_ok=True)
    f = p / "progress.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return f


def test_detect_state_plan_pending(project_root: Path):
    _write_progress(project_root, {
        "schema_version": "2.0",
        "version": 1,
        "status": "in_progress",
        "epics": [{"id": "E", "stories": [{"id": "S1", "status": "pending"}]}],
        "sprints": [],
    })
    report = detect_state(project_root)
    assert report.state == PipelineState.PLAN_PENDING
    assert "sprint plan" in report.command


def test_detect_state_sprint_planned(project_root: Path):
    _write_progress(project_root, {
        "status": "in_progress",
        "epics": [{"id": "E", "stories": [{"id": "S1", "status": "pending"}]}],
        "sprints": [{"number": 1, "status": "planned", "story_ids": ["S1"]}],
    })
    report = detect_state(project_root)
    assert report.state == PipelineState.SPRINT_PLANNED
    assert "sprint start" in report.command


def test_detect_state_sprint_in_progress(project_root: Path):
    _write_progress(project_root, {
        "status": "in_progress",
        "epics": [{"id": "E", "stories": [{"id": "S1", "status": "in_progress"}]}],
        "sprints": [{"number": 1, "status": "in_progress", "story_ids": ["S1"]}],
    })
    report = detect_state(project_root)
    assert report.state == PipelineState.SPRINT_IN_PROGRESS
    assert "resume" in report.command


def test_detect_state_sprint_completed_more_remains(project_root: Path):
    _write_progress(project_root, {
        "status": "in_progress",
        "epics": [{
            "id": "E",
            "stories": [
                {"id": "S1", "status": "completed"},
                {"id": "S2", "status": "pending"},
            ],
        }],
        "sprints": [{"number": 1, "status": "completed", "story_ids": ["S1"]}],
    })
    report = detect_state(project_root)
    assert report.state == PipelineState.SPRINT_COMPLETED_MORE_REMAINS
    assert "sprint plan" in report.command


def test_detect_state_all_complete(project_root: Path):
    _write_progress(project_root, {
        "status": "completed",
        "epics": [{"id": "E", "stories": [{"id": "S1", "status": "completed"}]}],
        "sprints": [{"number": 1, "status": "completed"}],
    })
    report = detect_state(project_root)
    assert report.state == PipelineState.ALL_COMPLETE
    assert report.command is None


def test_detect_state_gate_failed(project_root: Path):
    _write_progress(project_root, {
        "status": "gate_failed",
        "gate_failures": ["clean_working_tree: uncommitted files"],
        "epics": [{"id": "E", "stories": []}],
    })
    report = detect_state(project_root)
    assert report.state == PipelineState.GATE_FAILED


def test_detect_state_budget_exceeded(project_root: Path):
    _write_progress(project_root, {
        "status": "budget_exceeded",
        "epics": [{"id": "E", "stories": [{"id": "S1", "status": "in_progress"}]}],
    })
    report = detect_state(project_root)
    assert report.state == PipelineState.BUDGET_EXCEEDED
    assert "resume" in report.command


def test_detect_state_open_rfcs_takes_priority(project_root: Path):
    """Open RFC blocks pipeline regardless of other state."""
    _write_progress(project_root, {
        "status": "in_progress",
        "epics": [{"id": "E", "stories": [{"id": "S1", "status": "pending"}]}],
        "sprints": [{"number": 1, "status": "planned", "story_ids": ["S1"]}],
    })
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-issue.md").write_text(
        "# RFC-0001\n\nStatus: open\n\n## Detail\nSomething\n",
        encoding="utf-8",
    )
    report = detect_state(project_root)
    assert report.state == PipelineState.OPEN_RFCS
    assert report.command == "aa-orchestrator rfc"


def test_detect_state_open_rfc_resolved_does_not_trigger(project_root: Path):
    """Closed RFCs don't block."""
    _write_progress(project_root, {
        "status": "in_progress",
        "epics": [{"id": "E", "stories": [{"id": "S1", "status": "pending"}]}],
        "sprints": [],
    })
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-issue.md").write_text(
        "# RFC-0001\n\nStatus: resolved\n",
        encoding="utf-8",
    )
    report = detect_state(project_root)
    assert report.state != PipelineState.OPEN_RFCS


def test_detect_state_corrupt_progress_returns_spec_invalid(project_root: Path):
    p = project_root / ".opencode"
    p.mkdir()
    (p / "progress.json").write_text("{ not json", encoding="utf-8")
    report = detect_state(project_root)
    assert report.state == PipelineState.SPEC_INVALID
    assert "--force" in report.command
