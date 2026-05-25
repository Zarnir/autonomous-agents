"""M16 followup: call_agent budget tracking + _sprint_start error branches + small coverage."""

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


def _seed_progress(project_root, stories=None, sprints=None, **extra):
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": stories or []}],
        "sprints": sprints or [], "current_sprint": 0,
        "completed_stories": [], "failed_stories": [], "blocked_stories": [],
    }
    data.update(extra)
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# call_agent budget exceeded path
# ---------------------------------------------------------------------------

def test_call_agent_raises_when_budget_exceeded(import_orch, project_root, monkeypatch):
    orch = import_orch

    class FakeRunner:
        name = "fake"
        def run(self, agent_name, prompt, **kw):
            return "agent output"

    monkeypatch.setattr(orch, "_get_runner", lambda: FakeRunner())
    monkeypatch.setattr(orch, "_COST_TRACKING_AVAILABLE", True)
    monkeypatch.setattr(orch, "_cost_parse", lambda out, name: {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(orch, "_cost_compute", lambda usage, model: 99.99)

    def fake_accumulate(tracking, name, usage, cost):
        tracking["total_usd"] = tracking.get("total_usd", 0) + cost
    monkeypatch.setattr(orch, "_cost_accumulate", fake_accumulate)

    monkeypatch.setattr(orch, "MAX_BUDGET_USD", 1.0)
    orch.GLOBAL_PERSIST_STATE = {"cost_tracking": {}, "status": "in_progress"}

    with pytest.raises(orch.AgentError) as exc_info:
        orch.call_agent("engineer", "prompt")
    assert "budget exceeded" in str(exc_info.value).lower()
    assert orch.GLOBAL_PERSIST_STATE["status"] == "budget_exceeded"


def test_call_agent_swallows_cost_tracking_exceptions(import_orch, project_root, monkeypatch):
    orch = import_orch

    class FakeRunner:
        name = "fake"
        def run(self, agent_name, prompt, **kw):
            return "agent output"

    monkeypatch.setattr(orch, "_get_runner", lambda: FakeRunner())
    monkeypatch.setattr(orch, "_COST_TRACKING_AVAILABLE", True)

    def broken_parse(out, name):
        raise RuntimeError("cost parser broken")
    monkeypatch.setattr(orch, "_cost_parse", broken_parse)

    orch.GLOBAL_PERSIST_STATE = {"cost_tracking": {}}
    out = orch.call_agent("engineer", "prompt")
    assert "agent output" in out


# ---------------------------------------------------------------------------
# _sprint_start error branches
# ---------------------------------------------------------------------------

def test_sprint_start_returns_2_when_remaining_blocked_on_deps(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    story = {
        "id": "S1", "title": "t", "status": "pending",
        "depends_on": ["S-ghost"], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {},
    }
    _seed_progress(
        project_root, stories=[story],
        sprints=[{
            "number": 1, "status": "planned", "story_ids": ["S1"],
            "velocity_points": 0,
        }],
    )

    rc = orch._sprint_start(argparse.Namespace())
    assert rc == 2


def test_sprint_start_finalizes_failed_story_on_agent_error_and_continues(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    s1 = {
        "id": "S1", "title": "t", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }
    s2 = {
        "id": "S2", "title": "t2", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }
    _seed_progress(
        project_root, stories=[s1, s2],
        sprints=[{
            "number": 1, "status": "planned", "story_ids": ["S1", "S2"],
            "velocity_points": 0,
        }],
    )

    def fake_run_story(data, story):
        if story["id"] == "S1":
            raise orch.AgentError("make", "timeout")
        # Persist S2 completion to disk so the loop's next read_progress sees it
        for ep in data["epics"]:
            for s in ep["stories"]:
                if s["id"] == "S2":
                    s["status"] = "completed"
                    s.setdefault("artifacts", {})["commit_hash"] = "abc"
        return orch.persist(data)
    monkeypatch.setattr(orch, "run_story", fake_run_story)
    monkeypatch.setattr(orch, "run_watcher", lambda d: 0)

    rc = orch._sprint_start(argparse.Namespace())
    # The AgentError-recovery path finalizes S1 as failed and continues
    # (the loop returns 0 only after all sprint stories are terminal).
    final = orch.read_progress()
    assert final["epics"][0]["stories"][0]["status"] == "failed"


def test_sprint_start_returns_1_on_unexpected_exception(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    story = {
        "id": "S1", "title": "t", "status": "pending", "depends_on": [],
        "execution_wave": 1, "estimated_complexity": "small",
        "acceptance_criteria": [], "tasks": [], "artifacts": {},
    }
    _seed_progress(
        project_root, stories=[story],
        sprints=[{
            "number": 1, "status": "planned", "story_ids": ["S1"],
            "velocity_points": 0,
        }],
    )

    def boom(data, story):
        raise RuntimeError("simulated bug")
    monkeypatch.setattr(orch, "run_story", boom)
    monkeypatch.setattr(orch, "run_watcher", lambda d: 0)

    rc = orch._sprint_start(argparse.Namespace())
    assert rc == 1


# ---------------------------------------------------------------------------
# process_rfc_files file-read failure
# ---------------------------------------------------------------------------

def test_process_rfc_files_skips_unreadable_rfc(import_orch, project_root, monkeypatch, capsys):
    """When the full RFC read fails after find_open_rfcs succeeds, the loop logs and continues."""
    orch = import_orch
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    rfc_path = rfc_dir / "0001-broken.md"
    rfc_path.write_text("# RFC-0001\nStatus: open\n", encoding="utf-8")
    _seed_progress(project_root)

    # find_open_rfcs succeeds (first read returns content). The SECOND read,
    # inside process_rfc_files for the full text, fails.
    real_read = Path.read_text
    call_count = {"n": 0}

    def flaky_read(self, *a, **kw):
        if str(self).endswith("0001-broken.md"):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise OSError("disk error")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky_read)

    data = orch.read_progress()
    orch.process_rfc_files(data)
    out = capsys.readouterr().out
    assert "cannot read" in out or "disk error" in out


# ---------------------------------------------------------------------------
# cmd_refine post-refinement validation failure
# ---------------------------------------------------------------------------

def test_cmd_refine_returns_1_when_validation_fails_after_refinement(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    _seed_progress(project_root, stories=[{
        "id": "STORY-a", "title": "Big", "status": "pending",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "large", "acceptance_criteria": ["AC1"],
        "tasks": [{"id": "T1", "files_to_touch": ["src/a.py"]}],
        "artifacts": {},
    }])
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "Refined\nVERDICT: EPIC_REFINED\n")
    from spec_parser import ValidationReport
    monkeypatch.setattr(orch, "validate_specs",
                        lambda root: ValidationReport(errors=["new validation error"]))

    rc = orch.cmd_refine(argparse.Namespace(story="STORY-a"))
    assert rc == 1


# ---------------------------------------------------------------------------
# _next_rfc_number + write_rfc_stub
# ---------------------------------------------------------------------------

def test_next_rfc_number_when_dir_missing(import_orch, project_root):
    assert import_orch._next_rfc_number() == 1


def test_next_rfc_number_increments(import_orch, project_root):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0002-foo.md").write_text("# RFC-0002\n", encoding="utf-8")
    (rfc_dir / "0005-bar.md").write_text("# RFC-0005\n", encoding="utf-8")
    (rfc_dir / "notes.md").write_text("not an RFC\n", encoding="utf-8")
    assert import_orch._next_rfc_number() == 6


def test_write_rfc_stub_creates_file_with_metadata(import_orch, project_root):
    orch = import_orch
    signal = {
        "type": "stall",
        "story_id": "STORY-x",
        "detail": "stuck for 30 minutes",
    }
    path = orch.write_rfc_stub(signal)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "RFC-0001" in content
    assert "Status: open" in content
    assert "STORY-x" in content
    assert "stuck for 30 minutes" in content


def test_write_rfc_stub_uses_naoa_when_no_story_id(import_orch, project_root):
    orch = import_orch
    signal = {"type": "cascade-failure", "detail": "many failures"}
    path = orch.write_rfc_stub(signal)
    content = path.read_text(encoding="utf-8")
    assert "Story: n/a" in content
