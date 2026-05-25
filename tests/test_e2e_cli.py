"""End-to-end CLI tests via real subprocess invocation (M13.7).

These tests spawn `python3 lib/orchestrator.py <subcommand>` for real, with
NONINTERACTIVE=1 so the wizards return defaults instead of blocking on input.
Verifies exit codes, stdout snippets, and side effects on the filesystem.

Complements tests/test_e2e_smoke.py (which covers `develop --dry-run` /
`validate`) by exercising the new M11+M12 wizard subcommands.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = REPO_ROOT / "lib" / "orchestrator.py"


def _run(args: list[str], cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Spawn `python3 lib/orchestrator.py <args>` with NONINTERACTIVE=1."""
    env = os.environ.copy()
    env["NONINTERACTIVE"] = "1"
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR)] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


@pytest.mark.e2e
def test_cli_health_check_in_clean_dir_exits_nonzero(project_root: Path):
    """In an empty dir (no agents, no config, no git), health-check fails fast."""
    result = _run(["health-check"], cwd=project_root)
    assert result.returncode != 0, f"expected non-zero, stdout: {result.stdout}"
    assert "Health check" in result.stdout
    assert "✗" in result.stdout  # at least one failed check


@pytest.mark.e2e
def test_cli_wizard_not_initialized_prints_bootstrap_hint(project_root: Path):
    """In an empty dir, wizard suggests running init.sh — NONINTERACTIVE picks default (Exit)."""
    result = _run(["wizard"], cwd=project_root)
    assert "not_initialized" in result.stdout
    assert "init.sh" in result.stdout
    # NONINTERACTIVE mode auto-selects defaults; just verify it exits cleanly
    assert result.returncode in (0, 1)


@pytest.mark.e2e
def test_cli_help_lists_all_15_subcommands(project_root: Path):
    """`--help` shows the full subcommand surface (regression guard)."""
    result = _run(["--help"], cwd=project_root)
    assert result.returncode == 0
    expected = [
        "develop", "resume", "status", "validate",
        "health-check", "setup", "wizard", "new",
        "revisit", "discover", "sprint", "adr",
        "refine", "rfc", "agent",
    ]
    for name in expected:
        assert name in result.stdout, f"missing subcommand in --help: {name}"


@pytest.mark.e2e
def test_cli_setup_in_clean_env_exits_with_code(project_root: Path):
    """`setup` runs against the test machine and reports per-check ✓/✗ rows."""
    result = _run(["setup"], cwd=project_root)
    assert "one-time machine setup" in result.stdout
    assert "Python" in result.stdout
    assert "git" in result.stdout
    assert result.returncode in (0, 1)


@pytest.mark.e2e
def test_cli_unknown_subcommand_exits_nonzero(project_root: Path):
    """argparse should reject unknown subcommands with non-zero."""
    result = _run(["bogus-command"], cwd=project_root)
    assert result.returncode != 0
    assert "invalid choice" in result.stderr.lower() or "usage" in result.stderr.lower()
