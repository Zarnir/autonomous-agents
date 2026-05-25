"""Unit tests for lib/runners.py.

Tests the agent-file parser and the auto-selection logic. The actual subprocess
invocation of `claude` / `opencode` is monkeypatched so these tests do not
require the binaries to be installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runners import (
    ClaudeCodeRunner,
    OpenCodeRunner,
    _split_frontmatter,
    parse_agent_file,
    select_runner,
)


# ---------------------------------------------------------------------------
# Frontmatter splitter
# ---------------------------------------------------------------------------

def test_split_frontmatter_basic():
    text = "---\nfoo: bar\n---\nbody here\n"
    fm, body = _split_frontmatter(text)
    assert fm == "foo: bar"
    assert body == "body here\n"


def test_split_frontmatter_no_frontmatter():
    fm, body = _split_frontmatter("just body\n")
    assert fm == ""
    assert body == "just body\n"


def test_split_frontmatter_unclosed():
    fm, _ = _split_frontmatter("---\nfoo: bar\nno close\n")
    assert fm == ""


# ---------------------------------------------------------------------------
# Agent file parser
# ---------------------------------------------------------------------------

def test_parse_agent_file_extracts_description_and_body(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "myagent.md").write_text(
        """---
description: Test agent for unit tests
mode: all
permission:
  edit: allow
  write: deny
  bash:
    "ls *": allow
    "rm *": deny
  webfetch: deny
---

You are a test agent. Your job is X.
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("myagent", agents_dir=agents)
    assert agent.name == "myagent"
    assert "Test agent" in agent.description
    assert "You are a test agent" in agent.system_prompt
    assert agent.edit_allowed is True
    assert agent.write_allowed is False
    assert "ls *" in agent.bash_allow
    assert "rm *" in agent.bash_deny
    assert agent.webfetch_allowed is False


def test_parse_agent_file_real_make_agent():
    """Smoke against the real make.md from this repo — catches format drift."""
    repo_agents = Path(__file__).resolve().parent.parent / ".opencode" / "agents"
    agent = parse_agent_file("make", agents_dir=repo_agents)
    assert agent.name == "make"
    assert agent.system_prompt
    assert agent.bash_allow


def test_parse_agent_file_raises_on_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_agent_file("nonexistent", agents_dir=tmp_path)


# ---------------------------------------------------------------------------
# select_runner — auto-detect + explicit override
# ---------------------------------------------------------------------------

def test_select_runner_explicit_opencode():
    assert isinstance(select_runner("opencode"), OpenCodeRunner)


def test_select_runner_explicit_claude():
    assert isinstance(select_runner("claude"), ClaudeCodeRunner)


def test_select_runner_rejects_unknown():
    with pytest.raises(ValueError):
        select_runner("notreal")


def test_select_runner_env_opencode(monkeypatch):
    monkeypatch.setenv("AA_RUNNER", "opencode")
    monkeypatch.delenv("OPENCODE_AGENT_CMD", raising=False)
    assert isinstance(select_runner(), OpenCodeRunner)


def test_select_runner_env_claude(monkeypatch):
    monkeypatch.setenv("AA_RUNNER", "claude")
    assert isinstance(select_runner(), ClaudeCodeRunner)


def test_select_runner_opencode_agent_cmd_implies_opencode(monkeypatch):
    monkeypatch.delenv("AA_RUNNER", raising=False)
    monkeypatch.setenv("OPENCODE_AGENT_CMD", "opencode run --agent")
    assert isinstance(select_runner(), OpenCodeRunner)


# ---------------------------------------------------------------------------
# ClaudeCodeRunner command-line construction (no real binary invoked)
# ---------------------------------------------------------------------------

class _FakeProc:
    returncode = 0
    stdout = "OK\n"
    stderr = ""


def test_claude_runner_constructs_command_with_system_prompt(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "x.md").write_text(
        """---
description: test
permission:
  edit: allow
  write: allow
  bash:
    "ls *": allow
---

You are X.
""",
        encoding="utf-8",
    )

    captured: dict = {}
    import subprocess as _sub

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(_sub, "run", fake_run)
    runner = ClaudeCodeRunner(agents_dir=agents)
    runner.run("x", "hello prompt", timeout=10)

    cmd = captured["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert "--append-system-prompt" in cmd
    assert "You are X." in cmd[cmd.index("--append-system-prompt") + 1]
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert "Edit" in allowed
    assert "Write" in allowed
    assert "Bash(ls *)" in allowed


def test_claude_runner_passes_explicit_model(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "x.md").write_text(
        "---\ndescription: t\n---\n\nbody\n", encoding="utf-8"
    )
    captured: dict = {}
    import subprocess as _sub

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(_sub, "run", fake_run)
    runner = ClaudeCodeRunner(agents_dir=agents)
    runner.run("x", "p", timeout=10, model="claude-haiku-4-5-20251001")
    assert "--model" in captured["cmd"]
    assert "claude-haiku-4-5-20251001" in captured["cmd"]


def test_opencode_runner_passes_explicit_model(tmp_path, monkeypatch):
    """OpenCodeRunner should pass --model when given a model."""
    runner = OpenCodeRunner(use_pty=False)
    captured: dict = {}
    import subprocess as _sub

    class FakeProc:
        returncode = 0
        stdout = "OK"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(_sub, "run", fake_run)
    runner.run("make", "p", timeout=10, model="claude-haiku-4-5-20251001")
    assert "--model" in captured["cmd"]
    assert "claude-haiku-4-5-20251001" in captured["cmd"]


def test_claude_runner_propagates_cwd(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "x.md").write_text(
        "---\ndescription: t\n---\n\nbody\n", encoding="utf-8"
    )
    captured: dict = {}
    import subprocess as _sub

    def fake_run(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(_sub, "run", fake_run)
    runner = ClaudeCodeRunner(agents_dir=agents)
    runner.run("x", "p", timeout=10, cwd="/tmp/somewhere")
    assert captured["kwargs"]["cwd"] == "/tmp/somewhere"
