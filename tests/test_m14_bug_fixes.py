"""Regression tests for M14 bug fixes surfaced by the silent-failure-hunter audit.

Each test was written RED-first (verified to fail against the unfixed code)
before the fix landed. Comments mark which bug each test guards against.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


# ---------------------------------------------------------------------------
# M14.1 (HIGH): UNKNOWN architect verdict must set needs_human, not silently resolve
# ---------------------------------------------------------------------------

def test_process_rfc_files_unknown_verdict_sets_needs_human(import_orch, project_root, monkeypatch):
    """RFC architect returns prose with no VERDICT line → must NOT auto-resolve."""
    orch = import_orch

    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    rfc_path = rfc_dir / "0001-stall.md"
    rfc_path.write_text(
        "# RFC-0001\n\nStatus: open\nDetected: 2026-05-17\n\n## Detail\nstuck\n",
        encoding="utf-8",
    )

    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": []}], "sprints": [],
    }), encoding="utf-8")

    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "I had some thoughts but didn't conclude.")

    data = orch.read_progress()
    rc = orch.process_rfc_files(data)

    assert rc == orch.EXIT_RFC_NEEDS_HUMAN, "UNKNOWN verdict must trigger needs_human"

    content = rfc_path.read_text(encoding="utf-8")
    assert "Status: open" in content, "RFC should stay open when verdict can't be parsed"


# ---------------------------------------------------------------------------
# M14.2 (MED): OSError on RFC status update must surface (not silently swallowed)
# ---------------------------------------------------------------------------

def test_process_rfc_files_logs_status_update_oserror(import_orch, project_root, monkeypatch, capsys):
    """A write failure when marking RFC resolved must emit a warning, not vanish."""
    orch = import_orch

    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    rfc_path = rfc_dir / "0002-issue.md"
    rfc_path.write_text(
        "# RFC-0002\n\nStatus: open\n", encoding="utf-8",
    )

    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "E", "stories": []}], "sprints": [],
    }), encoding="utf-8")

    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw:
        "## Architect resolution\n**Recommendation:** NONE\nVERDICT: RFC_RESOLVED\n")

    real_write = Path.write_text
    def flaky_write(self, content, *args, **kwargs):
        # Status-update write to the RFC file always fails — verifies the
        # production code logs the failure instead of swallowing it.
        if str(self).endswith("0002-issue.md"):
            raise OSError("disk full")
        return real_write(self, content, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", flaky_write)

    data = orch.read_progress()
    orch.process_rfc_files(data)

    out = capsys.readouterr().out
    assert "could not update" in out or "OSError" in out or "disk full" in out, \
        f"silent OSError swallow not fixed; stdout: {out[-500:]}"


# ---------------------------------------------------------------------------
# M14.3 (MED): _run_release — push failure should log distinctly
# ---------------------------------------------------------------------------

def test_run_release_logs_push_failure_at_error_level(import_orch, project_root, monkeypatch, capsys):
    """When auto_push_tags fails, the log message must distinguish push from tag."""
    orch = import_orch

    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "config.json").write_text(json.dumps({
        "pipeline": {"auto_tag": True, "auto_push_tags": True},
    }), encoding="utf-8")

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "tag"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "push"]:
            raise subprocess.CalledProcessError(1, cmd, "", "remote rejected")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(orch.subprocess, "run", fake_run)
    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "release notes\nVERDICT: RELEASE_NOTED\n")

    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0", "version": 1, "status": "in_progress",
        "epics": [{"id": "EPIC-x", "stories": [{
            "id": "STORY-x", "status": "completed",
            "title": "Story", "artifacts": {
                "commit_hash": "abc1234567",
                "implementation_files": ["src/foo.py"],
                "test_files": [],
            },
        }]}],
        "sprints": [],
    }), encoding="utf-8")

    data = orch.read_progress()
    sprint = {
        "number": 1, "goal": "Test", "story_ids": ["STORY-x"],
        "status": "completed", "velocity_points": 1,
    }
    orch._run_release(data, sprint)

    out = capsys.readouterr().out
    assert "push" in out.lower(), f"push failure not mentioned; stdout: {out}"
    push_lines = [line for line in out.splitlines() if "push" in line.lower()]
    severe = ("✗", "ERROR", "error", "failed")
    assert any(any(w in line for w in severe) for line in push_lines), \
        f"push failure should be logged at error severity: {push_lines}"


# ---------------------------------------------------------------------------
# M14.4 (MED): with_retry on_retry exception must not be swallowed silently
# ---------------------------------------------------------------------------

def test_with_retry_logs_swallowed_on_retry_exception(capsys):
    """A broken on_retry hook must not vanish without a trace."""
    from retry import RetryPolicy, with_retry

    class _Transient(Exception):
        pass

    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _Transient("retry me")
        return "ok"

    def bad_hook(attempt, exc, delay):
        raise RuntimeError("hook is broken")

    import io, sys
    old_stderr = sys.stderr
    captured_stderr = io.StringIO()
    sys.stderr = captured_stderr
    try:
        result = with_retry(
            fn,
            is_transient=lambda e: isinstance(e, _Transient),
            policy=RetryPolicy(max_attempts=3),
            on_retry=bad_hook,
            sleep_fn=lambda _: None,
        )
    finally:
        sys.stderr = old_stderr

    assert result == "ok"
    cap = capsys.readouterr()
    combined = captured_stderr.getvalue() + cap.out + cap.err
    assert "hook" in combined.lower() or "RuntimeError" in combined, \
        f"on_retry exception silently swallowed; output: {combined!r}"


# ---------------------------------------------------------------------------
# M14.5 (LOW): cmd_new must check git init return code
# ---------------------------------------------------------------------------

def test_cmd_new_dies_when_git_init_fails(import_orch, project_root, monkeypatch):
    """Failed git init must surface immediately, not be masked by later init.sh failure."""
    orch = import_orch

    aa_home = project_root / "aa-home"
    aa_home.mkdir()
    (aa_home / "init.sh").write_text("#!/bin/bash\n")
    monkeypatch.setenv("AA_HOME", str(aa_home))

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "init"]:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: cannot init")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(orch.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda *a: "n")

    with pytest.raises(SystemExit) as exc_info:
        orch.cmd_new(argparse.Namespace(
            name="failgit",
            runner="claude",
            idea=None,
            interactive=False,
        ))
    assert exc_info.value.code != 0
