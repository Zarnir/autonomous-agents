"""M16.3: OpenCodeRunner._run_pty coverage with real bash subprocesses."""

from __future__ import annotations

import shutil

import pytest

from runners import OpenCodeRunner, AgentRunnerError


pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_run_pty_happy_path_returns_output():
    runner = OpenCodeRunner(agent_cmd="bash -c", use_pty=True)
    out = runner._run_pty(
        agent_name="bash-echo",
        cmd=["bash", "-c", "echo hello-from-pty"],
        prompt="ignored\n",
        timeout=10,
        cwd=None,
    )
    assert "hello-from-pty" in out


def test_run_pty_strips_ansi_escapes():
    runner = OpenCodeRunner(agent_cmd="bash -c", use_pty=True)
    out = runner._run_pty(
        agent_name="bash-ansi",
        cmd=["bash", "-c", r"printf '\x1b[31mRED\x1b[0m clean\n'"],
        prompt="x",
        timeout=10,
        cwd=None,
    )
    assert "RED" in out and "clean" in out
    assert "\x1b[" not in out


def test_run_pty_raises_on_missing_binary():
    runner = OpenCodeRunner(agent_cmd="nonexistent-binary-xyz", use_pty=True)
    with pytest.raises(AgentRunnerError) as exc_info:
        runner._run_pty(
            agent_name="missing",
            cmd=["nonexistent-binary-xyz"],
            prompt="x",
            timeout=5,
            cwd=None,
        )
    assert "runner binary not found" in str(exc_info.value)


def test_run_pty_raises_on_nonzero_exit():
    runner = OpenCodeRunner(agent_cmd="bash -c", use_pty=True)
    with pytest.raises(AgentRunnerError) as exc_info:
        runner._run_pty(
            agent_name="bash-fail",
            cmd=["bash", "-c", "echo some-stderr-output >&2; exit 7"],
            prompt="x",
            timeout=10,
            cwd=None,
        )
    assert "non-zero exit (7)" in str(exc_info.value)


def test_run_pty_raises_on_empty_output():
    runner = OpenCodeRunner(agent_cmd="bash -c", use_pty=True)
    with pytest.raises(AgentRunnerError) as exc_info:
        runner._run_pty(
            agent_name="bash-silent",
            cmd=["bash", "-c", "true"],
            prompt="x",
            timeout=10,
            cwd=None,
        )
    assert "empty output" in str(exc_info.value)
