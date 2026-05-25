"""Regression tests for ClaudeCodeRunner._resolve_agents_dir.

Guards the fix for the `aa-orchestrator new --interactive` bug where a fresh
project's `.opencode/agents/` is empty, so the runner has to fall back to the
global `~/.config/opencode/agents/` install location.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _write_agent(agents_dir: Path, name: str, body: str = "body") -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        f"---\ndescription: {name}\npermission:\n  edit: deny\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_resolve_agents_dir_prefers_project_local(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner

    project_agents = tmp_path / "project" / ".opencode" / "agents"
    global_agents = tmp_path / "home" / ".config" / "opencode" / "agents"
    _write_agent(project_agents, "discover", body="LOCAL")
    _write_agent(global_agents, "discover", body="GLOBAL")

    monkeypatch.setenv("OPENCODE_HOME", str(tmp_path / "home" / ".config" / "opencode"))
    runner = ClaudeCodeRunner(agents_dir=project_agents)
    assert runner._resolve_agents_dir("discover") == project_agents


def test_resolve_agents_dir_falls_back_to_global_when_project_empty(tmp_path, monkeypatch):
    """The new --interactive bug: project-local dir empty, global has the file."""
    from runners import ClaudeCodeRunner

    project_agents = tmp_path / "project" / ".opencode" / "agents"
    project_agents.mkdir(parents=True)  # exists but empty
    global_agents = tmp_path / "home" / ".config" / "opencode" / "agents"
    _write_agent(global_agents, "discover", body="GLOBAL")

    monkeypatch.setenv("OPENCODE_HOME", str(tmp_path / "home" / ".config" / "opencode"))
    runner = ClaudeCodeRunner(agents_dir=project_agents)
    assert runner._resolve_agents_dir("discover") == global_agents


def test_resolve_agents_dir_falls_back_when_project_dir_missing(tmp_path, monkeypatch):
    """Project doesn't even have .opencode/agents/ yet — still find the agent globally."""
    from runners import ClaudeCodeRunner

    project_agents = tmp_path / "project" / ".opencode" / "agents"  # deliberately missing
    global_agents = tmp_path / "home" / ".config" / "opencode" / "agents"
    _write_agent(global_agents, "discover")

    monkeypatch.setenv("OPENCODE_HOME", str(tmp_path / "home" / ".config" / "opencode"))
    runner = ClaudeCodeRunner(agents_dir=project_agents)
    assert runner._resolve_agents_dir("discover") == global_agents


def test_resolve_agents_dir_uses_default_home_when_no_env(tmp_path, monkeypatch):
    """When OPENCODE_HOME is unset, ~/.config/opencode is the default."""
    from runners import ClaudeCodeRunner

    fake_home = tmp_path / "fakehome"
    global_agents = fake_home / ".config" / "opencode" / "agents"
    _write_agent(global_agents, "discover")

    monkeypatch.delenv("OPENCODE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    runner = ClaudeCodeRunner(agents_dir=tmp_path / "project" / ".opencode" / "agents")
    assert runner._resolve_agents_dir("discover") == global_agents


def test_resolve_agents_dir_returns_primary_when_neither_exists(tmp_path, monkeypatch):
    """When the agent isn't in either location, return primary so parse_agent_file raises with the project-local path."""
    from runners import ClaudeCodeRunner

    project_agents = tmp_path / "project" / ".opencode" / "agents"
    monkeypatch.setenv("OPENCODE_HOME", str(tmp_path / "empty-home"))
    runner = ClaudeCodeRunner(agents_dir=project_agents)
    assert runner._resolve_agents_dir("nonexistent") == project_agents


def test_opencode_use_pty_env_var_disables_pty(monkeypatch):
    """OPENCODE_USE_PTY=false should set use_pty=False (workaround for OpenCode 1.15.x)."""
    from runners import OpenCodeRunner
    for falsy in ("0", "false", "FALSE", "no", "off", "No"):
        monkeypatch.setenv("OPENCODE_USE_PTY", falsy)
        assert OpenCodeRunner().use_pty is False, f"failed for OPENCODE_USE_PTY={falsy!r}"


def test_opencode_use_pty_env_var_keeps_pty_when_truthy(monkeypatch):
    """M20: only explicit truthy strings opt PTY back on; anything else defaults to False."""
    from runners import OpenCodeRunner
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("OPENCODE_USE_PTY", truthy)
        assert OpenCodeRunner().use_pty is True, f"failed for OPENCODE_USE_PTY={truthy!r}"


def test_opencode_use_pty_explicit_arg_overrides_env(monkeypatch):
    """Explicit use_pty= constructor argument wins over the env var."""
    from runners import OpenCodeRunner
    monkeypatch.setenv("OPENCODE_USE_PTY", "false")
    assert OpenCodeRunner(use_pty=True).use_pty is True
    monkeypatch.setenv("OPENCODE_USE_PTY", "true")
    assert OpenCodeRunner(use_pty=False).use_pty is False


def test_opencode_use_pty_default_now_false(monkeypatch):
    """M20: with no env var and no kwarg, the default is now False (was True pre-M20).

    Reason: OpenCode 1.15.x returns empty stdout when stdout is a PTY pipe,
    which makes every agent invocation fail. Safer default is non-PTY mode;
    users on a working OpenCode build opt back in via OPENCODE_USE_PTY=true.
    """
    from runners import OpenCodeRunner
    monkeypatch.delenv("OPENCODE_USE_PTY", raising=False)
    assert OpenCodeRunner().use_pty is False


def test_opencode_use_pty_empty_string_keeps_default(monkeypatch):
    """`export OPENCODE_USE_PTY=` (empty) treated like unset — falls back to the new default (False)."""
    from runners import OpenCodeRunner
    monkeypatch.setenv("OPENCODE_USE_PTY", "")
    assert OpenCodeRunner().use_pty is False


def test_opencode_use_pty_unrecognized_value_falls_to_default(monkeypatch):
    """Arbitrary strings (e.g. 'maybe') don't count as truthy — they fall back to the False default."""
    from runners import OpenCodeRunner
    monkeypatch.setenv("OPENCODE_USE_PTY", "anything-else")
    assert OpenCodeRunner().use_pty is False


def test_resolve_agents_dir_with_nonexistent_opencode_home(tmp_path, monkeypatch):
    """OPENCODE_HOME pointing to a missing directory falls back to primary gracefully."""
    from runners import ClaudeCodeRunner

    project_agents = tmp_path / "project" / ".opencode" / "agents"
    monkeypatch.setenv("OPENCODE_HOME", str(tmp_path / "does" / "not" / "exist"))
    runner = ClaudeCodeRunner(agents_dir=project_agents)
    # No agent file anywhere → returns primary so parse_agent_file raises with the right path
    assert runner._resolve_agents_dir("any-agent") == project_agents


def test_resolve_agents_dir_when_fallback_target_is_a_file_not_directory(tmp_path, monkeypatch):
    """If $OPENCODE_HOME/agents is a regular file (misconfigured), don't crash — fall back to primary."""
    from runners import ClaudeCodeRunner

    fake_opencode = tmp_path / "broken-opencode"
    fake_opencode.mkdir()
    # Create `agents` as a FILE instead of a directory
    (fake_opencode / "agents").write_text("not a dir", encoding="utf-8")

    monkeypatch.setenv("OPENCODE_HOME", str(fake_opencode))
    project_agents = tmp_path / "project" / ".opencode" / "agents"
    runner = ClaudeCodeRunner(agents_dir=project_agents)
    # `(file_path / "discover.md").exists()` returns False for a non-dir parent → falls back to primary
    assert runner._resolve_agents_dir("discover") == project_agents


def test_run_uses_fallback_dir_end_to_end(tmp_path, monkeypatch):
    """ClaudeCodeRunner.run() should succeed when only the global agents dir has the file."""
    if not shutil.which("bash"):
        pytest.skip("needs bash")

    from runners import ClaudeCodeRunner

    project_agents = tmp_path / "project" / ".opencode" / "agents"  # missing
    global_agents = tmp_path / "home" / ".config" / "opencode" / "agents"
    _write_agent(global_agents, "fake-agent", body="fake body")

    monkeypatch.setenv("OPENCODE_HOME", str(tmp_path / "home" / ".config" / "opencode"))

    runner = ClaudeCodeRunner(agent_cmd="bash -c", agents_dir=project_agents)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok from fallback\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    out = runner.run("fake-agent", "hi", timeout=5)
    assert "ok from fallback" in out
    assert any("fake body" in str(arg) for arg in captured["cmd"])
