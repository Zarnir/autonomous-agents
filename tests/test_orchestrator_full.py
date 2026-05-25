"""M17.7: orchestrator.py 100% coverage push — comprehensive edge tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _seed(project_root, stories=None, sprints=None, **extra):
    (project_root / ".opencode").mkdir(exist_ok=True)
    data = {
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": stories or []}],
        "sprints": sprints or [],
    }
    data.update(extra)
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# _float_env malformed value
# ---------------------------------------------------------------------------

def test_float_env_invalid_value_returns_default(import_orch, monkeypatch, capsys):
    monkeypatch.setenv("AA_TEST_FLOAT", "not-a-number")
    val = import_orch._float_env("AA_TEST_FLOAT", 1.5)
    assert val == 1.5
    err = capsys.readouterr().err
    assert "not a number" in err


# ---------------------------------------------------------------------------
# load_config malformed paths
# ---------------------------------------------------------------------------

def test_load_config_malformed_json_returns_defaults(import_orch, project_root, capsys):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("not json", encoding="utf-8")
    cfg = import_orch.load_config()
    assert cfg == {}
    err = capsys.readouterr().err
    assert "could not read" in err


def test_load_config_handles_bad_max_review_cycles(import_orch, project_root, capsys):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"max_review_cycles": "abc"}}), encoding="utf-8"
    )
    import_orch.load_config()
    err = capsys.readouterr().err
    assert "max_review_cycles" in err


def test_load_config_handles_bad_max_budget_usd(import_orch, project_root, capsys, monkeypatch):
    monkeypatch.delenv("MAX_BUDGET_USD", raising=False)
    monkeypatch.setattr(import_orch, "MAX_BUDGET_USD", 0.0)
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"max_budget_usd": "many"}}), encoding="utf-8"
    )
    import_orch.load_config()
    err = capsys.readouterr().err
    assert "max_budget_usd" in err


def test_load_config_handles_bad_agent_retry_base_delay(import_orch, project_root, capsys):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"agent_retry_base_delay_sec": "fast"}}), encoding="utf-8"
    )
    import_orch.load_config()
    err = capsys.readouterr().err
    assert "agent_retry_base_delay_sec" in err


# ---------------------------------------------------------------------------
# read_progress die paths
# ---------------------------------------------------------------------------

def test_read_progress_dies_when_file_missing(import_orch, project_root):
    with pytest.raises(SystemExit):
        import_orch.read_progress()


def test_read_progress_dies_on_schema_mismatch(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps({"schema_version": "1.5", "version": 1, "epics": []}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        import_orch.read_progress()


def test_read_progress_dies_on_corrupt_json(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(
        "{broken json", encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        import_orch.read_progress()


def test_read_progress_dies_on_unicode_decode(import_orch, project_root, monkeypatch):
    (project_root / ".opencode").mkdir(exist_ok=True)
    progress = project_root / ".opencode" / "progress.json"
    progress.write_text(json.dumps({"schema_version": "2.0", "version": 1}), encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("progress.json"):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    with pytest.raises(SystemExit):
        import_orch.read_progress()


def test_append_execution_log_truncates_when_huge(import_orch):
    data = {"execution_log": [{"ts": "x", "msg": f"m{i}"} for i in range(6000)]}
    import_orch.append_execution_log(data, "newest")
    assert len(data["execution_log"]) == 4000
    assert data["execution_log"][-1]["msg"] == "newest"


# ---------------------------------------------------------------------------
# detect_test_command per-project-type
# ---------------------------------------------------------------------------

def test_detect_test_command_test_cmd_override(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "TEST_CMD_OVERRIDE", "echo hello world")
    assert import_orch.detect_test_command() == ["echo", "hello", "world"]


def test_detect_test_command_npm_for_package_json(import_orch, project_root):
    (project_root / "package.json").write_text("{}", encoding="utf-8")
    cmd = import_orch.detect_test_command()
    assert cmd[0] == "npm"


def test_detect_test_command_pytest_for_pyproject(import_orch, project_root):
    (project_root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert import_orch.detect_test_command()[0] == "pytest"


def test_detect_test_command_go_for_gomod(import_orch, project_root):
    (project_root / "go.mod").write_text("module x\n", encoding="utf-8")
    assert import_orch.detect_test_command()[0] == "go"


def test_detect_test_command_cargo_for_cargo_toml(import_orch, project_root):
    (project_root / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    assert import_orch.detect_test_command()[0] == "cargo"


def test_detect_test_command_none_when_no_markers(import_orch, project_root):
    assert import_orch.detect_test_command() is None


def test_epic_for_story_returns_none_when_missing(import_orch):
    data = {"epics": [{"id": "E", "stories": [{"id": "S1"}]}]}
    assert import_orch.epic_for_story(data, "ghost") is None


# ---------------------------------------------------------------------------
# extract_json error paths
# ---------------------------------------------------------------------------

def test_extract_json_die_includes_tail_for_long_output(import_orch):
    long_text = "x" * 1000 + " not json"
    with pytest.raises(SystemExit):
        import_orch.extract_json(long_text)


def test_extract_json_skips_empty_candidate(import_orch):
    text = '```json\n\n```\nthen {"valid": true}\n'
    assert import_orch.extract_json(text) == {"valid": True}


# ---------------------------------------------------------------------------
# Impediment + DoD OSError handlers
# ---------------------------------------------------------------------------

def test_next_impediment_number_handles_unreadable_file(import_orch, project_root, monkeypatch):
    (project_root / "docs").mkdir()
    (project_root / "docs" / "impediments.md").write_text("# x\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("impediments.md"):
            raise OSError("disk")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    assert import_orch._next_impediment_number() == 1


def test_count_open_impediments_handles_oserror(import_orch, project_root, monkeypatch):
    (project_root / "docs").mkdir()
    (project_root / "docs" / "impediments.md").write_text("Status: open\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("impediments.md"):
            raise OSError("disk")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    assert import_orch.count_open_impediments() == 0


def test_load_definition_of_done_handles_oserror(import_orch, project_root, monkeypatch):
    (project_root / "docs").mkdir()
    (project_root / "docs" / "definition-of-done.md").write_text("custom\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("definition-of-done.md"):
            raise OSError("disk")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    text = import_orch.load_definition_of_done()
    assert "# Definition of Done" in text


def test_find_open_rfcs_skips_unreadable_file(import_orch, project_root, monkeypatch):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-bad.md").write_text("Status: open\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("0001-bad.md"):
            raise OSError("disk")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    assert import_orch.find_open_rfcs() == []


def test_parse_rfc_resolution_returns_unknown_for_no_verdict(import_orch):
    out = "Some prose without verdict\nRecommendation: NEW STORY\n"
    parsed = import_orch.parse_rfc_resolution(out)
    assert parsed["verdict"] == "UNKNOWN"
    assert parsed["action"] == "NEW_STORY"


def test_rfc_auto_apply_enabled_default(import_orch, project_root):
    assert import_orch._rfc_auto_apply_enabled() is True


def test_rfc_auto_apply_disabled_via_config(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"rfc_auto_apply": False}}), encoding="utf-8"
    )
    assert import_orch._rfc_auto_apply_enabled() is False


def test_rfc_auto_apply_malformed_config_returns_default(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text("garbage", encoding="utf-8")
    assert import_orch._rfc_auto_apply_enabled() is True


def test_process_rfc_files_returns_zero_when_no_open(import_orch, project_root):
    _seed(project_root)
    data = import_orch.read_progress()
    rc = import_orch.process_rfc_files(data)
    assert rc == 0


def test_process_rfc_files_when_auto_apply_disabled_needs_human(
    import_orch, project_root, monkeypatch
):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-foo.md").write_text("Status: open\n", encoding="utf-8")
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"rfc_auto_apply": False}}), encoding="utf-8"
    )
    _seed(project_root)
    monkeypatch.setattr(
        import_orch, "call_agent",
        lambda *a, **kw: "Recommendation: NONE\nVERDICT: RFC_RESOLVED\n",
    )
    data = import_orch.read_progress()
    rc = import_orch.process_rfc_files(data)
    assert rc == import_orch.EXIT_RFC_NEEDS_HUMAN


def test_process_rfc_files_swallows_call_agent_failure(
    import_orch, project_root, monkeypatch
):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-foo.md").write_text("Status: open\n", encoding="utf-8")
    _seed(project_root)

    def boom(*a, **kw):
        raise import_orch.AgentError("architect", "rate limited")
    monkeypatch.setattr(import_orch, "call_agent", boom)

    data = import_orch.read_progress()
    import_orch.process_rfc_files(data)


def test_process_rfc_files_swallows_append_oserror(
    import_orch, project_root, monkeypatch, capsys
):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    rfc_path = rfc_dir / "0001-foo.md"
    rfc_path.write_text("Status: open\n", encoding="utf-8")
    _seed(project_root)
    monkeypatch.setattr(
        import_orch, "call_agent",
        lambda *a, **kw: "Recommendation: NONE\nVERDICT: RFC_RESOLVED\n",
    )

    real_open = open
    def flaky_open(path, mode="r", *args, **kw):
        if "0001-foo.md" in str(path) and "a" in mode:
            raise OSError("write fail")
        return real_open(path, mode, *args, **kw)
    monkeypatch.setattr("builtins.open", flaky_open)

    data = import_orch.read_progress()
    import_orch.process_rfc_files(data)
    out = capsys.readouterr().out
    assert "could not append" in out


# ---------------------------------------------------------------------------
# Watcher signals
# ---------------------------------------------------------------------------

def test_detect_watcher_signals_returns_empty_for_clean_run(import_orch):
    data = {"epics": [{"id": "E", "stories": [
        {"id": "S1", "status": "completed", "artifacts": {}, "title": "x"},
    ]}], "execution_log": []}
    assert import_orch.detect_watcher_signals(data) == []


def test_detect_watcher_signals_flags_cascade(import_orch):
    """When more stories are blocked than max_blocked threshold (3), emit cascade signal."""
    data = {
        "epics": [{"id": "E", "stories": [
            {"id": f"S{i}", "status": "blocked", "title": "x", "artifacts": {}}
            for i in range(5)  # 5 > default max_blocked (3)
        ]}],
        "execution_log": [],
    }
    signals = import_orch.detect_watcher_signals(data)
    assert any(s["type"] == "cascade" for s in signals)


def test_detect_watcher_signals_flags_repeated_retries(import_orch):
    """Stories with many retry entries in execution_log trigger repeated_retries signal."""
    data = {
        "epics": [{"id": "E", "stories": [
            {"id": "S1", "status": "in_progress", "title": "x", "artifacts": {}},
        ]}],
        "execution_log": [
            {"ts": "x", "msg": f"retry attempt story=S1 #{i}"}
            for i in range(5)
        ],
    }
    signals = import_orch.detect_watcher_signals(data)
    assert any(s["type"] == "repeated_retries" for s in signals)


def test_run_watcher_skips_when_disabled(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"watcher_enabled": False}}), encoding="utf-8"
    )
    data = {"epics": [], "execution_log": []}
    assert import_orch.run_watcher(data) == 0


def test_run_watcher_dedupes_existing_rfc(import_orch, project_root):
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-cascade.md").write_text(
        "# RFC\nStatus: open\n## Detail\ncascade story=n/a count=5\n",
        encoding="utf-8",
    )
    data = {
        "epics": [{"id": "E", "stories": [
            {"id": f"S{i}", "status": "blocked", "title": "x", "artifacts": {}}
            for i in range(5)
        ]}],
        "execution_log": [],
    }
    written = import_orch.run_watcher(data)
    assert written == 0


def test_run_watcher_writes_new_rfc_when_no_dedup(import_orch, project_root):
    """No existing RFC for the signal → write a stub."""
    data = {
        "epics": [{"id": "E", "stories": [
            {"id": f"S{i}", "status": "blocked", "title": "x", "artifacts": {}}
            for i in range(5)
        ]}],
        "execution_log": [],
    }
    written = import_orch.run_watcher(data)
    assert written >= 1
    assert (project_root / "docs" / "rfc").exists()


# ---------------------------------------------------------------------------
# Project context
# ---------------------------------------------------------------------------

def test_update_project_context_disabled(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"project_context_enabled": False}}), encoding="utf-8"
    )
    data = {"epics": []}
    story = {"id": "S1", "artifacts": {}, "title": "x", "acceptance_criteria": []}
    import_orch.update_project_context(data, story)
    assert not (project_root / "docs" / "specs" / "PROJECT_CONTEXT.md").exists()


def test_update_project_context_logs_exception(import_orch, project_root, monkeypatch, capsys):
    def boom(self, *a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(import_orch.Path, "mkdir", boom)

    data = {"epics": []}
    story = {"id": "S1", "artifacts": {}, "title": "x", "acceptance_criteria": []}
    import_orch.update_project_context(data, story)
    assert "project context update failed" in capsys.readouterr().out


def test_load_project_context_disabled(import_orch, project_root):
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "config.json").write_text(
        json.dumps({"pipeline": {"project_context_enabled": False}}), encoding="utf-8"
    )
    assert import_orch.load_project_context() == ""


def test_load_project_context_handles_oserror(import_orch, project_root, monkeypatch):
    pc = project_root / "docs" / "specs" / "PROJECT_CONTEXT.md"
    pc.parent.mkdir(parents=True)
    pc.write_text("# pc\n## STORY-x\nstuff\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("PROJECT_CONTEXT.md"):
            raise OSError("disk")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    assert import_orch.load_project_context() == ""


def test_load_project_context_zero_max_entries(import_orch, project_root):
    pc = project_root / "docs" / "specs" / "PROJECT_CONTEXT.md"
    pc.parent.mkdir(parents=True)
    pc.write_text("# pc\n## STORY-x\nstuff\n", encoding="utf-8")
    assert import_orch.load_project_context(max_entries=0) == ""


def test_load_project_context_no_entries_in_file(import_orch, project_root):
    pc = project_root / "docs" / "specs" / "PROJECT_CONTEXT.md"
    pc.parent.mkdir(parents=True)
    pc.write_text("# Just a header, no story entries\n", encoding="utf-8")
    assert import_orch.load_project_context() == ""


# ---------------------------------------------------------------------------
# main() argparse dispatch - missing cmd handlers
# ---------------------------------------------------------------------------

def test_main_dispatches_to_resume(import_orch, monkeypatch):
    def fake(args):
        return 0
    monkeypatch.setattr(import_orch, "cmd_resume", fake)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "resume"])
    assert import_orch.main() == 0


def test_main_dispatches_to_health_check(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_health_check", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "health-check"])
    assert import_orch.main() == 0


def test_main_dispatches_to_setup(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_setup", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "setup"])
    assert import_orch.main() == 0


def test_main_dispatches_to_new(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_new", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "new", "myproj"])
    assert import_orch.main() == 0


def test_main_dispatches_to_wizard(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_wizard", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "wizard"])
    assert import_orch.main() == 0


def test_main_dispatches_to_revisit(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_revisit", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "revisit", "STORY-x"])
    assert import_orch.main() == 0


def test_main_dispatches_to_discover(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_discover", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "discover", "some idea"])
    assert import_orch.main() == 0


def test_main_dispatches_to_refine(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_refine", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "refine", "STORY-x"])
    assert import_orch.main() == 0


def test_main_dispatches_to_rfc(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_rfc", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "rfc"])
    assert import_orch.main() == 0


def test_main_dispatches_to_develop(import_orch, monkeypatch):
    monkeypatch.setattr(import_orch, "cmd_develop", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "develop"])
    assert import_orch.main() == 0
