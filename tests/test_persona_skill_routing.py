"""Unit tests for skill registry parsing + routing (M8.1 + M10.2)."""

from __future__ import annotations

from pathlib import Path

from runners import (
    AgentDef,
    ClaudeCodeRunner,
    OpenCodeRunner,
    Skill,
    _prepend_skill_context,
    check_skill_permissions,
    parse_agent_file,
    resolve_skill_for_agent,
)


def _write_persona(agents: Path, name: str = "engineer") -> Path:
    path = agents / f"{name}.md"
    path.write_text(
        """---
description: Multi-skill engineer.
mode: all
skills:
  - id: fix-bug
    description: Triage and fix a failing test
    inputs: [story_id, failing_test, error_output]
  - id: refactor
    description: Simplify a function without changing behavior
    inputs: [target_path]
  - id: review-code
    description: Review a diff for issues
    inputs: [diff_text]
permission:
  edit: allow
  write: allow
  bash:
    "ls *": allow
---

You are the engineer persona.
""",
        encoding="utf-8",
    )
    return path


def test_parse_agent_file_extracts_skills(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_persona(agents)
    agent = parse_agent_file("engineer", agents_dir=agents)
    assert len(agent.skills) == 3
    ids = {s.id for s in agent.skills}
    assert ids == {"fix-bug", "refactor", "review-code"}


def test_parse_agent_file_extracts_skill_inputs(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_persona(agents)
    agent = parse_agent_file("engineer", agents_dir=agents)
    fix = agent.find_skill("fix-bug")
    assert fix is not None
    assert "story_id" in fix.inputs
    assert "failing_test" in fix.inputs
    assert "error_output" in fix.inputs


def test_parse_agent_file_extracts_skill_descriptions(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_persona(agents)
    agent = parse_agent_file("engineer", agents_dir=agents)
    refactor = agent.find_skill("refactor")
    assert refactor is not None
    assert "Simplify" in refactor.description


def test_parse_agent_file_no_skills_section_returns_empty_list(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "simple.md").write_text(
        """---
description: A single-purpose agent.
mode: all
permission:
  edit: allow
  bash:
    "ls": allow
---

You are a simple agent.
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("simple", agents_dir=agents)
    assert agent.skills == []


def test_find_skill_returns_none_for_missing():
    agent = AgentDef(
        name="x", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=True, write_allowed=True,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[Skill(id="a", description="A")],
    )
    assert agent.find_skill("nonexistent") is None
    assert agent.find_skill("a") is not None


def test_prepend_skill_context_for_known_skill():
    agent = AgentDef(
        name="x", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=True, write_allowed=True,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[Skill(id="fix-bug", description="Fix bugs", inputs=["story_id", "error"])],
    )
    out = _prepend_skill_context("Do thing X", agent, "fix-bug")
    assert "Skill: fix-bug" in out
    assert "Fix bugs" in out
    assert "story_id" in out
    assert "error" in out
    assert "Do thing X" in out


def test_prepend_skill_context_for_unknown_skill_warns_inline():
    agent = AgentDef(
        name="x", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=True, write_allowed=True,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[Skill(id="a", description="A")],
    )
    out = _prepend_skill_context("body", agent, "nonexistent")
    assert "not declared in agent registry" in out
    assert "body" in out


def test_claude_runner_skill_param_propagates(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_persona(agents)

    captured: dict = {}
    import subprocess as _sub

    class FakeProc:
        returncode = 0
        stdout = "OK"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["input"] = kwargs.get("input", "")
        return FakeProc()

    monkeypatch.setattr(_sub, "run", fake_run)
    runner = ClaudeCodeRunner(agents_dir=agents)
    runner.run("engineer", "fix the broken test", timeout=10, skill="fix-bug")
    assert "Skill: fix-bug" in captured["input"]
    assert "Triage and fix" in captured["input"]


# ---------------------------------------------------------------------------
# M10.2: imports field + skill resolution via global library
# ---------------------------------------------------------------------------

def _write_agent_with_imports(agents: Path, name: str = "scrum-lite") -> Path:
    path = agents / f"{name}.md"
    path.write_text(
        """---
description: Agent that imports global skills.
mode: all
imports:
  - facilitate-planning
  - summarize-status
permission:
  edit: deny
  write: allow
  bash:
    "ls *": allow
---

You are a SCRUM helper.
""",
        encoding="utf-8",
    )
    return path


def _write_agent_with_inline_imports(agents: Path, name: str = "engineer-lite") -> Path:
    path = agents / f"{name}.md"
    path.write_text(
        """---
description: Agent with inline imports list.
mode: all
imports: [review-code, fix-bug]
permission:
  edit: allow
  write: allow
  bash:
    "ls *": allow
---

You are an engineer.
""",
        encoding="utf-8",
    )
    return path


def _write_global_skill(skills_dir: Path, skill_id: str, requires_edit: bool = False) -> Path:
    path = skills_dir / f"{skill_id}.md"
    path.write_text(
        f"""---
id: {skill_id}
description: Global skill {skill_id}
inputs:
  - name: target
    type: string
    description: The target
output_contract:
  - Ends with VERDICT: {skill_id.upper().replace('-', '_')}_DONE
requires:
  edit: {str(requires_edit).lower()}
  write: false
  webfetch: false
  websearch: false
  bash_allow: []
  bash_deny: []
applicable_agents: [engineer, scrum-lite]
---

# Skill: {skill_id}

Do the thing per skill {skill_id}.
""",
        encoding="utf-8",
    )
    return path


def test_parse_agent_file_extracts_imports_block(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent_with_imports(agents)
    agent = parse_agent_file("scrum-lite", agents_dir=agents)
    assert agent.imports == ["facilitate-planning", "summarize-status"]


def test_parse_agent_file_extracts_imports_inline(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent_with_inline_imports(agents)
    agent = parse_agent_file("engineer-lite", agents_dir=agents)
    assert agent.imports == ["review-code", "fix-bug"]


def test_parse_agent_file_imports_default_empty_when_absent(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_persona(agents)  # the M8.1-style persona has no imports
    agent = parse_agent_file("engineer", agents_dir=agents)
    assert agent.imports == []


def test_resolve_skill_inline_wins_over_global(tmp_path, monkeypatch):
    """When the same id exists both inline and globally, inline takes precedence."""
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    _write_global_skill(skills_dir, "review-code")
    monkeypatch.chdir(tmp_path)

    inline = Skill(id="review-code", description="Inline version", inputs=["x"])
    agent = AgentDef(
        name="hybrid", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=True, write_allowed=True,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[inline],
        imports=["review-code"],
    )
    inline_match, skill_file = resolve_skill_for_agent(agent, "review-code")
    assert inline_match is not None
    assert skill_file is None
    assert inline_match.description == "Inline version"


def test_resolve_skill_falls_back_to_global_when_imported(tmp_path, monkeypatch):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    _write_global_skill(skills_dir, "facilitate-planning")
    monkeypatch.chdir(tmp_path)

    agent = AgentDef(
        name="scrum-lite", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=True, write_allowed=True,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[],
        imports=["facilitate-planning"],
    )
    inline_match, skill_file = resolve_skill_for_agent(agent, "facilitate-planning")
    assert inline_match is None
    assert skill_file is not None
    assert skill_file.id == "facilitate-planning"


def test_resolve_skill_refuses_global_when_not_imported(tmp_path, monkeypatch):
    """A skill exists globally but the agent does NOT import it -> no resolution."""
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    _write_global_skill(skills_dir, "secret-skill")
    monkeypatch.chdir(tmp_path)

    agent = AgentDef(
        name="unrelated", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=True, write_allowed=True,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[],
        imports=[],
    )
    inline_match, skill_file = resolve_skill_for_agent(agent, "secret-skill")
    assert inline_match is None
    assert skill_file is None


def test_prepend_skill_context_uses_skill_file_when_imported(tmp_path, monkeypatch):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    _write_global_skill(skills_dir, "summarize-status")
    monkeypatch.chdir(tmp_path)

    agent = AgentDef(
        name="scrum-lite", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=True, write_allowed=True,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[],
        imports=["summarize-status"],
    )
    out = _prepend_skill_context("body content", agent, "summarize-status")
    assert "Skill: summarize-status" in out
    assert "Do the thing per skill summarize-status" in out  # body of skill file
    assert "body content" in out


def test_check_skill_permissions_passes_for_compatible(tmp_path, monkeypatch):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    _write_global_skill(skills_dir, "innocent", requires_edit=False)
    monkeypatch.chdir(tmp_path)

    agent = AgentDef(
        name="x", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=False, write_allowed=False,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[],
        imports=["innocent"],
    )
    ok, conflicts = check_skill_permissions(agent, "innocent")
    assert ok
    assert conflicts == []


def test_check_skill_permissions_flags_edit_conflict(tmp_path, monkeypatch):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    _write_global_skill(skills_dir, "destructive", requires_edit=True)
    monkeypatch.chdir(tmp_path)

    agent = AgentDef(
        name="x", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=False, write_allowed=False,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[],
        imports=["destructive"],
    )
    ok, conflicts = check_skill_permissions(agent, "destructive")
    assert not ok
    assert any("edit" in c for c in conflicts)


def test_check_skill_permissions_returns_ok_for_inline_only(tmp_path, monkeypatch):
    """Inline skills don't have a separate permission contract — nothing to enforce."""
    monkeypatch.chdir(tmp_path)
    agent = AgentDef(
        name="x", description="", system_prompt="",
        bash_allow=[], bash_deny=[],
        edit_allowed=False, write_allowed=False,
        webfetch_allowed=False, websearch_allowed=False,
        skills=[Skill(id="inline-thing", description="x")],
        imports=[],
    )
    ok, conflicts = check_skill_permissions(agent, "inline-thing")
    assert ok
    assert conflicts == []
