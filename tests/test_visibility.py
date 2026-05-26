"""M24 — visibility tests for slow / blocked / stuck stories.

Covers:
  - `_agent_heartbeat` context manager: ticks, final-duration on success
    AND exception, EventBus fan-out (M21 integration), graduated warnings
    at 5/10/20-min thresholds.
  - Per-story timing fields: `started_at` set by `run_story`, `completed_at`
    + `failure_reason` set by `finalize_story`, cascade-fail dependents
    get `failure_reason` referencing the upstream.
  - Extended `cmd_status` output: durations on completed, live elapsed on
    in_progress, per-story blocked/failed reasons.
"""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator
    # Don't leak _EVENT_BUS across tests (M21)
    orchestrator._EVENT_BUS = None


# ---------------------------------------------------------------------------
# Part A — _agent_heartbeat context manager
# ---------------------------------------------------------------------------

def test_heartbeat_emits_ticks_at_interval(import_orch, monkeypatch):
    """At 0.1s interval, a 0.4s sleep produces at least 3 tick lines."""
    orch = import_orch
    logs: list[str] = []
    monkeypatch.setattr(orch, "log", lambda msg: logs.append(str(msg)))

    with orch._agent_heartbeat("check", timeout=600, interval=0.1):
        time.sleep(0.4)

    ticks = [l for l in logs if "⏱" in l]
    assert len(ticks) >= 3, f"expected ≥3 ticks at 0.1s interval, got {len(ticks)}: {logs}"
    assert all("@check" in t for t in ticks)


def test_heartbeat_emits_final_duration_on_normal_exit(import_orch, monkeypatch):
    """Even with a long interval (no ticks fire), the final-duration line must land."""
    orch = import_orch
    logs: list[str] = []
    monkeypatch.setattr(orch, "log", lambda msg: logs.append(str(msg)))

    with orch._agent_heartbeat("simplify", timeout=600, interval=10):
        pass  # immediate exit; no tick should fire

    finals = [l for l in logs if "⏲" in l and "finished" in l]
    assert len(finals) == 1, f"expected one final log, got {finals}"
    assert "@simplify" in finals[0]


def test_heartbeat_emits_final_duration_even_on_exception(import_orch, monkeypatch):
    """Exception in the wrapped block must NOT skip the final duration log."""
    orch = import_orch
    logs: list[str] = []
    monkeypatch.setattr(orch, "log", lambda msg: logs.append(str(msg)))

    class CustomError(Exception):
        pass

    with pytest.raises(CustomError):
        with orch._agent_heartbeat("make", timeout=900, interval=10):
            raise CustomError("simulated agent failure")

    finals = [l for l in logs if "⏲" in l]
    assert len(finals) == 1, f"final duration must still log on exception, got {finals}"
    assert "@make" in finals[0]


def test_heartbeat_emits_event_bus_notifications(import_orch, monkeypatch):
    """When an EventBus is registered (M21), tick events also fan out."""
    from event_stream import EventBus

    orch = import_orch
    monkeypatch.setattr(orch, "log", lambda msg: None)  # silence stdout

    bus = EventBus()
    _sid, q = bus.subscribe()
    orch._EVENT_BUS = bus

    with orch._agent_heartbeat("check", timeout=600, interval=0.1):
        time.sleep(0.3)

    items: list[dict] = []
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            items.append(q.get(timeout=0.05))
        except queue.Empty:
            if items:
                break

    heartbeats = [it for it in items if it.get("method") == "event/agent_heartbeat"]
    assert len(heartbeats) >= 2, f"expected ≥2 heartbeat events, got {len(heartbeats)}: {items}"
    p = heartbeats[0]["params"]
    assert p["agent"] == "check"
    assert "elapsed_sec" in p
    assert "timeout" in p


# ---------------------------------------------------------------------------
# Part B — per-story timing fields
# ---------------------------------------------------------------------------

def _seed_progress(project_root: Path, stories: list[dict]) -> None:
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0",
        "version": 1,
        "status": "in_progress",
        "epics": [{"id": "E1", "stories": stories}],
        "sprints": [],
        "current_sprint": 0,
        "completed_stories": [s["id"] for s in stories if s["status"] == "completed"],
    }), encoding="utf-8")


def _story(sid: str, status: str = "pending", *, depends_on=None):
    return {
        "id": sid,
        "title": f"Story {sid}",
        "status": status,
        "depends_on": depends_on or [],
        "execution_wave": 1,
        "estimated_complexity": "small",
        "acceptance_criteria": [],
        "tasks": [],
        "artifacts": {},
    }


def test_finalize_story_records_completed_at_and_failure_reason(
    import_orch, project_root, monkeypatch
):
    """finalize_story sets `completed_at` on every terminal status and
    stores `failure_reason` for failed/blocked outcomes."""
    orch = import_orch
    _seed_progress(project_root, [_story("S1", "in_progress")])

    data = orch.read_progress()
    orch.finalize_story(data, "S1", "failed", "agent_error:make:timeout after 900s")

    final = orch.read_progress()
    s1 = final["epics"][0]["stories"][0]
    assert s1["status"] == "failed"
    assert s1.get("completed_at"), "completed_at must be set on terminal status"
    assert s1.get("failure_reason") == "agent_error:make:timeout after 900s"


def test_finalize_story_records_completed_at_for_completed_status(
    import_orch, project_root
):
    orch = import_orch
    _seed_progress(project_root, [_story("S1", "in_progress")])
    data = orch.read_progress()
    orch.finalize_story(data, "S1", "completed", None)
    s1 = orch.read_progress()["epics"][0]["stories"][0]
    assert s1.get("completed_at")
    # Successful stories don't get failure_reason
    assert not s1.get("failure_reason")


def test_cascade_fail_writes_failure_reason_on_dependents(
    import_orch, project_root
):
    """When upstream fails, downstream blocked dependents must have
    failure_reason referencing the upstream."""
    orch = import_orch
    _seed_progress(project_root, [
        _story("UP", "in_progress"),
        _story("DOWN1", "pending", depends_on=["UP"]),
        _story("DOWN2", "pending", depends_on=["DOWN1"]),  # transitive
    ])
    data = orch.read_progress()
    orch.finalize_story(data, "UP", "failed", "agent_error:make:x")

    final = orch.read_progress()
    stories = {s["id"]: s for s in final["epics"][0]["stories"]}
    assert stories["DOWN1"]["status"] == "blocked"
    assert "cascade" in (stories["DOWN1"].get("failure_reason") or "").lower()
    assert "UP" in (stories["DOWN1"].get("failure_reason") or "")
    # Transitive dependent should also be blocked + tagged
    assert stories["DOWN2"]["status"] == "blocked"
    assert "cascade" in (stories["DOWN2"].get("failure_reason") or "").lower()


# ---------------------------------------------------------------------------
# Part C — extended cmd_status output
# ---------------------------------------------------------------------------

def test_cmd_status_shows_duration_for_completed_story(
    import_orch, project_root, capsys
):
    """A completed story with started_at + completed_at should show '4m32s'-style duration."""
    import argparse
    orch = import_orch
    s1 = _story("S1", "completed")
    s1["started_at"] = "2026-05-26T10:00:00Z"
    s1["completed_at"] = "2026-05-26T10:04:32Z"  # 4m32s
    _seed_progress(project_root, [s1])

    orch.cmd_status(argparse.Namespace())
    out = capsys.readouterr().out

    assert "4m32s" in out or "4m 32s" in out, (
        f"expected 4m32s duration in status output:\n{out}"
    )


def test_cmd_status_shows_live_elapsed_for_in_progress(
    import_orch, project_root, capsys
):
    """An in_progress story should show live elapsed time."""
    import argparse
    from datetime import datetime, timezone, timedelta
    orch = import_orch

    eight_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s1 = _story("S1", "in_progress")
    s1["started_at"] = eight_min_ago
    _seed_progress(project_root, [s1])

    orch.cmd_status(argparse.Namespace())
    out = capsys.readouterr().out

    assert "S1" in out
    assert ("7m" in out or "8m" in out or "9m" in out) and "elapsed" in out.lower(), (
        f"expected elapsed line for in_progress S1:\n{out}"
    )


def test_cmd_status_shows_blocked_reason_per_story(
    import_orch, project_root, capsys
):
    """Each blocked/failed story should display its failure_reason."""
    import argparse
    orch = import_orch
    s1 = _story("S1", "blocked")
    s1["failure_reason"] = "cascade from STORY-UP (failed)"
    s2 = _story("S2", "failed")
    s2["failure_reason"] = "agent_error:make:timeout after 900s"
    _seed_progress(project_root, [s1, s2])

    orch.cmd_status(argparse.Namespace())
    out = capsys.readouterr().out

    assert "cascade from STORY-UP" in out
    assert "agent_error:make:timeout" in out


# ---------------------------------------------------------------------------
# Coverage closers — _format_duration edge cases + malformed timestamps
# ---------------------------------------------------------------------------

def test_format_duration_seconds_only(import_orch):
    """Durations < 60s render as 'Ns' (no minute component)."""
    assert import_orch._format_duration(42.0) == "42s"
    assert import_orch._format_duration(0.0) == "0s"
    assert import_orch._format_duration(59.9) == "59s"


def test_format_duration_hours_and_minutes(import_orch):
    """Durations >= 3600s render as 'NhMMm' (hour + minute components)."""
    # 1h12m = 4320s
    assert import_orch._format_duration(4320.0) == "1h12m"
    # 3h05m = 11100s — exercises the zero-padded minute
    assert import_orch._format_duration(11100.0) == "3h05m"


def test_story_duration_sec_returns_none_for_malformed_completed_at(import_orch):
    """If `completed_at` is present but malformed, _story_duration_sec returns None
    rather than crashing or returning a bogus number."""
    story = {
        "started_at": "2026-05-26T10:00:00Z",
        "completed_at": "not-a-real-iso-timestamp",
    }
    assert import_orch._story_duration_sec(story) is None


# ---------------------------------------------------------------------------
# Part D — graduated 5/10/20-min warnings
# ---------------------------------------------------------------------------

def test_graduated_warnings_fire_at_thresholds(import_orch, monkeypatch):
    """At simulated 5/10/20 min, the heartbeat emits ⚠ / ⚠⚠ / ⚠⚠⚠ once each."""
    orch = import_orch
    logs: list[str] = []
    monkeypatch.setattr(orch, "log", lambda msg: logs.append(str(msg)))

    # Mock the elapsed-time clock via monkeypatching the module-level reference
    # used by _agent_heartbeat. The heartbeat will read this stream of values.
    real_monotonic = time.monotonic
    fake_values = iter([
        0.0,        # start time captured
        301.0,      # tick 1 — past 5 min
        601.0,      # tick 2 — past 10 min
        1201.0,     # tick 3 — past 20 min
        1202.0,     # final exit
    ])
    def fake_monotonic():
        try:
            return next(fake_values)
        except StopIteration:
            return real_monotonic()

    monkeypatch.setattr(orch.time, "monotonic", fake_monotonic)

    with orch._agent_heartbeat("make", timeout=1800, interval=0.01):
        # Real-time sleep to allow at least 3 tick iterations
        time.sleep(0.2)

    five_min = [l for l in logs if "⚠" in l and "⚠⚠" not in l]
    ten_min = [l for l in logs if "⚠⚠" in l and "⚠⚠⚠" not in l]
    twenty_min = [l for l in logs if "⚠⚠⚠" in l]
    assert len(five_min) == 1, f"expected exactly one 5-min warning, got {five_min}"
    assert len(ten_min) == 1, f"expected exactly one 10-min warning, got {ten_min}"
    assert len(twenty_min) == 1, f"expected exactly one 20-min warning, got {twenty_min}"
