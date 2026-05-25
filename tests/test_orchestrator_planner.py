"""M15.4 + M15.5: Planner, product review, run_loop, signal handlers."""

from __future__ import annotations

import json
import signal as _signal
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _seed_progress(project_root: Path, stories=None, status="pending"):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": status,
        "epics": [{"id": "EPIC-x", "stories": stories or []}],
        "sprints": [],
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# _try_local_planner
# ---------------------------------------------------------------------------

def test_try_local_planner_assigns_waves_topologically(import_orch, project_root):
    orch = import_orch
    spec_json = {
        "epics": [{
            "id": "EPIC-1",
            "stories": [
                {"id": "S1", "title": "first", "depends_on": [], "tasks": [],
                 "acceptance_criteria": [], "estimated_complexity": "small"},
                {"id": "S2", "title": "second", "depends_on": ["S1"], "tasks": [],
                 "acceptance_criteria": [], "estimated_complexity": "small"},
                {"id": "S3", "title": "third", "depends_on": ["S2"], "tasks": [],
                 "acceptance_criteria": [], "estimated_complexity": "small"},
            ],
        }],
    }
    assert orch._try_local_planner(spec_json) is True
    data = json.loads((project_root / ".opencode" / "progress.json").read_text())
    waves = {s["id"]: s["execution_wave"] for s in data["epics"][0]["stories"]}
    assert waves["S1"] == 1
    assert waves["S2"] == 2
    assert waves["S3"] == 3


def test_try_local_planner_parallel_stories_same_wave(import_orch, project_root):
    orch = import_orch
    spec_json = {
        "epics": [{
            "id": "EPIC-1",
            "stories": [
                {"id": "A", "title": "a", "depends_on": [], "tasks": [],
                 "acceptance_criteria": [], "estimated_complexity": "small"},
                {"id": "B", "title": "b", "depends_on": [], "tasks": [],
                 "acceptance_criteria": [], "estimated_complexity": "small"},
            ],
        }],
    }
    orch._try_local_planner(spec_json)
    data = json.loads((project_root / ".opencode" / "progress.json").read_text())
    waves = {s["id"]: s["execution_wave"] for s in data["epics"][0]["stories"]}
    assert waves["A"] == 1 and waves["B"] == 1


def test_try_local_planner_initializes_artifacts(import_orch, project_root):
    orch = import_orch
    spec_json = {
        "epics": [{"id": "E", "stories": [
            {"id": "S1", "title": "t", "depends_on": [], "tasks": [{"id": "T1"}],
             "acceptance_criteria": [], "estimated_complexity": "small"},
        ]}],
    }
    orch._try_local_planner(spec_json)
    data = json.loads((project_root / ".opencode" / "progress.json").read_text())
    s = data["epics"][0]["stories"][0]
    assert s["status"] == "pending"
    assert s["artifacts"]["test_files"] == []
    assert s["artifacts"]["commit_hash"] is None
    assert s["tasks"][0]["status"] == "pending"


def test_try_local_planner_cycle_assigns_late_wave(import_orch, project_root):
    orch = import_orch
    spec_json = {
        "epics": [{"id": "E", "stories": [
            {"id": "A", "title": "a", "depends_on": ["B"], "tasks": [],
             "acceptance_criteria": [], "estimated_complexity": "small"},
            {"id": "B", "title": "b", "depends_on": ["A"], "tasks": [],
             "acceptance_criteria": [], "estimated_complexity": "small"},
            {"id": "C", "title": "c", "depends_on": [], "tasks": [],
             "acceptance_criteria": [], "estimated_complexity": "small"},
        ]}],
    }
    assert orch._try_local_planner(spec_json) is True
    data = json.loads((project_root / ".opencode" / "progress.json").read_text())
    waves = {s["id"]: s["execution_wave"] for s in data["epics"][0]["stories"]}
    assert waves["C"] == 1
    assert waves["A"] > 1 and waves["B"] > 1


def test_try_local_planner_returns_false_on_bad_spec(import_orch, project_root):
    orch = import_orch
    assert orch._try_local_planner({"not_an_epic_dict": True}) is False


# ---------------------------------------------------------------------------
# phase_spec_and_plan
# ---------------------------------------------------------------------------

def test_phase_spec_and_plan_uses_deterministic_path(import_orch, project_root, monkeypatch):
    orch = import_orch

    monkeypatch.setattr(orch, "parse_specs", lambda root: {
        "epics": [{"id": "E", "stories": [
            {"id": "S1", "title": "t", "depends_on": [], "tasks": [],
             "acceptance_criteria": [], "estimated_complexity": "small"},
        ]}],
        "methodology": "structured",
    })

    def boom(*a, **kw):
        raise RuntimeError("call_agent should not be invoked")
    monkeypatch.setattr(orch, "call_agent", boom)

    data = orch.phase_spec_and_plan(spec_path=None, use_llm_spec=False)
    assert data["version"] == 1
    assert len(data["epics"]) == 1


def test_phase_spec_and_plan_dies_when_no_epics(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "parse_specs", lambda root: {"epics": []})
    with pytest.raises(SystemExit):
        orch.phase_spec_and_plan(spec_path=None, use_llm_spec=False)


def test_phase_spec_and_plan_dies_on_malformed_spec(import_orch, project_root, monkeypatch):
    orch = import_orch

    def raise_malformed(root):
        raise orch.MalformedSpec(Path("docs/specs/x.md"), 1, "missing AC")
    monkeypatch.setattr(orch, "parse_specs", raise_malformed)
    with pytest.raises(SystemExit):
        orch.phase_spec_and_plan(spec_path=None, use_llm_spec=False)


# ---------------------------------------------------------------------------
# run_product_review
# ---------------------------------------------------------------------------

def test_run_product_review_pass_as_is(import_orch, project_root, monkeypatch):
    orch = import_orch
    data = {"epics": [{"id": "E", "stories": [
        {"id": "S1", "status": "completed", "title": "x",
         "artifacts": {"implementation_files": ["src/x.py"]}},
    ]}]}
    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "All good.\nVERDICT: PASS_AS_IS\n")

    verdict, parsed = orch.run_product_review(data)
    assert verdict == "PASS_AS_IS"
    assert parsed["verdict"] == "PASS_AS_IS"


def test_run_product_review_reopen_extracts_story_ids(import_orch, project_root, monkeypatch):
    orch = import_orch
    data = {"epics": [{"id": "E", "stories": [
        {"id": "S1", "status": "completed", "title": "x", "artifacts": {}},
    ]}]}
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "VERDICT: REOPEN STORY-x, STORY-y\n")

    verdict, parsed = orch.run_product_review(data)
    assert verdict == "REOPEN"
    assert "STORY-x" in parsed["story_ids"]
    assert "STORY-y" in parsed["story_ids"]


# ---------------------------------------------------------------------------
# _product_review_enabled / _max_product_review_cycles
# ---------------------------------------------------------------------------

def test_product_review_disabled_by_default(import_orch, project_root):
    assert import_orch._product_review_enabled() is False


def test_product_review_enabled_when_configured(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"product_review_enabled": True}}), encoding="utf-8"
    )
    assert import_orch._product_review_enabled() is True


def test_product_review_disabled_on_malformed_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("not json", encoding="utf-8")
    assert import_orch._product_review_enabled() is False


def test_max_product_review_cycles_default(import_orch, project_root):
    assert import_orch._max_product_review_cycles() == 2


def test_max_product_review_cycles_configured(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"max_product_review_cycles": 5}}), encoding="utf-8"
    )
    assert import_orch._max_product_review_cycles() == 5


def test_max_product_review_cycles_falls_back_on_bad_value(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"max_product_review_cycles": "many"}}', encoding="utf-8"
    )
    assert import_orch._max_product_review_cycles() == 2


# ---------------------------------------------------------------------------
# run_loop
# ---------------------------------------------------------------------------

def test_run_loop_terminates_when_only_story_already_completed(import_orch, project_root, capsys):
    orch = import_orch
    story = {
        "id": "STORY-x", "title": "done", "status": "completed",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {"commit_hash": "abc"},
    }
    _seed_progress(project_root, stories=[story], status="in_progress")
    data = orch.read_progress()
    rc = orch.run_loop(data, only_story="STORY-x")
    assert rc == 0


def test_run_loop_dies_when_only_story_not_found(import_orch, project_root):
    orch = import_orch
    _seed_progress(project_root, stories=[], status="in_progress")
    data = orch.read_progress()
    with pytest.raises(SystemExit):
        orch.run_loop(data, only_story="STORY-ghost")


def test_run_loop_blocked_when_unmet_deps(import_orch, project_root):
    orch = import_orch
    story = {
        "id": "STORY-needs-x", "title": "needs x", "status": "pending",
        "depends_on": ["STORY-missing"], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {},
    }
    _seed_progress(project_root, stories=[story], status="in_progress")
    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == 2
    after = orch.read_progress()
    assert after["status"] == "blocked"


def test_run_loop_completes_when_all_stories_done(import_orch, project_root, monkeypatch):
    orch = import_orch
    story = {
        "id": "S1", "title": "t", "status": "completed",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {"commit_hash": "abc"},
    }
    _seed_progress(project_root, stories=[story], status="in_progress")
    monkeypatch.setattr(orch, "run_production_gates", lambda data: (True, []))
    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == 0
    assert orch.read_progress()["status"] == "completed"


def test_run_loop_gate_failed_returns_exit_code(import_orch, project_root, monkeypatch):
    orch = import_orch
    story = {
        "id": "S1", "title": "t", "status": "completed",
        "depends_on": [], "execution_wave": 1,
        "estimated_complexity": "small", "acceptance_criteria": [],
        "tasks": [], "artifacts": {"commit_hash": "abc"},
    }
    _seed_progress(project_root, stories=[story], status="in_progress")
    monkeypatch.setattr(orch, "run_production_gates",
                        lambda data: (False, ["tests failing", "uncommitted files"]))
    data = orch.read_progress()
    rc = orch.run_loop(data)
    assert rc == orch.EXIT_GATE_FAILED
    after = orch.read_progress()
    assert after["status"] == "gate_failed"
    assert "tests failing" in after["gate_failures"]


# ---------------------------------------------------------------------------
# _graceful_shutdown / _install_signal_handlers
# ---------------------------------------------------------------------------

def test_graceful_shutdown_persists_and_exits(import_orch, project_root, capsys):
    orch = import_orch
    _seed_progress(project_root)
    orch.GLOBAL_PERSIST_STATE = orch.read_progress()

    with pytest.raises(SystemExit) as exc_info:
        orch._graceful_shutdown(_signal.SIGTERM, None)
    assert exc_info.value.code == orch.EXIT_MORE_WORK
    out = capsys.readouterr().out
    assert "SIGTERM" in out
    assert "state persisted" in out


def test_graceful_shutdown_handles_none_state(import_orch):
    orch = import_orch
    orch.GLOBAL_PERSIST_STATE = None
    with pytest.raises(SystemExit):
        orch._graceful_shutdown(_signal.SIGINT, None)


def test_graceful_shutdown_logs_persist_failure(import_orch, project_root, monkeypatch, capsys):
    orch = import_orch
    orch.GLOBAL_PERSIST_STATE = {"epics": []}

    def boom(data):
        raise OSError("disk full")
    monkeypatch.setattr(orch, "persist", boom)

    with pytest.raises(SystemExit):
        orch._graceful_shutdown(_signal.SIGTERM, None)
    out = capsys.readouterr().out
    assert "persist failed" in out


def test_install_signal_handlers_registers_both(import_orch, monkeypatch):
    orch = import_orch
    registered = {}

    def fake_signal(sig, handler):
        registered[sig] = handler
    monkeypatch.setattr(_signal, "signal", fake_signal)

    orch._install_signal_handlers()
    assert _signal.SIGTERM in registered
    assert _signal.SIGINT in registered
    assert registered[_signal.SIGTERM] is orch._graceful_shutdown
