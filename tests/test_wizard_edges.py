"""M17.3: wizard.py edge-case coverage for 100%."""

from __future__ import annotations

from pathlib import Path

import pytest

from wizard import (
    PipelineState,
    WizardAborted,
    _has_open_rfcs,
    detect_state,
    prompt_choice,
    prompt_text,
)


def test_prompt_text_noninteractive_default_rejected_by_validator(monkeypatch):
    monkeypatch.setenv("NONINTERACTIVE", "1")
    with pytest.raises(WizardAborted):
        prompt_text(
            "Your name?",
            default="",
            validator=lambda v: None if v.strip() else "cannot be empty",
        )


def test_prompt_text_noninteractive_default_passes_validator(monkeypatch):
    monkeypatch.setenv("NONINTERACTIVE", "1")
    result = prompt_text("Name?", default="alice", validator=lambda v: None)
    assert result == "alice"


def test_prompt_choice_noninteractive_returns_default(monkeypatch):
    monkeypatch.setenv("NONINTERACTIVE", "1")
    result = prompt_choice("Pick:", options=["a", "b", "c"], default_index=2)
    assert result == "c"


def test_prompt_choice_handles_invalid_input_then_valid(monkeypatch):
    monkeypatch.delenv("NONINTERACTIVE", raising=False)
    answers = iter(["abc", "2"])
    monkeypatch.setattr("wizard._read_line", lambda prompt: next(answers))

    result = prompt_choice("Pick:", options=["a", "b", "c"], default_index=0)
    assert result == "b"


def test_has_open_rfcs_skips_unreadable_file(tmp_path, monkeypatch):
    rfc_dir = tmp_path / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    bad = rfc_dir / "0001-bad.md"
    bad.write_text("Status: open\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky_read(self, *a, **kw):
        if str(self).endswith("0001-bad.md"):
            raise OSError("disk error")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky_read)

    assert _has_open_rfcs(tmp_path) is False


def test_detect_state_fallback_when_only_docs_specs_no_epics_no_opencode(tmp_path):
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    report = detect_state(tmp_path)
    assert report.state == PipelineState.NOT_INITIALIZED
    assert "unknown project state" in report.summary
