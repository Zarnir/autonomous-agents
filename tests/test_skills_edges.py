"""M17.4: skills.py edge-case coverage for 100%."""

from __future__ import annotations

from pathlib import Path

from skills import (
    SkillFile,
    SkillRequires,
    check_skill_permissions_against_agent,
    load_all_skills,
    load_skill,
    parse_skill_file,
)


def test_parse_skill_file_no_closing_frontmatter():
    text = "---\nid: foo\ndescription: bar\nno closing marker\n"
    skill = parse_skill_file(text, name_hint="fallback-id")
    assert skill.id == "fallback-id"


def test_parse_skill_file_skips_empty_lines_in_inputs():
    text = (
        "---\n"
        "id: my-skill\n"
        "description: A skill\n"
        "inputs:\n"
        "\n"
        "  - name: foo\n"
        "    type: string\n"
        "---\n\nbody\n"
    )
    skill = parse_skill_file(text, name_hint="x")
    assert skill.id == "my-skill"
    assert len(skill.inputs) == 1
    assert skill.inputs[0].name == "foo"


def test_parse_skill_file_inputs_section_no_name_prefix_skips():
    text = (
        "---\n"
        "id: my-skill\n"
        "description: x\n"
        "inputs:\n"
        "  random non-name line\n"
        "  - name: foo\n"
        "    type: string\n"
        "---\n"
    )
    skill = parse_skill_file(text, name_hint="x")
    assert len(skill.inputs) == 1


def test_parse_skill_file_applicable_agents_block_form():
    text = (
        "---\n"
        "id: my-skill\n"
        "description: x\n"
        "applicable_agents:\n"
        "  - engineer\n"
        "  - architect\n"
        "  - scrum-master\n"
        "---\n"
    )
    skill = parse_skill_file(text, name_hint="x")
    assert "engineer" in skill.applicable_agents
    assert "architect" in skill.applicable_agents
    assert "scrum-master" in skill.applicable_agents


def test_parse_skill_file_missing_id_uses_name_hint():
    text = (
        "---\n"
        "description: a skill without id\n"
        "---\n\nbody\n"
    )
    skill = parse_skill_file(text, name_hint="fallback")
    assert skill.id == "fallback"


def test_parse_skill_file_empty_id_field_falls_back_to_name_hint():
    """When id: is explicitly empty, the post-loop check overrides with name_hint."""
    text = (
        "---\n"
        "id: \n"
        "description: empty id\n"
        "---\n\nbody\n"
    )
    skill = parse_skill_file(text, name_hint="fallback-id")
    assert skill.id == "fallback-id"


def test_load_skill_returns_none_on_oserror(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "broken.md").write_text("---\nid: x\n---\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("broken.md"):
            raise OSError("disk")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    assert load_skill("broken", skills_dir=skills) is None


def test_load_all_skills_skips_unreadable_file(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "good.md").write_text(
        "---\nid: good\ndescription: ok\n---\n", encoding="utf-8",
    )
    (skills / "bad.md").write_text("---\nid: bad\n---\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("bad.md"):
            raise OSError("disk")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    skills_loaded = load_all_skills(skills_dir=skills)
    assert "good" in skills_loaded
    assert "bad" not in skills_loaded


def test_check_skill_permissions_flags_websearch_conflict():
    skill = SkillFile(
        id="searchy",
        description="needs search",
        requires=SkillRequires(websearch=True),
    )
    ok, conflicts = check_skill_permissions_against_agent(
        skill,
        agent_edit_allowed=False,
        agent_write_allowed=False,
        agent_webfetch_allowed=False,
        agent_websearch_allowed=False,
        agent_bash_allow=[],
        agent_bash_deny=[],
    )
    assert ok is False
    assert any("websearch" in c for c in conflicts)
