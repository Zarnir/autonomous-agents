"""Unit tests for RFC pipeline (M9.1)."""

from __future__ import annotations

from pathlib import Path

from orchestrator import find_open_rfcs, parse_rfc_resolution


def _write_rfc(rfc_dir: Path, num: int, status: str, body: str = "") -> Path:
    path = rfc_dir / f"{num:04d}-issue.md"
    path.write_text(
        f"# RFC-{num:04d}: test issue\n\nStatus: {status}\nDetected: 2026-05-12\n\n{body}",
        encoding="utf-8",
    )
    return path


def test_find_open_rfcs_returns_only_open(project_root: Path):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    _write_rfc(rfc_dir, 1, "open")
    _write_rfc(rfc_dir, 2, "resolved")
    _write_rfc(rfc_dir, 3, "open")
    opens = find_open_rfcs()
    assert len(opens) == 2
    nums = sorted(int(p.stem.split("-")[0]) for p in opens)
    assert nums == [1, 3]


def test_find_open_rfcs_empty_when_no_dir(project_root: Path):
    assert find_open_rfcs() == []


def test_parse_rfc_resolution_extracts_verdict_resolved():
    out = "Some analysis here.\n\nRecommendation: REOPEN STORY-foo\n\nVERDICT: RFC_RESOLVED"
    result = parse_rfc_resolution(out)
    assert result["verdict"] == "RFC_RESOLVED"
    assert result["action"] == "REOPEN"
    assert result["target_story_id"] == "STORY-foo"


def test_parse_rfc_resolution_needs_human():
    out = "This is ambiguous.\n\nVERDICT: NEEDS_HUMAN"
    result = parse_rfc_resolution(out)
    assert result["verdict"] == "NEEDS_HUMAN"


def test_parse_rfc_resolution_edit_scope_action():
    out = "Recommendation: EDIT_SCOPE STORY-x\nVERDICT: RFC_RESOLVED"
    result = parse_rfc_resolution(out)
    assert result["action"] == "EDIT_SCOPE"
    assert result["target_story_id"] == "STORY-x"


def test_parse_rfc_resolution_none_action():
    out = "False positive.\nRecommendation: NONE\nVERDICT: RFC_RESOLVED"
    result = parse_rfc_resolution(out)
    assert result["action"] == "NONE"
    assert result["target_story_id"] is None


def test_parse_rfc_resolution_unknown_when_no_verdict():
    result = parse_rfc_resolution("just text, no verdict")
    assert result["verdict"] == "UNKNOWN"
