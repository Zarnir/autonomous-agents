"""Unit tests for the deterministic watcher (M8.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator import (
    _next_rfc_number,
    detect_watcher_signals,
    run_watcher,
    write_rfc_stub,
)


def _story(sid: str, status: str = "pending", depends_on=None) -> dict:
    return {
        "id": sid,
        "title": sid,
        "status": status,
        "depends_on": depends_on or [],
        "execution_wave": 1,
        "acceptance_criteria": [],
        "tasks": [],
        "artifacts": {},
    }


def _data(stories: list[dict], execution_log=None) -> dict:
    return {
        "schema_version": "2.0",
        "version": 1,
        "epics": [{"id": "EPIC-x", "title": "x", "stories": stories}],
        "execution_log": execution_log or [],
    }


def _old_ts(seconds_ago: int) -> str:
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_detect_no_signals_when_pipeline_healthy(project_root: Path):
    data = _data([_story("STORY-a", status="completed")])
    assert detect_watcher_signals(data) == []


def test_detect_stalled_story_when_in_progress_past_threshold(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"watcher_stall_threshold_sec": 10}}', encoding="utf-8"
    )
    data = _data(
        [_story("STORY-slow", status="in_progress")],
        execution_log=[{"ts": _old_ts(100), "msg": "start story=STORY-slow"}],
    )
    signals = detect_watcher_signals(data)
    assert any(s["type"] == "stalled_story" and s["story_id"] == "STORY-slow" for s in signals)


def test_detect_no_stall_when_within_threshold(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"watcher_stall_threshold_sec": 3600}}', encoding="utf-8"
    )
    data = _data(
        [_story("STORY-fast", status="in_progress")],
        execution_log=[{"ts": _old_ts(60), "msg": "start story=STORY-fast"}],
    )
    signals = detect_watcher_signals(data)
    assert not any(s["type"] == "stalled_story" for s in signals)


def test_detect_cascade_when_too_many_blocked(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"watcher_max_blocked": 2}}', encoding="utf-8"
    )
    data = _data([
        _story("STORY-a", status="blocked"),
        _story("STORY-b", status="blocked"),
        _story("STORY-c", status="blocked"),
        _story("STORY-d", status="blocked"),
    ])
    signals = detect_watcher_signals(data)
    cascade = [s for s in signals if s["type"] == "cascade"]
    assert len(cascade) == 1
    assert "4 stories blocked" in cascade[0]["detail"]


def test_detect_repeated_retries(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"watcher_max_retries": 2}}', encoding="utf-8"
    )
    data = _data(
        [_story("STORY-flaky")],
        execution_log=[
            {"ts": _old_ts(300), "msg": "retry story=STORY-flaky attempt=1"},
            {"ts": _old_ts(200), "msg": "retry story=STORY-flaky attempt=2"},
            {"ts": _old_ts(100), "msg": "retry story=STORY-flaky attempt=3"},
        ],
    )
    signals = detect_watcher_signals(data)
    retries = [s for s in signals if s["type"] == "repeated_retries"]
    assert len(retries) == 1
    assert retries[0]["story_id"] == "STORY-flaky"


def test_watcher_disabled_returns_no_signals(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"watcher_enabled": false, "watcher_max_blocked": 0}}',
        encoding="utf-8",
    )
    data = _data([_story("STORY-x", status="blocked"), _story("STORY-y", status="blocked")])
    assert detect_watcher_signals(data) == []


def test_next_rfc_number_starts_at_1(project_root: Path):
    assert _next_rfc_number() == 1


def test_next_rfc_number_increments(project_root: Path):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-issue.md").write_text("x", encoding="utf-8")
    (rfc_dir / "0003-another.md").write_text("x", encoding="utf-8")
    assert _next_rfc_number() == 4


def test_write_rfc_stub_creates_numbered_file(project_root: Path):
    signal = {"type": "stalled_story", "story_id": "STORY-x", "detail": "in_progress for 9999s"}
    path = write_rfc_stub(signal)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "stalled_story" in content
    assert "STORY-x" in content
    assert "Status: open" in content


def test_run_watcher_writes_stubs_for_each_signal(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"watcher_max_blocked": 1}}', encoding="utf-8"
    )
    data = _data([
        _story("STORY-a", status="blocked"),
        _story("STORY-b", status="blocked"),
    ])
    written = run_watcher(data)
    assert written == 1
    rfcs = list((project_root / "docs" / "rfc").glob("*.md"))
    assert len(rfcs) == 1


def test_run_watcher_skips_duplicate_open_signals(project_root: Path):
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"watcher_max_blocked": 1}}', encoding="utf-8"
    )
    data = _data([
        _story("STORY-a", status="blocked"),
        _story("STORY-b", status="blocked"),
    ])
    run_watcher(data)
    written = run_watcher(data)
    assert written == 0
