"""Unit tests for lib/orchestrator.py.

All tests run hermetically in tmp_path. No agent subprocesses are spawned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrator
from orchestrator import (
    VersionConflict,
    _int_env,
    cascade_fail,
    deps_satisfied,
    is_pass,
    next_eligible_story,
    parse_verdict,
    persist,
    read_progress,
    write_progress,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_story(sid: str, **overrides) -> dict:
    base = {
        "id": sid,
        "title": f"Story {sid}",
        "status": "pending",
        "depends_on": [],
        "execution_wave": 1,
        "acceptance_criteria": [],
        "tasks": [],
        "artifacts": {
            "commit_hash": None,
            "test_files": [],
            "implementation_files": [],
        },
    }
    base.update(overrides)
    return base


def _make_progress(stories: list[dict]) -> dict:
    return {
        "schema_version": orchestrator.SCHEMA_VERSION,
        "version": 1,
        "updated_at": "2026-05-12T00:00:00Z",
        "status": "in_progress",
        "current_story_id": None,
        "completed_stories": [],
        "failed_stories": [],
        "blocked_stories": [],
        "epics": [{"id": "EPIC-x", "title": "x", "stories": stories}],
        "execution_log": [],
    }


# ---------------------------------------------------------------------------
# next_eligible_story
# ---------------------------------------------------------------------------

def test_next_eligible_returns_lowest_wave_first():
    data = _make_progress([
        _make_story("STORY-b", execution_wave=2),
        _make_story("STORY-a", execution_wave=1),
    ])
    s = next_eligible_story(data)
    assert s is not None
    assert s["id"] == "STORY-a"


def test_next_eligible_skips_stories_with_unmet_deps():
    data = _make_progress([
        _make_story("STORY-a", status="pending", depends_on=["STORY-b"]),
        _make_story("STORY-b", status="pending"),
    ])
    s = next_eligible_story(data)
    assert s["id"] == "STORY-b"


def test_next_eligible_returns_none_when_all_terminal():
    data = _make_progress([
        _make_story("STORY-a", status="completed"),
        _make_story("STORY-b", status="failed"),
    ])
    assert next_eligible_story(data) is None


def test_deps_satisfied_requires_commit_hash():
    data = _make_progress([
        _make_story("STORY-dep", status="completed"),
        _make_story("STORY-a", depends_on=["STORY-dep"]),
    ])
    story_a = data["epics"][0]["stories"][1]
    ok, unmet = deps_satisfied(data, story_a)
    assert not ok
    assert any("no_commit" in u for u in unmet)


def test_deps_satisfied_passes_when_completed_with_hash():
    completed = _make_story("STORY-dep", status="completed")
    completed["artifacts"]["commit_hash"] = "abc123def456"
    data = _make_progress([completed, _make_story("STORY-a", depends_on=["STORY-dep"])])
    story_a = data["epics"][0]["stories"][1]
    ok, unmet = deps_satisfied(data, story_a)
    assert ok and unmet == []


# ---------------------------------------------------------------------------
# cascade_fail
# ---------------------------------------------------------------------------

def test_cascade_fail_blocks_direct_dependents():
    data = _make_progress([
        _make_story("STORY-root", status="failed"),
        _make_story("STORY-child", depends_on=["STORY-root"]),
    ])
    n = cascade_fail(data, "STORY-root", "test failure")
    assert n == 1
    child = data["epics"][0]["stories"][1]
    assert child["status"] == "blocked"
    assert "STORY-child" in data["blocked_stories"]


def test_cascade_fail_walks_transitive_dependents():
    data = _make_progress([
        _make_story("STORY-root", status="failed"),
        _make_story("STORY-a", depends_on=["STORY-root"]),
        _make_story("STORY-b", depends_on=["STORY-a"]),
        _make_story("STORY-c", depends_on=["STORY-b"]),
    ])
    n = cascade_fail(data, "STORY-root", "kaboom")
    assert n == 3
    for sid in ("STORY-a", "STORY-b", "STORY-c"):
        s = next(x for x in data["epics"][0]["stories"] if x["id"] == sid)
        assert s["status"] == "blocked", f"{sid} should be blocked"


def test_cascade_fail_does_not_touch_independent_stories():
    data = _make_progress([
        _make_story("STORY-root", status="failed"),
        _make_story("STORY-unrelated"),
    ])
    cascade_fail(data, "STORY-root", "x")
    unrelated = next(s for s in data["epics"][0]["stories"] if s["id"] == "STORY-unrelated")
    assert unrelated["status"] == "pending"


# ---------------------------------------------------------------------------
# Convergence rules
# ---------------------------------------------------------------------------

def test_parse_verdict_extracts_pass():
    assert parse_verdict("VERDICT: PASS").startswith("PASS")


def test_parse_verdict_marks_convergence():
    v = parse_verdict("VERDICT: NEEDS_CHANGES\n[CONVERGENCE]")
    assert "[CONVERGENCE]" in v


def test_is_pass_accepts_clean_pass():
    assert is_pass("PASS")


def test_is_pass_rejects_block():
    assert not is_pass("BLOCK")


def test_is_pass_rejects_convergence_after_fail():
    """Regression: [CONVERGENCE] alone is not enough — prior verdict must have been PASS."""
    assert not is_pass("NEEDS_CHANGES [CONVERGENCE]", prior_was_pass=False)


def test_is_pass_accepts_convergence_after_pass():
    assert is_pass("PASS [CONVERGENCE]", prior_was_pass=True)


# ---------------------------------------------------------------------------
# Persist / VersionConflict
# ---------------------------------------------------------------------------

def test_write_progress_creates_file_atomically(progress_file: Path):
    data = _make_progress([_make_story("STORY-a")])
    write_progress(data, expected_version=1)
    assert progress_file.exists()
    on_disk = json.loads(progress_file.read_text(encoding="utf-8"))
    assert on_disk["version"] == 2


def test_write_progress_raises_on_version_mismatch(progress_file: Path):
    data = _make_progress([_make_story("STORY-a")])
    progress_file.write_text(json.dumps({**data, "version": 5}), encoding="utf-8")
    with pytest.raises(VersionConflict):
        write_progress(data, expected_version=1)


def test_persist_retries_on_conflict_then_succeeds(progress_file: Path):
    initial = _make_progress([_make_story("STORY-a")])
    initial["version"] = 5
    progress_file.write_text(json.dumps(initial), encoding="utf-8")

    stale = _make_progress([_make_story("STORY-a")])
    stale["version"] = 1
    persist(stale)
    final = json.loads(progress_file.read_text(encoding="utf-8"))
    assert final["version"] == 6


def test_persist_gives_up_after_max_retries(progress_file: Path, monkeypatch):
    """Regression: persist used to silently overwrite on conflict; now re-raises after retries."""
    data = _make_progress([_make_story("STORY-a")])
    progress_file.write_text(json.dumps(data), encoding="utf-8")

    call_count = {"n": 0}

    def always_conflict(*args, **kwargs):
        call_count["n"] += 1
        raise VersionConflict(expected=1, found=99)

    monkeypatch.setattr(orchestrator, "write_progress", always_conflict)
    with pytest.raises(VersionConflict):
        persist(data)
    assert call_count["n"] == orchestrator.MAX_PERSIST_RETRIES


def test_read_progress_dies_on_corrupt_json(progress_file: Path):
    """Regression: raw json.JSONDecodeError used to escape read_progress."""
    progress_file.write_text("{ broken not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        read_progress()


def test_read_progress_dies_on_non_utf8(progress_file: Path):
    """Regression: raw UnicodeDecodeError used to escape read_progress."""
    progress_file.write_bytes(b"\xff\xfe not utf-8 at all")
    with pytest.raises(SystemExit):
        read_progress()


# ---------------------------------------------------------------------------
# Config robustness
# ---------------------------------------------------------------------------

def test_int_env_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("AA_TEST_VAR", raising=False)
    assert _int_env("AA_TEST_VAR", 42) == 42


def test_int_env_parses_valid_int(monkeypatch):
    monkeypatch.setenv("AA_TEST_VAR", "7")
    assert _int_env("AA_TEST_VAR", 42) == 7


def test_int_env_falls_back_on_bad_value(monkeypatch, capsys):
    """Regression: int(env) used to crash at import time on bad input."""
    monkeypatch.setenv("AA_TEST_VAR", "not-a-number")
    result = _int_env("AA_TEST_VAR", 42)
    assert result == 42
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "not-a-number" in captured.err


def test_int_env_handles_empty_string(monkeypatch):
    monkeypatch.setenv("AA_TEST_VAR", "")
    assert _int_env("AA_TEST_VAR", 42) == 42


# ---------------------------------------------------------------------------
# extract_* contract
# ---------------------------------------------------------------------------

def test_extract_test_files_returns_list_of_paths():
    out = (
        "## Test files written\n"
        "- `tests/test_foo.py` covers AC1\n"
        "- `tests/test_bar.py` covers AC2\n"
        "Status: RED_VERIFIED\n"
    )
    files = orchestrator.extract_test_files(out)
    assert files == ["tests/test_foo.py", "tests/test_bar.py"]


def test_extract_commit_hash_extracts_sha():
    out = "Status: COMMITTED\nCommit hash: abc1234def\nBranch: feat/x\n"
    assert orchestrator.extract_commit_hash(out) == "abc1234def"


def test_extract_commit_hash_returns_none_when_absent():
    assert orchestrator.extract_commit_hash("Status: COMMITTED\n") is None


def test_agent_models_loaded_from_config_per_agent_name(project_root, monkeypatch):
    """Regression for M3.3: each named agent gets its configured model."""
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"models": {"check": "claude-haiku-4-5-20251001", '
        '"make": "claude-sonnet-4-6", "default": "claude-opus-4-7"}}',
        encoding="utf-8",
    )
    # Force re-load (orchestrator.load_config was called at import time before chdir)
    import importlib
    import orchestrator as _o
    importlib.reload(_o)
    assert _o.AGENT_MODELS.get("check") == "claude-haiku-4-5-20251001"
    assert _o.AGENT_MODELS.get("make") == "claude-sonnet-4-6"
    assert _o.AGENT_MODELS.get("default") == "claude-opus-4-7"
    # Restore for subsequent tests
    importlib.reload(_o)


def test_runner_config_loaded_from_pipeline_runner_field(project_root):
    """Regression for M1.1: pipeline.runner sets _CONFIG_RUNNER."""
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(
        '{"pipeline": {"runner": "claude"}}', encoding="utf-8"
    )
    import importlib
    import orchestrator as _o
    importlib.reload(_o)
    assert _o._CONFIG_RUNNER == "claude"
    importlib.reload(_o)


def test_extract_criterion_mapping_parses_table():
    out = (
        "| Acceptance Criterion | Covering Test |\n"
        "| --- | --- |\n"
        "| AC1: prints hello | `tests/test_hello.py::test_prints` |\n"
    )
    mapping = orchestrator.extract_criterion_mapping(out)
    assert "AC1: prints hello" in mapping
    assert mapping["AC1: prints hello"] == ["tests/test_hello.py::test_prints"]
