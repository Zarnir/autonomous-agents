"""M20 — completion-log + exit-code tests for run_loop.

Before M20:
    run_loop logged "✓ All stories complete." whenever next_eligible_story()
    returned None AND no story was "pending". It did NOT distinguish
    completed / failed / blocked. The user hit the trap: 30 stories all
    failed at @check (PTY bug), and the orchestrator's only feedback was
    the falsely-positive "complete" line.

After M20:
    The log reports a real status histogram (completed / failed / blocked),
    prefixed with ✓ for the all-completed path or ⚠ for any failures.
    Exit code is 0 only when every story completed; 2 otherwise.
    data["status"] persists as "completed" or "failed" accordingly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _story(sid: str, status: str, *, depends_on=None, with_hash: bool = False):
    return {
        "id": sid,
        "title": f"Story {sid}",
        "status": status,
        "depends_on": depends_on or [],
        "execution_wave": 1,
        "estimated_complexity": "small",
        "acceptance_criteria": [],
        "tasks": [],
        "artifacts": ({"commit_hash": f"hash-{sid}"} if with_hash else {}),
    }


def _seed(project_root: Path, stories: list[dict]) -> None:
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0",
        "version": 1,
        "status": "in_progress",
        "epics": [{"id": "E1", "stories": stories}],
        "sprints": [],
        "current_sprint": 0,
        "completed_stories": [s["id"] for s in stories if s["status"] == "completed"],
    }
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _capture_log(monkeypatch, orch) -> list[str]:
    """Return a list that captures every line passed to `orch.log()`."""
    lines: list[str] = []
    monkeypatch.setattr(orch, "log", lambda msg: lines.append(str(msg)))
    return lines


def _silence_status(monkeypatch, orch) -> None:
    """`run_loop` calls cmd_status() before returning; silence it in tests."""
    monkeypatch.setattr(orch, "cmd_status", lambda *_a, **_kw: 0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_log_reports_breakdown_on_mixed_terminal_states(
    import_orch, project_root, monkeypatch
):
    """When stories ended in mixed states (completed + failed + blocked), the
    completion log must show the real histogram and use the ⚠ prefix."""
    orch = import_orch
    _seed(project_root, [
        _story("S1", "completed", with_hash=True),
        _story("S2", "completed", with_hash=True),
        _story("S3", "failed"),
        _story("S4", "blocked"),
    ])
    lines = _capture_log(monkeypatch, orch)
    _silence_status(monkeypatch, orch)
    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))

    data = orch.read_progress()
    orch.run_loop(data)

    joined = "\n".join(lines)
    assert "⚠" in joined, f"expected ⚠ prefix in log, got:\n{joined}"
    assert "2 completed" in joined
    assert "1 failed" in joined
    assert "1 blocked" in joined
    # Must NOT use the old misleading line as-is
    assert "All stories complete" not in joined, (
        "old misleading log message must not appear when failures exist"
    )


def test_exit_code_nonzero_when_any_story_failed(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    _seed(project_root, [
        _story("S1", "completed", with_hash=True),
        _story("S2", "failed"),
    ])
    _capture_log(monkeypatch, orch)
    _silence_status(monkeypatch, orch)
    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))

    data = orch.read_progress()
    rc = orch.run_loop(data)

    assert rc == 2, f"expected exit code 2 when any story failed, got {rc}"


def test_exit_code_nonzero_when_any_story_blocked(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    _seed(project_root, [
        _story("S1", "blocked"),
        _story("S2", "blocked"),
    ])
    _capture_log(monkeypatch, orch)
    _silence_status(monkeypatch, orch)
    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))

    data = orch.read_progress()
    rc = orch.run_loop(data)

    assert rc == 2, f"expected exit code 2 when any story blocked, got {rc}"


def test_happy_path_logs_check_and_returns_zero(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    _seed(project_root, [
        _story("S1", "completed", with_hash=True),
        _story("S2", "completed", with_hash=True),
        _story("S3", "completed", with_hash=True),
    ])
    lines = _capture_log(monkeypatch, orch)
    _silence_status(monkeypatch, orch)
    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))

    data = orch.read_progress()
    rc = orch.run_loop(data)

    assert rc == 0, f"expected exit 0 on all-completed, got {rc}"
    joined = "\n".join(lines)
    assert "✓" in joined, f"expected ✓ prefix in happy-path log, got:\n{joined}"
    assert "3 completed" in joined
    assert "⚠" not in joined, "must not use ⚠ prefix when everything completed"


def test_persists_status_after_completion(
    import_orch, project_root, monkeypatch
):
    """Mixed terminal state → data["status"] == "failed" persisted to disk.
    All-completed → data["status"] == "completed"."""
    orch = import_orch

    # Mixed case: 1 completed + 1 failed → persisted status is "failed"
    _seed(project_root, [
        _story("S1", "completed", with_hash=True),
        _story("S2", "failed"),
    ])
    _capture_log(monkeypatch, orch)
    _silence_status(monkeypatch, orch)
    monkeypatch.setattr(orch, "run_production_gates", lambda d: (True, []))

    orch.run_loop(orch.read_progress())
    assert orch.read_progress()["status"] == "failed"

    # Happy case (fresh seed): all completed → "completed"
    _seed(project_root, [
        _story("S1", "completed", with_hash=True),
    ])
    orch.run_loop(orch.read_progress())
    assert orch.read_progress()["status"] == "completed"
