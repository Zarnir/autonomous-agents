"""M15.8: Final coverage push — call_agent, extract_json, impediments, DoD, sprint_start."""

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


def _seed_progress(project_root: Path, stories=None, sprints=None,
                   current_sprint=0, status="in_progress"):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": status,
        "epics": [{"id": "EPIC-x", "stories": stories or []}],
        "sprints": sprints or [],
        "current_sprint": current_sprint,
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# call_agent
# ---------------------------------------------------------------------------

def test_call_agent_invokes_runner_and_returns_output(import_orch, project_root, monkeypatch):
    orch = import_orch

    class FakeRunner:
        name = "fake"
        def run(self, agent_name, prompt, *, timeout, cwd=None, model=None, skill=None):
            return f"agent={agent_name} prompt_tail={prompt[-30:]}"

    monkeypatch.setattr(orch, "_get_runner_for_agent", lambda name: FakeRunner())
    out = orch.call_agent("engineer", "do thing")
    assert "agent=engineer" in out


def test_call_agent_translates_hard_runner_error(import_orch, project_root, monkeypatch):
    orch = import_orch

    class FakeRunner:
        name = "fake"
        def run(self, *a, **kw):
            from runners import AgentRunnerError
            raise AgentRunnerError("engineer", "authentication failed")

    monkeypatch.setattr(orch, "_get_runner_for_agent", lambda name: FakeRunner())
    with pytest.raises(orch.AgentError) as exc_info:
        orch.call_agent("engineer", "prompt")
    assert "authentication" in str(exc_info.value)


def test_call_agent_retries_on_transient_then_succeeds(import_orch, project_root, monkeypatch):
    orch = import_orch
    call_count = {"n": 0}

    class FakeRunner:
        name = "fake"
        def run(self, *a, **kw):
            from runners import AgentRunnerError
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise AgentRunnerError("engineer", "rate limit 429")
            return "success after retry"

    monkeypatch.setattr(orch, "_get_runner_for_agent", lambda name: FakeRunner())
    import retry as retry_mod
    monkeypatch.setattr(retry_mod, "_compute_backoff", lambda attempt, policy: 0.0)

    out = orch.call_agent("engineer", "prompt")
    assert "success after retry" in out
    assert call_count["n"] == 2


def test_call_agent_prepends_project_context_when_available(import_orch, project_root, monkeypatch):
    orch = import_orch
    captured = {}

    class FakeRunner:
        name = "fake"
        def run(self, agent_name, prompt, **kw):
            captured["prompt"] = prompt
            return "ok"

    monkeypatch.setattr(orch, "_get_runner_for_agent", lambda name: FakeRunner())
    monkeypatch.setattr(orch, "load_project_context", lambda: "Prior story context here")

    orch.call_agent("engineer", "main task")
    assert "Project context" in captured["prompt"]
    assert "Prior story context" in captured["prompt"]
    assert "main task" in captured["prompt"]


def test_call_agent_prepends_adr_context_when_available(import_orch, project_root, monkeypatch):
    orch = import_orch
    captured = {}

    class FakeRunner:
        name = "fake"
        def run(self, agent_name, prompt, **kw):
            captured["prompt"] = prompt
            return "ok"

    monkeypatch.setattr(orch, "_get_runner_for_agent", lambda name: FakeRunner())
    monkeypatch.setattr(orch, "load_recent_adrs", lambda: "ADR-1: use Postgres")

    orch.call_agent("engineer", "main task")
    assert "Architecture decisions" in captured["prompt"]
    assert "ADR-1: use Postgres" in captured["prompt"]


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------

def test_extract_json_from_fenced_block(import_orch):
    text = 'Some prose\n```json\n{"epics": [{"id": "E1"}]}\n```\n'
    result = import_orch.extract_json(text)
    assert result == {"epics": [{"id": "E1"}]}


def test_extract_json_falls_back_to_brace_extraction(import_orch):
    text = 'Random prose {"key": "value"} trailing text'
    result = import_orch.extract_json(text)
    assert result == {"key": "value"}


def test_extract_json_dies_when_no_json(import_orch):
    with pytest.raises(SystemExit):
        import_orch.extract_json("just text with no braces")


def test_extract_json_skips_invalid_candidate(import_orch):
    # First fenced block invalid, second is valid
    text = '```json\n{broken\n```\nthen\n```json\n{"valid": true}\n```\n'
    result = import_orch.extract_json(text)
    assert result == {"valid": True}


# ---------------------------------------------------------------------------
# Impediment log
# ---------------------------------------------------------------------------

def test_next_impediment_number_when_file_missing(import_orch, project_root):
    assert import_orch._next_impediment_number() == 1


def test_next_impediment_number_increments(import_orch, project_root):
    (project_root / "docs").mkdir()
    (project_root / "docs" / "impediments.md").write_text(
        "# Impediments\n\n## IMP-0001: first\nStatus: open\n\n## IMP-0003: third\nStatus: open\n",
        encoding="utf-8",
    )
    assert import_orch._next_impediment_number() == 4


def test_append_impediment_creates_file(import_orch, project_root):
    orch = import_orch
    path = orch.append_impediment("flaky tests", "fail randomly", sprint=2,
                                  suggested_mitigation="add retry")
    text = path.read_text(encoding="utf-8")
    assert "# Impediments" in text
    assert "IMP-0001: flaky tests" in text
    assert "Status: open" in text
    assert "Sprint: #2" in text


def test_append_impediment_appends_to_existing(import_orch, project_root):
    orch = import_orch
    orch.append_impediment("first", "x")
    orch.append_impediment("second", "y")
    text = (project_root / "docs" / "impediments.md").read_text()
    assert "IMP-0001: first" in text
    assert "IMP-0002: second" in text


def test_count_open_impediments_when_file_missing(import_orch, project_root):
    assert import_orch.count_open_impediments() == 0


def test_count_open_impediments_counts_open_only(import_orch, project_root):
    (project_root / "docs").mkdir()
    (project_root / "docs" / "impediments.md").write_text(
        "# Impediments\n"
        "## IMP-1\nStatus: open\n"
        "## IMP-2\nStatus: closed\n"
        "## IMP-3\nStatus: open\n"
        "## IMP-4\nStatus: mitigated\n",
        encoding="utf-8",
    )
    assert import_orch.count_open_impediments() == 2


# ---------------------------------------------------------------------------
# Definition of Done
# ---------------------------------------------------------------------------

def test_load_definition_of_done_returns_default_when_missing(import_orch, project_root):
    text = import_orch.load_definition_of_done()
    assert "# Definition of Done" in text


def test_load_definition_of_done_reads_custom_file(import_orch, project_root):
    (project_root / "docs").mkdir()
    (project_root / "docs" / "definition-of-done.md").write_text(
        "# DoD\nCustom criteria here.\n", encoding="utf-8"
    )
    assert "Custom criteria" in import_orch.load_definition_of_done()


def test_enforce_dod_passes_when_all_done(import_orch, project_root):
    story = {"id": "S1", "status": "completed", "artifacts": {"commit_hash": "abc"}}
    _seed_progress(project_root, stories=[story])
    data = import_orch.read_progress()
    sprint = {
        "number": 1, "story_ids": ["S1"],
        "retro_path": "docs/sprints/01-retro.md",
        "release_path": "docs/releases/v0.1.md",
    }
    ok, failures = import_orch.enforce_definition_of_done(data, sprint)
    assert ok is True
    assert failures == []


def test_enforce_dod_flags_incomplete_stories(import_orch, project_root):
    story = {"id": "S1", "status": "pending", "artifacts": {}}
    _seed_progress(project_root, stories=[story])
    data = import_orch.read_progress()
    sprint = {"number": 1, "story_ids": ["S1"], "retro_path": "x"}
    ok, failures = import_orch.enforce_definition_of_done(data, sprint)
    assert ok is False
    assert any("not completed" in f for f in failures)


def test_enforce_dod_flags_missing_retro(import_orch, project_root):
    story = {"id": "S1", "status": "completed", "artifacts": {}}
    _seed_progress(project_root, stories=[story])
    data = import_orch.read_progress()
    sprint = {"number": 1, "story_ids": ["S1"]}
    ok, failures = import_orch.enforce_definition_of_done(data, sprint)
    assert ok is False
    assert any("No retro" in f for f in failures)


def test_enforce_dod_flags_missing_release_notes_when_commits_exist(import_orch, project_root):
    story = {"id": "S1", "status": "completed",
             "artifacts": {"commit_hash": "abc1234"}}
    _seed_progress(project_root, stories=[story])
    data = import_orch.read_progress()
    sprint = {"number": 1, "story_ids": ["S1"], "retro_path": "x"}
    ok, failures = import_orch.enforce_definition_of_done(data, sprint)
    assert ok is False
    assert any("release notes" in f for f in failures)


def test_enforce_dod_flags_open_rfcs(import_orch, project_root):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-open.md").write_text("Status: open\n", encoding="utf-8")
    story = {"id": "S1", "status": "completed", "artifacts": {}}
    _seed_progress(project_root, stories=[story])
    data = import_orch.read_progress()
    sprint = {"number": 1, "story_ids": ["S1"], "retro_path": "x"}
    ok, failures = import_orch.enforce_definition_of_done(data, sprint)
    assert ok is False
    assert any("Open RFCs" in f for f in failures)


# ---------------------------------------------------------------------------
# find_open_rfcs (complementary cases)
# ---------------------------------------------------------------------------

def test_find_open_rfcs_returns_multiple_open(import_orch, project_root):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-open.md").write_text("# RFC\n\nStatus: open\n")
    (rfc_dir / "0002-closed.md").write_text("# RFC\n\nStatus: resolved\n")
    (rfc_dir / "0003-open2.md").write_text("# RFC\n\nStatus: open\n")

    result = import_orch.find_open_rfcs()
    assert len(result) == 2
    names = sorted(p.name for p in result)
    assert "0001-open.md" in names
    assert "0003-open2.md" in names


# ---------------------------------------------------------------------------
# _sprint_start
# ---------------------------------------------------------------------------

def test_sprint_start_returns_1_when_no_sprints(import_orch, project_root):
    _seed_progress(project_root, stories=[])
    rc = import_orch._sprint_start(argparse.Namespace())
    assert rc == 1


def test_sprint_start_returns_1_when_last_sprint_completed(import_orch, project_root):
    _seed_progress(
        project_root, stories=[],
        sprints=[{"number": 1, "status": "completed", "story_ids": []}],
    )
    rc = import_orch._sprint_start(argparse.Namespace())
    assert rc == 1


def test_sprint_start_returns_0_when_no_eligible_stories(import_orch, project_root):
    story = {"id": "S1", "status": "completed", "depends_on": [],
             "execution_wave": 1, "estimated_complexity": "small",
             "acceptance_criteria": [], "tasks": [], "artifacts": {}}
    _seed_progress(
        project_root, stories=[story],
        sprints=[{
            "number": 1, "status": "planned", "story_ids": ["S1"],
            "velocity_points": 0,
        }],
    )
    rc = import_orch._sprint_start(argparse.Namespace())
    assert rc == 0


def test_sprint_start_runs_eligible_story_via_stub(import_orch, project_root, monkeypatch):
    story = {"id": "S1", "title": "t", "status": "pending", "depends_on": [],
             "execution_wave": 1, "estimated_complexity": "small",
             "acceptance_criteria": [], "tasks": [], "artifacts": {}}
    _seed_progress(
        project_root, stories=[story],
        sprints=[{
            "number": 1, "status": "planned", "story_ids": ["S1"],
            "velocity_points": 0,
        }],
    )

    def fake_run_story(data, s):
        for ep in data["epics"]:
            for st in ep["stories"]:
                if st["id"] == s["id"]:
                    st["status"] = "completed"
        return data

    monkeypatch.setattr(import_orch, "run_story", fake_run_story)
    monkeypatch.setattr(import_orch, "run_watcher", lambda d: 0)

    rc = import_orch._sprint_start(argparse.Namespace())
    assert rc == 0
