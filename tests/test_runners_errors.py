"""M15.6: runner error paths + flag matrix + skill injection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _write_agent(agents_dir: Path, name: str, **perms) -> None:
    edit = "allow" if perms.get("edit", False) else "deny"
    write = "allow" if perms.get("write", False) else "deny"
    webfetch = "allow" if perms.get("webfetch", False) else "deny"
    websearch = "allow" if perms.get("websearch", False) else "deny"
    bash_allow = perms.get("bash_allow", [])
    bash_deny = perms.get("bash_deny", [])
    skills = perms.get("skills", [])
    imports = perms.get("imports", [])

    fm = [
        "---",
        f"description: {name} agent",
        "permission:",
        f"  edit: {edit}",
        f"  write: {write}",
        f"  webfetch: {webfetch}",
        f"  websearch: {websearch}",
        "  bash:",
    ]
    for p in bash_allow:
        fm.append(f'    "{p}": allow')
    for p in bash_deny:
        fm.append(f'    "{p}": deny')
    if skills:
        fm.append("skills:")
        for s in skills:
            fm.append(f"  - id: {s['id']}")
            fm.append(f"    description: {s.get('description', '')}")
    if imports:
        fm.append(f"imports: [{', '.join(imports)}]")
    fm.append("---")
    fm.append("")
    fm.append(f"Body of @{name}.")
    (agents_dir / f"{name}.md").write_text("\n".join(fm), encoding="utf-8")


# ---------------------------------------------------------------------------
# OpenCodeRunner._run_simple error paths
# ---------------------------------------------------------------------------

def test_opencode_run_simple_file_not_found(tmp_path, monkeypatch):
    from runners import OpenCodeRunner, AgentRunnerError
    runner = OpenCodeRunner(agent_cmd="opencode-bin run --agent", use_pty=False)

    def fake_run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AgentRunnerError) as exc_info:
        runner.run("eng", "prompt", timeout=5)
    assert "runner binary not found" in str(exc_info.value)


def test_opencode_run_simple_timeout(tmp_path, monkeypatch):
    from runners import OpenCodeRunner, AgentRunnerError
    runner = OpenCodeRunner(agent_cmd="opencode-bin run --agent", use_pty=False)

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 5))
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AgentRunnerError) as exc_info:
        runner.run("eng", "prompt", timeout=5)
    assert "timeout after 5s" in str(exc_info.value)


def test_opencode_run_simple_nonzero_exit(tmp_path, monkeypatch):
    from runners import OpenCodeRunner, AgentRunnerError
    runner = OpenCodeRunner(agent_cmd="opencode-bin run --agent", use_pty=False)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 7, stdout="some out", stderr="err msg")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AgentRunnerError) as exc_info:
        runner.run("eng", "prompt", timeout=5)
    assert "non-zero exit (7)" in str(exc_info.value)
    assert "err msg" in str(exc_info.value)


def test_opencode_run_simple_empty_stdout(tmp_path, monkeypatch):
    from runners import OpenCodeRunner, AgentRunnerError
    runner = OpenCodeRunner(agent_cmd="opencode-bin run --agent", use_pty=False)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="   ", stderr="warning text")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AgentRunnerError) as exc_info:
        runner.run("eng", "prompt", timeout=5)
    assert "empty stdout" in str(exc_info.value)


def test_opencode_run_simple_success_returns_stdout(tmp_path, monkeypatch):
    from runners import OpenCodeRunner
    runner = OpenCodeRunner(agent_cmd="opencode-bin run --agent", use_pty=False)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="hello world\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    out = runner.run("eng", "prompt", timeout=5)
    assert "hello world" in out


# ---------------------------------------------------------------------------
# ClaudeCodeRunner error paths
# ---------------------------------------------------------------------------

def test_claude_runner_file_not_found(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner, AgentRunnerError
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng")

    runner = ClaudeCodeRunner(agent_cmd="claude-missing-bin", agents_dir=agents)

    def fake_run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AgentRunnerError) as exc_info:
        runner.run("eng", "prompt", timeout=5)
    assert "claude CLI not found" in str(exc_info.value)


def test_claude_runner_timeout(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner, AgentRunnerError
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng")

    runner = ClaudeCodeRunner(agent_cmd="claude", agents_dir=agents)

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AgentRunnerError) as exc_info:
        runner.run("eng", "prompt", timeout=5)
    assert "timeout after 5s" in str(exc_info.value)


def test_claude_runner_nonzero_exit(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner, AgentRunnerError
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng")

    runner = ClaudeCodeRunner(agent_cmd="claude", agents_dir=agents)
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, stdout="", stderr="fail")
    )
    with pytest.raises(AgentRunnerError) as exc_info:
        runner.run("eng", "prompt", timeout=5)
    assert "non-zero exit (3)" in str(exc_info.value)


def test_claude_runner_empty_stdout(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner, AgentRunnerError
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng")

    runner = ClaudeCodeRunner(agent_cmd="claude", agents_dir=agents)
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="  \n", stderr="hint")
    )
    with pytest.raises(AgentRunnerError) as exc_info:
        runner.run("eng", "prompt", timeout=5)
    assert "empty stdout" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Claude permission flag matrix
# ---------------------------------------------------------------------------

def test_claude_runner_edit_allow_appends_acceptedits_mode(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng", edit=True)

    runner = ClaudeCodeRunner(agent_cmd="claude", agents_dir=agents)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner.run("eng", "prompt", timeout=5)
    assert "--permission-mode" in captured["cmd"]
    assert "acceptEdits" in captured["cmd"]


def test_claude_runner_edit_deny_disallows_edit(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng", edit=False, write=False)

    runner = ClaudeCodeRunner(agent_cmd="claude", agents_dir=agents)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner.run("eng", "prompt", timeout=5)
    cmd_str = " ".join(captured["cmd"])
    assert "Edit" in cmd_str
    assert "Write" in cmd_str
    assert "--disallowedTools" in captured["cmd"]


def test_claude_runner_bash_allow_appears(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng", edit=False, bash_allow=["ls *", "git status"])

    runner = ClaudeCodeRunner(agent_cmd="claude", agents_dir=agents)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner.run("eng", "prompt", timeout=5)
    cmd_str = " ".join(captured["cmd"])
    assert "Bash(ls *)" in cmd_str
    assert "Bash(git status)" in cmd_str


def test_claude_runner_bash_deny_skipping_wildcard(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng", bash_deny=["*", "rm -rf *"])

    runner = ClaudeCodeRunner(agent_cmd="claude", agents_dir=agents)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner.run("eng", "prompt", timeout=5)
    cmd_str = " ".join(captured["cmd"])
    assert "Bash(rm -rf *)" in cmd_str
    assert "Bash(*)" not in cmd_str


def test_claude_runner_webfetch_websearch_default_denied(tmp_path, monkeypatch):
    from runners import ClaudeCodeRunner
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng", webfetch=False, websearch=False)

    runner = ClaudeCodeRunner(agent_cmd="claude", agents_dir=agents)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner.run("eng", "prompt", timeout=5)
    cmd_str = " ".join(captured["cmd"])
    assert "WebFetch" in cmd_str
    assert "WebSearch" in cmd_str


# ---------------------------------------------------------------------------
# _prepend_skill_context paths
# ---------------------------------------------------------------------------

def test_prepend_skill_context_uses_inline_skill(tmp_path):
    from runners import _prepend_skill_context, parse_agent_file
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng", skills=[
        {"id": "fix-bug", "description": "Find and fix a bug in given files"},
    ])
    agent = parse_agent_file("eng", agents_dir=agents)
    out = _prepend_skill_context("user prompt body", agent, "fix-bug")
    assert "Skill: fix-bug" in out
    assert "Find and fix a bug" in out
    assert "user prompt body" in out


def test_prepend_skill_context_undeclared_skill_emits_warning(tmp_path):
    from runners import _prepend_skill_context, parse_agent_file
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng")
    agent = parse_agent_file("eng", agents_dir=agents)
    out = _prepend_skill_context("user prompt body", agent, "unknown-skill")
    assert "not declared" in out
    assert "user prompt body" in out


def test_resolve_skill_for_agent_inline_first(tmp_path):
    from runners import resolve_skill_for_agent, parse_agent_file
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng", skills=[{"id": "review-code", "description": "Review"}])
    agent = parse_agent_file("eng", agents_dir=agents)
    inline, skill_file = resolve_skill_for_agent(agent, "review-code")
    assert inline is not None
    assert inline.id == "review-code"
    assert skill_file is None


def test_resolve_skill_for_agent_refuses_when_not_imported(tmp_path):
    from runners import resolve_skill_for_agent, parse_agent_file
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent(agents, "eng")
    agent = parse_agent_file("eng", agents_dir=agents)
    inline, skill_file = resolve_skill_for_agent(agent, "some-global-skill")
    assert inline is None
    assert skill_file is None


# ---------------------------------------------------------------------------
# parse_agent_file — block-style imports + edge cases
# ---------------------------------------------------------------------------

def test_parse_agent_file_block_style_imports(tmp_path):
    from runners import parse_agent_file
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "b.md").write_text(
        "---\n"
        "description: agent with block imports\n"
        "imports:\n"
        "  - skill-one\n"
        "  - skill-two\n"
        "  - \"skill-three\"\n"
        "permission:\n"
        "  edit: deny\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    agent = parse_agent_file("b", agents_dir=agents)
    assert set(agent.imports) == {"skill-one", "skill-two", "skill-three"}
