"""Unit tests for lib/skills.py (M10.1 + M10.2 permission check)."""

from __future__ import annotations

from pathlib import Path

from skills import (
    SkillFile,
    SkillInput,
    SkillRequires,
    _pattern_covered,
    check_skill_permissions_against_agent,
    load_all_skills,
    load_skill,
    parse_skill_file,
    render_skill_context,
)


def _write_skill(skills_dir: Path, skill_id: str, body: str = "Do the thing.") -> Path:
    path = skills_dir / f"{skill_id}.md"
    path.write_text(
        f"""---
id: {skill_id}
description: Test skill {skill_id}
inputs:
  - name: target
    type: string
    description: The target
  - name: count
    type: int
    description: How many
output_contract:
  - Produces a report
  - Ends with VERDICT: DONE
requires:
  edit: false
  write: false
  webfetch: false
  websearch: false
  bash_allow: ["ls *", "cat *"]
  bash_deny: []
applicable_agents: [engineer, architect]
---

# Skill: {skill_id}

{body}
""",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# parse_skill_file
# ---------------------------------------------------------------------------

def test_parse_skill_file_extracts_top_level_fields(tmp_path: Path):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    path = _write_skill(skills_dir, "review-code")
    text = path.read_text(encoding="utf-8")
    skill = parse_skill_file(text, name_hint="review-code")
    assert skill.id == "review-code"
    assert "review-code" in skill.description
    assert "Skill: review-code" in skill.system_prompt_fragment


def test_parse_skill_file_extracts_inputs(tmp_path: Path):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    path = _write_skill(skills_dir, "fix-bug")
    skill = parse_skill_file(path.read_text(encoding="utf-8"), name_hint="fix-bug")
    assert len(skill.inputs) == 2
    names = {i.name for i in skill.inputs}
    assert names == {"target", "count"}
    target = next(i for i in skill.inputs if i.name == "target")
    assert target.type == "string"
    assert "target" in target.description.lower()
    count = next(i for i in skill.inputs if i.name == "count")
    assert count.type == "int"


def test_parse_skill_file_extracts_output_contract(tmp_path: Path):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    path = _write_skill(skills_dir, "refactor")
    skill = parse_skill_file(path.read_text(encoding="utf-8"), name_hint="refactor")
    assert len(skill.output_contract) == 2
    assert "Produces a report" in skill.output_contract
    assert any("VERDICT" in c for c in skill.output_contract)


def test_parse_skill_file_extracts_requires(tmp_path: Path):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    path = skills_dir / "elevated.md"
    path.write_text(
        """---
id: elevated
description: Needs many permissions
requires:
  edit: true
  write: true
  webfetch: false
  websearch: true
  bash_allow: ["git *", "npm install"]
  bash_deny: ["rm -rf *"]
---

Body.
""",
        encoding="utf-8",
    )
    skill = parse_skill_file(path.read_text(encoding="utf-8"), name_hint="elevated")
    assert skill.requires.edit is True
    assert skill.requires.write is True
    assert skill.requires.webfetch is False
    assert skill.requires.websearch is True
    assert "git *" in skill.requires.bash_allow
    assert "npm install" in skill.requires.bash_allow
    assert "rm -rf *" in skill.requires.bash_deny


def test_parse_skill_file_extracts_applicable_agents_inline(tmp_path: Path):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    path = _write_skill(skills_dir, "shared")
    skill = parse_skill_file(path.read_text(encoding="utf-8"), name_hint="shared")
    assert "engineer" in skill.applicable_agents
    assert "architect" in skill.applicable_agents


def test_parse_skill_file_no_frontmatter():
    """Missing frontmatter -> id falls back to name_hint, body becomes prompt."""
    skill = parse_skill_file("just a body, no fm", name_hint="bare")
    assert skill.id == "bare"
    assert skill.system_prompt_fragment == "just a body, no fm"
    assert skill.inputs == []


# ---------------------------------------------------------------------------
# load_skill / load_all_skills
# ---------------------------------------------------------------------------

def test_load_skill_returns_none_for_missing(tmp_path: Path):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    assert load_skill("nonexistent", skills_dir=skills_dir) is None


def test_load_skill_returns_skill_file(tmp_path: Path):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    _write_skill(skills_dir, "debug")
    skill = load_skill("debug", skills_dir=skills_dir)
    assert skill is not None
    assert skill.id == "debug"


def test_load_all_skills_enumerates_directory(tmp_path: Path):
    skills_dir = tmp_path / ".opencode" / "skills"
    skills_dir.mkdir(parents=True)
    _write_skill(skills_dir, "a")
    _write_skill(skills_dir, "b")
    _write_skill(skills_dir, "c")
    skills = load_all_skills(skills_dir=skills_dir)
    assert set(skills.keys()) == {"a", "b", "c"}


def test_load_all_skills_empty_when_no_dir(tmp_path: Path):
    skills = load_all_skills(skills_dir=tmp_path / "nonexistent")
    assert skills == {}


# ---------------------------------------------------------------------------
# Permission check
# ---------------------------------------------------------------------------

def test_check_permissions_passes_when_no_requirements():
    skill = SkillFile(id="x", description="")
    ok, conflicts = check_skill_permissions_against_agent(
        skill, True, True, False, False, [], [],
    )
    assert ok
    assert conflicts == []


def test_check_permissions_flags_edit_conflict():
    skill = SkillFile(id="x", description="", requires=SkillRequires(edit=True))
    ok, conflicts = check_skill_permissions_against_agent(
        skill, False, True, False, False, [], [],
    )
    assert not ok
    assert any("edit" in c for c in conflicts)


def test_check_permissions_flags_write_conflict():
    skill = SkillFile(id="x", description="", requires=SkillRequires(write=True))
    ok, conflicts = check_skill_permissions_against_agent(
        skill, True, False, False, False, [], [],
    )
    assert not ok
    assert any("write" in c for c in conflicts)


def test_check_permissions_flags_bash_conflict_missing_from_allow():
    skill = SkillFile(id="x", description="",
                      requires=SkillRequires(bash_allow=["npm install"]))
    ok, conflicts = check_skill_permissions_against_agent(
        skill, True, True, False, False, ["ls *", "cat *"], [],
    )
    assert not ok
    assert any("npm install" in c for c in conflicts)


def test_check_permissions_flags_bash_conflict_in_deny():
    skill = SkillFile(id="x", description="",
                      requires=SkillRequires(bash_allow=["rm *"]))
    ok, conflicts = check_skill_permissions_against_agent(
        skill, True, True, False, False, ["rm *"], ["rm *"],
    )
    assert not ok


def test_check_permissions_allows_wildcard_pattern_match():
    """Skill needs `git diff main`, agent allows `git diff *` -> covered."""
    skill = SkillFile(id="x", description="",
                      requires=SkillRequires(bash_allow=["git diff main"]))
    ok, conflicts = check_skill_permissions_against_agent(
        skill, True, True, False, False, ["git diff *"], [],
    )
    assert ok, f"unexpected conflicts: {conflicts}"


def test_check_permissions_allows_universal_wildcard():
    skill = SkillFile(id="x", description="",
                      requires=SkillRequires(bash_allow=["anything"]))
    ok, _ = check_skill_permissions_against_agent(
        skill, True, True, False, False, ["*"], [],
    )
    assert ok


def test_check_permissions_multiple_conflicts_listed():
    skill = SkillFile(id="x", description="",
                      requires=SkillRequires(edit=True, write=True, webfetch=True))
    ok, conflicts = check_skill_permissions_against_agent(
        skill, False, False, False, False, [], [],
    )
    assert not ok
    assert len(conflicts) >= 3


# ---------------------------------------------------------------------------
# Pattern matching helper
# ---------------------------------------------------------------------------

def test_pattern_covered_exact_match():
    assert _pattern_covered("ls", ["ls"])


def test_pattern_covered_universal_wildcard():
    assert _pattern_covered("anything", ["*"])


def test_pattern_covered_prefix_wildcard():
    assert _pattern_covered("git log --oneline", ["git log *"])


def test_pattern_covered_no_match():
    assert not _pattern_covered("rm -rf /", ["ls *", "cat *"])


# ---------------------------------------------------------------------------
# render_skill_context
# ---------------------------------------------------------------------------

def test_render_skill_context_basic():
    skill = SkillFile(
        id="review",
        description="Review code",
        inputs=[SkillInput(name="path", type="string", description="file to review")],
        output_contract=["bullet findings", "VERDICT: DONE"],
        system_prompt_fragment="Do a thorough job.",
    )
    rendered = render_skill_context(skill, user_inputs={"path": "src/foo.py"})
    assert "Skill: review" in rendered
    assert "Review code" in rendered
    assert "path (string)" in rendered
    assert "src/foo.py" in rendered
    assert "bullet findings" in rendered
    assert "VERDICT: DONE" in rendered
    assert "Do a thorough job." in rendered


def test_render_skill_context_no_inputs_no_contract():
    skill = SkillFile(id="bare", description="just a thing")
    rendered = render_skill_context(skill)
    assert "Skill: bare" in rendered
    assert "just a thing" in rendered
    # Sections should not appear when empty
    assert "## Inputs" not in rendered
    assert "## Output contract" not in rendered


# ---------------------------------------------------------------------------
# Repo-integration: every shipped skill loads and matches its expected agent
# ---------------------------------------------------------------------------

def _repo_skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent / ".opencode" / "skills"


def _repo_agents_dir() -> Path:
    return Path(__file__).resolve().parent.parent / ".opencode" / "agents"


def test_repo_skill_library_has_21_skills():
    """M10 ships 21 skills (6 engineer + 6 architect + 5 scrum-master + 4 watcher)."""
    skills = load_all_skills(skills_dir=_repo_skills_dir())
    assert len(skills) >= 21, f"only {len(skills)} skills found"


def test_repo_every_skill_has_required_fields():
    """Every shipped skill must have id, description, and applicable_agents."""
    skills = load_all_skills(skills_dir=_repo_skills_dir())
    for sid, skill in skills.items():
        assert skill.id, f"{sid}: missing id"
        assert skill.description, f"{sid}: missing description"
        assert skill.applicable_agents, f"{sid}: missing applicable_agents"
        assert skill.output_contract, f"{sid}: missing output_contract"


def test_repo_engineer_imports_resolve():
    """@engineer's 6 imports all load from .opencode/skills/."""
    from runners import parse_agent_file, resolve_skill_for_agent

    agent = parse_agent_file("engineer", agents_dir=_repo_agents_dir())
    assert agent.imports, "@engineer should declare imports"
    skills_dir = _repo_skills_dir()
    for sid in agent.imports:
        inline, skill_file = resolve_skill_for_agent(agent, sid, skills_dir=skills_dir)
        assert skill_file is not None or inline is not None, f"{sid} unresolved"


def test_repo_architect_imports_resolve():
    from runners import parse_agent_file, resolve_skill_for_agent

    agent = parse_agent_file("architect", agents_dir=_repo_agents_dir())
    assert agent.imports
    skills_dir = _repo_skills_dir()
    for sid in agent.imports:
        inline, skill_file = resolve_skill_for_agent(agent, sid, skills_dir=skills_dir)
        assert skill_file is not None or inline is not None, f"{sid} unresolved"


def test_repo_scrum_master_imports_resolve():
    from runners import parse_agent_file, resolve_skill_for_agent

    agent = parse_agent_file("scrum-master", agents_dir=_repo_agents_dir())
    assert agent.imports
    skills_dir = _repo_skills_dir()
    for sid in agent.imports:
        inline, skill_file = resolve_skill_for_agent(agent, sid, skills_dir=skills_dir)
        assert skill_file is not None or inline is not None, f"{sid} unresolved"


def test_repo_watcher_imports_resolve():
    from runners import parse_agent_file, resolve_skill_for_agent

    agent = parse_agent_file("watcher", agents_dir=_repo_agents_dir())
    assert agent.imports
    skills_dir = _repo_skills_dir()
    for sid in agent.imports:
        inline, skill_file = resolve_skill_for_agent(agent, sid, skills_dir=skills_dir)
        assert skill_file is not None or inline is not None, f"{sid} unresolved"


def test_repo_personas_no_longer_have_inline_skills():
    """M10.3 migration: personas use imports:, not inline skills:."""
    from runners import parse_agent_file

    for persona in ("engineer", "architect", "scrum-master", "watcher"):
        agent = parse_agent_file(persona, agents_dir=_repo_agents_dir())
        assert agent.skills == [], f"@{persona} still has inline skills: {[s.id for s in agent.skills]}"
        assert agent.imports, f"@{persona} should have imports"


def test_repo_skill_permissions_compatible_with_personas():
    """Every persona's permission set must cover every skill it imports."""
    from runners import check_skill_permissions, parse_agent_file

    skills_dir = _repo_skills_dir()
    for persona in ("engineer", "architect", "scrum-master", "watcher"):
        agent = parse_agent_file(persona, agents_dir=_repo_agents_dir())
        for sid in agent.imports:
            ok, conflicts = check_skill_permissions(agent, sid, skills_dir=skills_dir)
            assert ok, f"@{persona} cannot satisfy skill {sid}: {conflicts}"


def test_repo_skill_applicable_agents_match_importers():
    """For each shipped skill, its applicable_agents list should include at least one importer."""
    from runners import parse_agent_file

    skills = load_all_skills(skills_dir=_repo_skills_dir())
    persona_imports = {}
    for persona in ("engineer", "architect", "scrum-master", "watcher"):
        agent = parse_agent_file(persona, agents_dir=_repo_agents_dir())
        persona_imports[persona] = set(agent.imports)

    for sid, skill in skills.items():
        importers = {p for p, imps in persona_imports.items() if sid in imps}
        # If anyone imports this skill, they should appear in applicable_agents
        for importer in importers:
            assert importer in skill.applicable_agents, (
                f"@{importer} imports {sid} but skill's applicable_agents={skill.applicable_agents}"
            )
