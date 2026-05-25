"""E2E + shell-script tests for the bootstrap pipeline (M18).

Covers gaps that lib/-unit tests can't reach:
- install.sh agent + init.sh staging
- init.sh agent-copy block (lines 145-162 in init.sh)
- init.sh slash-command conditionals (lines 137-139)
- install.sh --update actually re-copies init.sh
- Full new → discover (stubbed) → develop --dry-run → status flow

Run with: pytest tests/test_bootstrap_e2e.py -v
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = REPO_ROOT / "lib" / "orchestrator.py"
INSTALL_SH = REPO_ROOT / "install.sh"
INIT_SH = REPO_ROOT / "init.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_bash(script: Path, args: list[str], cwd: Path, env_extra: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(script)] + args,
        cwd=str(cwd), capture_output=True, text=True, timeout=30, env=env,
    )


def _aa_home_with_real_templates(tmp_path: Path) -> Path:
    """Build a fake AA_HOME by running install.sh into tmp_path. Returns AA_HOME path."""
    aa_home = tmp_path / "aa_home"
    aa_bin = tmp_path / "aa_bin"
    opencode_home = tmp_path / "opencode_home"
    env = {
        "AA_HOME": str(aa_home),
        "AA_BIN": str(aa_bin),
        "OPENCODE_HOME": str(opencode_home),
    }
    res = _run_bash(INSTALL_SH, [], REPO_ROOT, env)
    assert res.returncode == 0, f"install.sh failed: {res.stderr}\nstdout: {res.stdout}"
    return aa_home


# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------

def test_install_sh_stages_init_sh_in_aa_home(tmp_path):
    """install.sh must copy init.sh into AA_HOME so users can re-init projects."""
    aa_home = _aa_home_with_real_templates(tmp_path)
    assert (aa_home / "init.sh").exists()
    assert (aa_home / "init.sh").read_bytes() == INIT_SH.read_bytes()


def test_install_sh_stages_all_five_opencode_slash_commands(tmp_path):
    """install.sh must stage all 5 OpenCode slash command templates (not just develop+resume)."""
    aa_home = _aa_home_with_real_templates(tmp_path)
    templates = aa_home / "templates"
    for name in ("develop.md", "resume.md", "discover.md", "revisit.md", "sprint.md"):
        assert (templates / name).exists(), f"missing template: {name}"


def test_install_sh_update_re_copies_init_sh(tmp_path):
    """install.sh --update must re-copy init.sh from source (catches stale-install bug)."""
    aa_home = _aa_home_with_real_templates(tmp_path)
    # Corrupt the installed init.sh to simulate stale state
    (aa_home / "init.sh").write_text("# stale stub\n", encoding="utf-8")
    assert (aa_home / "init.sh").read_text() == "# stale stub\n"

    env = {
        "AA_HOME": str(aa_home),
        "AA_BIN": str(tmp_path / "aa_bin"),
        "OPENCODE_HOME": str(tmp_path / "opencode_home"),
    }
    res = _run_bash(INSTALL_SH, ["--update"], REPO_ROOT, env)
    assert res.returncode == 0, f"install.sh --update failed: {res.stderr}"
    assert (aa_home / "init.sh").read_bytes() == INIT_SH.read_bytes()


def test_install_sh_uninstall_removes_everything(tmp_path):
    aa_home = _aa_home_with_real_templates(tmp_path)
    aa_bin = tmp_path / "aa_bin"
    opencode_home = tmp_path / "opencode_home"

    assert aa_home.exists()
    assert (aa_bin / "aa-orchestrator").exists()

    res = _run_bash(INSTALL_SH, ["--uninstall"], REPO_ROOT, {
        "AA_HOME": str(aa_home),
        "AA_BIN": str(aa_bin),
        "OPENCODE_HOME": str(opencode_home),
    })
    assert res.returncode == 0
    assert not aa_home.exists()
    assert not (aa_bin / "aa-orchestrator").exists()


# ---------------------------------------------------------------------------
# init.sh — the agent-copy block + slash-command conditionals (M18 gap)
# ---------------------------------------------------------------------------

def test_init_sh_copies_agents_into_project(tmp_path):
    """init.sh must copy global agents into <project>/.opencode/agents/ (the fix from earlier)."""
    aa_home = _aa_home_with_real_templates(tmp_path)
    opencode_home = tmp_path / "opencode_home"
    project = tmp_path / "test_project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    res = _run_bash(INIT_SH, [], project, {
        "AA_HOME": str(aa_home),
        "OPENCODE_HOME": str(opencode_home),
    })
    assert res.returncode == 0, f"init.sh failed: {res.stderr}\nstdout: {res.stdout}"

    agents_dir = project / ".opencode" / "agents"
    assert agents_dir.exists(), ".opencode/agents/ should exist after init.sh"
    md_files = list(agents_dir.glob("*.md"))
    assert len(md_files) >= 15, f"expected ~19 agent files, got {len(md_files)}"
    # The specific agent that was missing in the reported bug
    assert (agents_dir / "discover.md").exists()


def test_init_sh_installs_all_five_slash_commands(tmp_path):
    """init.sh must install all 5 OpenCode slash command files into project (not just 2)."""
    aa_home = _aa_home_with_real_templates(tmp_path)
    project = tmp_path / "test_project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    res = _run_bash(INIT_SH, [], project, {
        "AA_HOME": str(aa_home),
        "OPENCODE_HOME": str(tmp_path / "opencode_home"),
    })
    assert res.returncode == 0

    commands = project / ".opencode" / "commands"
    for name in ("develop.md", "resume.md", "discover.md", "revisit.md", "sprint.md"):
        assert (commands / name).exists(), f"missing slash command: {name}"


def test_init_sh_force_overwrites_existing_agent(tmp_path):
    """init.sh --force must refresh project-local agent files (TDD gap from review)."""
    aa_home = _aa_home_with_real_templates(tmp_path)
    project = tmp_path / "test_project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    _run_bash(INIT_SH, [], project, {
        "AA_HOME": str(aa_home),
        "OPENCODE_HOME": str(tmp_path / "opencode_home"),
    })
    custom_marker = "# USER_CUSTOMIZATION_MARKER\n"
    (project / ".opencode" / "agents" / "discover.md").write_text(custom_marker)

    # Without --force: skip existing, keep customization
    _run_bash(INIT_SH, [], project, {
        "AA_HOME": str(aa_home),
        "OPENCODE_HOME": str(tmp_path / "opencode_home"),
    })
    assert custom_marker in (project / ".opencode" / "agents" / "discover.md").read_text(), \
        "init.sh without --force unexpectedly overwrote customized agent"

    # With --force: overwrite back to canonical
    res = _run_bash(INIT_SH, ["--force"], project, {
        "AA_HOME": str(aa_home),
        "OPENCODE_HOME": str(tmp_path / "opencode_home"),
    })
    assert res.returncode == 0
    assert custom_marker not in (project / ".opencode" / "agents" / "discover.md").read_text(), \
        "init.sh --force should have overwritten customized agent"


def test_init_sh_handles_missing_opencode_home_gracefully(tmp_path):
    """If OPENCODE_HOME doesn't exist, init.sh warns but doesn't crash."""
    aa_home = _aa_home_with_real_templates(tmp_path)
    project = tmp_path / "test_project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    res = _run_bash(INIT_SH, [], project, {
        "AA_HOME": str(aa_home),
        "OPENCODE_HOME": str(tmp_path / "ghost_opencode_home"),
    })
    assert res.returncode == 0
    combined = res.stdout + res.stderr   # init.sh `warn` writes to stderr
    assert "No agent source dir found" in combined or "Agents not found" in combined


# ---------------------------------------------------------------------------
# Full E2E flow: new → discover (stubbed) → develop --dry-run → status
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_e2e_bootstrap_then_discover_then_dry_run(tmp_path, monkeypatch):
    """The smoking-gun e2e test — full bootstrap flow exercising every layer."""
    aa_home = _aa_home_with_real_templates(tmp_path)
    opencode_home = tmp_path / "opencode_home"

    env_extra = {
        "AA_HOME": str(aa_home),
        "OPENCODE_HOME": str(opencode_home),
        "NONINTERACTIVE": "1",
        "OPENCODE_USE_PTY": "false",
    }

    # ---- Step 1: aa-orchestrator new
    parent = tmp_path / "workspace"
    parent.mkdir()
    env = os.environ.copy()
    env.update(env_extra)
    new_res = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "new", "myproj", "--runner", "opencode"],
        cwd=str(parent), capture_output=True, text=True, timeout=30, env=env,
    )
    proj = parent / "myproj"
    assert proj.exists(), f"new should have created the project dir. stdout: {new_res.stdout}"
    assert (proj / ".opencode" / "config.json").exists()
    assert (proj / ".opencode" / "commands" / "develop.md").exists()
    assert (proj / ".opencode" / "commands" / "sprint.md").exists()
    assert (proj / ".opencode" / "agents" / "discover.md").exists()

    # ---- Step 2: cmd_discover in-process with stubs
    monkeypatch.chdir(proj)
    import orchestrator as orch

    epics_dir = proj / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True, exist_ok=True)

    def fake_call_agent(name, prompt, **kw):
        if name == "discover":
            (epics_dir / "01-test.md").write_text(
                "---\nid: EPIC-test\ntitle: Test Epic\n---\n\n"
                "## Story: STORY-a\ntitle: A\n\n"
                "### Acceptance Criteria\n- [ ] AC1: this is a long enough criterion text\n\n"
                "### Tasks\n- [ ] TASK-impl `src/a.py` (create)\n",
                encoding="utf-8",
            )
            return "VERDICT: SPEC_WRITTEN\n"
        return ""
    monkeypatch.setattr(orch, "call_agent", fake_call_agent)

    rc = orch.cmd_discover(argparse.Namespace(
        idea="A test app for the e2e suite",
        target_dir=str(proj),
        then_develop=False,
        interactive=False,
    ))
    assert rc == 0, "cmd_discover should succeed when stub writes a valid spec"
    assert (epics_dir / "01-test.md").exists()

    # ---- Step 3: develop --dry-run
    dev_res = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "develop", "--dry-run"],
        cwd=str(proj), capture_output=True, text=True, timeout=30, env=env,
    )
    assert dev_res.returncode == 0, f"develop --dry-run failed: {dev_res.stderr}"
    progress_path = proj / ".opencode" / "progress.json"
    assert progress_path.exists()
    data = json.loads(progress_path.read_text())
    assert data["schema_version"] == "2.0"
    assert any(ep["id"] == "EPIC-test" for ep in data["epics"])

    # ---- Step 4: status
    status_res = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "status"],
        cwd=str(proj), capture_output=True, text=True, timeout=30, env=env,
    )
    assert status_res.returncode == 0
    assert "pending" in status_res.stdout.lower() or "story" in status_res.stdout.lower()
