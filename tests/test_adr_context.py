"""Unit tests for ADR loader + context injection (M7.2)."""

from __future__ import annotations

from pathlib import Path

from orchestrator import load_recent_adrs, next_adr_number


def _write_adr(adr_dir: Path, num: int, title: str, status: str, decision: str) -> Path:
    path = adr_dir / f"{num:04d}-{title.lower().replace(' ', '-')}.md"
    path.write_text(
        f"# ADR-{num:04d}: {title}\n\n"
        f"Status: {status}\n"
        f"Date: 2026-05-12\n\n"
        f"## Context\nSome context here.\n\n"
        f"## Decision\n{decision}\n\n"
        f"## Consequences\nGood and bad things happen.\n",
        encoding="utf-8",
    )
    return path


def test_load_recent_adrs_empty_when_no_dir(project_root: Path):
    assert load_recent_adrs() == ""


def test_load_recent_adrs_returns_only_accepted(project_root: Path):
    adr_dir = project_root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    _write_adr(adr_dir, 1, "Use Postgres", "accepted", "We use Postgres.")
    _write_adr(adr_dir, 2, "Use Redis", "proposed", "We use Redis.")
    result = load_recent_adrs()
    assert "Use Postgres" in result
    assert "Use Redis" not in result


def test_load_recent_adrs_caps_at_n(project_root: Path):
    adr_dir = project_root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    for i in range(7):
        _write_adr(adr_dir, i + 1, f"Decision {i}", "accepted", f"We do thing {i}.")
    result = load_recent_adrs(max_entries=3)
    lines = [l for l in result.splitlines() if l.startswith("-")]
    assert len(lines) == 3


def test_load_recent_adrs_zero_entries_returns_empty(project_root: Path):
    adr_dir = project_root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    _write_adr(adr_dir, 1, "A", "accepted", "Some decision.")
    assert load_recent_adrs(max_entries=0) == ""


def test_next_adr_number_starts_at_1(project_root: Path):
    assert next_adr_number() == 1


def test_next_adr_number_increments(project_root: Path):
    adr_dir = project_root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    _write_adr(adr_dir, 3, "X", "accepted", "x")
    _write_adr(adr_dir, 1, "Y", "accepted", "y")
    assert next_adr_number() == 4


def test_load_recent_adrs_extracts_decision(project_root: Path):
    adr_dir = project_root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    _write_adr(adr_dir, 1, "Use Vite", "accepted", "We use Vite for the dev server.")
    result = load_recent_adrs()
    assert "Vite" in result
    assert "dev server" in result
