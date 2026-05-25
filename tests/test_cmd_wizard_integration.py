"""Integration tests for cmd_setup, cmd_new, cmd_wizard, cmd_discover --interactive,
and `sprint cycle --interactive` (M13.1–M13.5).

These test the orchestration / branching of the wizard commands without invoking
real LLMs. `input()` is monkey-patched; `subprocess.run` is intercepted; agent
calls are stubbed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scripted_input(responses: list[str]):
    """Returns an input() replacement that yields the given responses in order."""
    it = iter(responses)
    def _input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError("scripted input exhausted")
    return _input


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    """Provide the orchestrator module with cwd pointed at tmp_path.

    CONFIG_FILE etc. are relative Paths that resolve against cwd at use time,
    so chdir is enough — no importlib.reload (which would break class identity
    for sibling tests' `pytest.raises(VersionConflict)`).
    """
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


# ---------------------------------------------------------------------------
# M13.1: cmd_setup (5 tests)
# ---------------------------------------------------------------------------

def test_cmd_setup_reports_success_on_clean_machine(import_orch, monkeypatch, capsys):
    """With all prerequisites in place, cmd_setup exits 0."""
    orch = import_orch

    # Mock Python version so the test passes regardless of the host interpreter
    fake_version = type("V", (), {"major": 3, "minor": 12, "micro": 0})()
    monkeypatch.setattr(orch.sys, "version_info", fake_version)

    def fake_subprocess(cmd, **kw):
        if cmd[:2] == ["git", "--version"]:
            return type("P", (), {"returncode": 0, "stdout": "git version 2.43.0\n", "stderr": ""})()
        if cmd[:3] == ["git", "config", "--global"]:
            field = cmd[3]
            return type("P", (), {
                "returncode": 0,
                "stdout": "test@example.com\n" if field == "user.email" else "Test User\n",
                "stderr": "",
            })()
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(orch.subprocess, "run", fake_subprocess)
    monkeypatch.setattr(orch.shutil, "which", lambda name: "/usr/local/bin/" + name if name in ("claude", "aa-orchestrator") else None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert "✓ Python" in out
    assert "✓ git version" in out
    assert "✓ git user" in out
    assert "✓ claude CLI" in out
    assert "✓ ANTHROPIC_API_KEY set" in out
    assert "✓ aa-orchestrator on PATH" in out
    assert "ready to `aa-orchestrator new" in out
    assert rc == 0


def test_cmd_setup_flags_missing_runner(import_orch, monkeypatch, capsys):
    """No claude AND no opencode on PATH → reports issue, exits 1."""
    orch = import_orch
    monkeypatch.setattr(orch.shutil, "which", lambda name: None)
    monkeypatch.setattr(orch.subprocess, "run", lambda *a, **kw: type("P", (), {"returncode": 0, "stdout": "git version 2.43\n", "stderr": ""})())
    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert "✗ neither claude nor opencode on PATH" in out
    assert "Install one of:" in out
    assert rc == 1


def test_cmd_setup_interactive_git_config_fix(import_orch, monkeypatch, capsys):
    """When git user.email/name are missing, the user can set them via prompts."""
    orch = import_orch

    git_state = {"email": "", "name": ""}

    def fake_subprocess(cmd, **kw):
        if cmd[:2] == ["git", "--version"]:
            return type("P", (), {"returncode": 0, "stdout": "git version 2.43\n", "stderr": ""})()
        if cmd[:4] == ["git", "config", "--global", "user.email"] and len(cmd) == 4:
            return type("P", (), {"returncode": 0, "stdout": git_state["email"] + "\n" if git_state["email"] else "\n", "stderr": ""})()
        if cmd[:4] == ["git", "config", "--global", "user.name"] and len(cmd) == 4:
            return type("P", (), {"returncode": 0, "stdout": git_state["name"] + "\n" if git_state["name"] else "\n", "stderr": ""})()
        if cmd[:4] == ["git", "config", "--global", "user.email"] and len(cmd) == 5:
            git_state["email"] = cmd[4]
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if cmd[:4] == ["git", "config", "--global", "user.name"] and len(cmd) == 5:
            git_state["name"] = cmd[4]
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(orch.subprocess, "run", fake_subprocess)
    monkeypatch.setattr(orch.shutil, "which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)
    monkeypatch.setattr("builtins.input", _scripted_input(["y", "alice@example.com", "Alice"]))

    orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert "✓ git user: Alice <alice@example.com>" in out
    assert git_state["email"] == "alice@example.com"
    assert git_state["name"] == "Alice"


def test_cmd_setup_reports_missing_aa_on_path(import_orch, monkeypatch, capsys):
    """If aa-orchestrator isn't on PATH, instruct the user how to add it."""
    orch = import_orch
    monkeypatch.setattr(orch.shutil, "which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(orch.subprocess, "run", lambda *a, **kw: type("P", (), {"returncode": 0, "stdout": "git version 2.43\n", "stderr": ""})())
    rc = orch.cmd_setup(argparse.Namespace())
    out = capsys.readouterr().out
    assert "✗ aa-orchestrator not on PATH" in out
    assert "export PATH" in out
    assert rc == 1


def test_cmd_setup_noninteractive_mode_does_not_prompt(import_orch, monkeypatch):
    """With NONINTERACTIVE=1, no input() call is allowed."""
    orch = import_orch
    monkeypatch.setenv("NONINTERACTIVE", "1")

    def boom(*_):
        raise AssertionError("input() should not be called in NONINTERACTIVE mode")

    monkeypatch.setattr("builtins.input", boom)
    monkeypatch.setattr(orch.shutil, "which", lambda n: "/bin/" + n)
    monkeypatch.setattr(orch.subprocess, "run", lambda *a, **kw: type("P", (), {"returncode": 0, "stdout": "git\n", "stderr": ""})())
    orch.cmd_setup(argparse.Namespace())


# ---------------------------------------------------------------------------
# M13.2: cmd_new (4 tests)
# ---------------------------------------------------------------------------

def test_cmd_new_rejects_directory_already_nonempty(import_orch, project_root, monkeypatch):
    """If target exists and has files, prompt + decline → exit 1."""
    orch = import_orch
    target = project_root / "preexisting"
    target.mkdir()
    (target / "junk.txt").write_text("x")
    monkeypatch.setattr("builtins.input", _scripted_input(["n"]))
    rc = orch.cmd_new(argparse.Namespace(name="preexisting", runner="claude", idea=None, interactive=False))
    assert rc == 1


def test_cmd_new_creates_directory_and_runs_init(import_orch, project_root, monkeypatch):
    """Happy path: validates name → mkdir → runs init.sh → no idea → exits 0."""
    orch = import_orch
    init_calls = []

    def fake_run(cmd, **kw):
        init_calls.append(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(orch.subprocess, "run", fake_run)
    aa_home = project_root / "aa-home"
    aa_home.mkdir()
    (aa_home / "init.sh").write_text("#!/bin/bash\necho init")
    monkeypatch.setenv("AA_HOME", str(aa_home))
    monkeypatch.setattr("builtins.input", _scripted_input(["n"]))

    rc = orch.cmd_new(argparse.Namespace(name="newproj", runner="claude", idea=None, interactive=False))
    assert rc == 0
    assert (project_root / "newproj").exists()
    init_sh_calls = [c for c in init_calls if "init.sh" in " ".join(str(p) for p in c)]
    assert init_sh_calls, f"init.sh was not invoked. Calls: {init_calls}"


def test_cmd_new_chains_into_discover_when_idea_given(import_orch, project_root, monkeypatch):
    """When --idea is provided, cmd_new calls cmd_discover."""
    orch = import_orch

    aa_home = project_root / "aa-home"
    aa_home.mkdir()
    (aa_home / "init.sh").write_text("#!/bin/bash\n")
    monkeypatch.setenv("AA_HOME", str(aa_home))
    monkeypatch.setattr(orch.subprocess, "run", lambda *a, **kw: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    discover_calls = []

    def fake_discover(args):
        discover_calls.append(args)
        return 0

    monkeypatch.setattr(orch, "cmd_discover", fake_discover)

    rc = orch.cmd_new(argparse.Namespace(
        name="ideaproj",
        runner="claude",
        idea="a great app",
        interactive=False,
    ))
    assert rc == 0
    assert len(discover_calls) == 1
    assert discover_calls[0].idea == "a great app"


def test_cmd_new_fails_if_init_sh_missing(import_orch, project_root, monkeypatch):
    """If $AA_HOME/init.sh is absent, die with a clear error."""
    orch = import_orch
    monkeypatch.setenv("AA_HOME", str(project_root / "nonexistent"))
    monkeypatch.setattr("builtins.input", _scripted_input(["n"]))
    with pytest.raises(SystemExit):
        orch.cmd_new(argparse.Namespace(name="x", runner="claude", idea=None, interactive=False))


# ---------------------------------------------------------------------------
# M13.3: cmd_wizard (3 tests)
# ---------------------------------------------------------------------------

def test_cmd_wizard_all_complete_exits_zero(import_orch, project_root, capsys):
    """When state is ALL_COMPLETE, wizard returns 0 without prompting."""
    orch = import_orch
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "status": "completed",
        "epics": [{"id": "E", "stories": [{"id": "S1", "status": "completed"}]}],
        "sprints": [{"number": 1, "status": "completed"}],
    }), encoding="utf-8")

    rc = orch.cmd_wizard(argparse.Namespace())
    out = capsys.readouterr().out
    assert "shipped" in out.lower() or "🎉" in out
    assert rc == 0


def test_cmd_wizard_open_rfcs_suggests_rfc_command(import_orch, project_root, monkeypatch, capsys):
    """Open RFCs → wizard suggests `aa-orchestrator rfc`."""
    orch = import_orch
    (project_root / ".opencode").mkdir()
    (project_root / ".opencode" / "progress.json").write_text(
        json.dumps({"status": "in_progress", "epics": [], "sprints": []}),
        encoding="utf-8",
    )
    rfc_dir = project_root / "docs" / "rfc"
    rfc_dir.mkdir(parents=True)
    (rfc_dir / "0001-x.md").write_text("# RFC\n\nStatus: open\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", _scripted_input(["3"]))

    rc = orch.cmd_wizard(argparse.Namespace())
    out = capsys.readouterr().out
    assert "open_rfcs" in out
    assert "aa-orchestrator rfc" in out
    assert rc == 0


def test_cmd_wizard_not_initialized_offers_bootstrap(import_orch, project_root, monkeypatch, capsys):
    """In an empty dir, wizard suggests running init.sh and exits cleanly when user picks Exit."""
    orch = import_orch
    monkeypatch.setattr("builtins.input", _scripted_input(["3"]))

    rc = orch.cmd_wizard(argparse.Namespace())
    out = capsys.readouterr().out
    assert "not_initialized" in out
    assert "init.sh" in out
    assert rc == 0


# ---------------------------------------------------------------------------
# M13.4: cmd_discover --interactive (1 test)
# ---------------------------------------------------------------------------

def test_cmd_discover_interactive_appends_clarifications_to_prompt(import_orch, project_root, monkeypatch):
    """The 3 clarifying answers must appear in the prompt passed to @discover."""
    orch = import_orch
    monkeypatch.chdir(project_root)
    (project_root / "docs" / "specs" / "epics").mkdir(parents=True)

    captured_prompts: list[str] = []

    def fake_call_agent(name, prompt, **kw):
        captured_prompts.append(prompt)
        return "Files written.\nVERDICT: SPEC_WRITTEN\n"

    monkeypatch.setattr(orch, "call_agent", fake_call_agent)

    class FakeReport:
        ok = True
        def render(self) -> str:
            return "OK"
    monkeypatch.setattr(orch, "validate_specs", lambda *a, **kw: FakeReport())

    monkeypatch.setattr("builtins.input", _scripted_input([
        "solo developer",
        "side project",
        "Python + Postgres",
    ]))

    rc = orch.cmd_discover(argparse.Namespace(
        idea="a todo app",
        target_dir=None,
        then_develop=False,
        interactive=True,
    ))
    assert rc == 0
    assert captured_prompts, "call_agent was never invoked"
    prompt = captured_prompts[0]
    assert "Primary user: solo developer" in prompt
    assert "Scale target: side project" in prompt
    assert "Tech-stack constraints: Python + Postgres" in prompt


# ---------------------------------------------------------------------------
# M13.5: sprint cycle --interactive (1 test)
# ---------------------------------------------------------------------------

def test_sprint_cycle_interactive_stop_exits_cleanly(import_orch, project_root, monkeypatch, capsys):
    """The --interactive `sprint cycle` exits 0 when user picks Stop between sprints."""
    orch = import_orch

    (project_root / ".opencode").mkdir()
    progress = {
        "schema_version": "2.0",
        "version": 1,
        "status": "in_progress",
        "epics": [{"id": "EPIC-x", "stories": [
            {"id": "STORY-a", "status": "pending", "depends_on": [], "execution_wave": 1,
             "estimated_complexity": "small", "acceptance_criteria": [], "tasks": [], "artifacts": {}},
            {"id": "STORY-b", "status": "pending", "depends_on": [], "execution_wave": 1,
             "estimated_complexity": "small", "acceptance_criteria": [], "tasks": [], "artifacts": {}},
        ]}],
        "sprints": [],
    }
    (project_root / ".opencode" / "progress.json").write_text(json.dumps(progress), encoding="utf-8")

    plan_calls = {"n": 0}
    start_calls = {"n": 0}
    end_calls = {"n": 0}

    def fake_plan():
        plan_calls["n"] += 1
        data = json.loads((project_root / ".opencode" / "progress.json").read_text())
        data["sprints"].append({"number": 1, "status": "planned", "story_ids": ["STORY-a"]})
        (project_root / ".opencode" / "progress.json").write_text(json.dumps(data))
        return 0

    def fake_start(args):
        start_calls["n"] += 1
        data = json.loads((project_root / ".opencode" / "progress.json").read_text())
        data["sprints"][-1]["status"] = "completed"
        data["sprints"][-1]["velocity_points"] = 1
        for s in data["epics"][0]["stories"]:
            if s["id"] == "STORY-a":
                s["status"] = "completed"
        (project_root / ".opencode" / "progress.json").write_text(json.dumps(data))
        return 0

    def fake_end():
        end_calls["n"] += 1
        return 0

    monkeypatch.setattr(orch, "_sprint_plan", fake_plan)
    monkeypatch.setattr(orch, "_sprint_start", fake_start)
    monkeypatch.setattr(orch, "_sprint_end", fake_end)
    monkeypatch.setattr(orch, "_run_backlog_groomer", lambda: None)

    monkeypatch.setattr("builtins.input", _scripted_input(["3"]))

    rc = orch._sprint_cycle(argparse.Namespace(interactive=True))
    out = capsys.readouterr().out
    assert "Sprint review" in out
    assert "paused" in out.lower() or rc == 0
    assert plan_calls["n"] == 1
    assert start_calls["n"] == 1
    assert end_calls["n"] == 1
