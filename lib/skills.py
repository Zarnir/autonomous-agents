"""Skill library loader.

Skills are reusable LLM-task definitions that any agent may import. They live
under `.opencode/skills/<id>.md` and are loaded on-demand by the runner.

A skill declares:
- id, description, inputs
- output_contract (what the skill MUST emit)
- requires (permissions the skill needs)
- applicable_agents (which agents may use the skill)
- system_prompt_fragment (the body — prepended to the agent prompt)

This module is independent of `runners.py` so it can be imported by both the
runner adapter (for invocation) and the orchestrator (for validation /
ad-hoc cmd_agent).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


SKILLS_DIR_DEFAULT = Path(".opencode/skills")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SkillInput:
    name: str
    type: str = "string"
    description: str = ""


@dataclass
class SkillRequires:
    edit: bool = False
    write: bool = False
    webfetch: bool = False
    websearch: bool = False
    bash_allow: list[str] = field(default_factory=list)
    bash_deny: list[str] = field(default_factory=list)


@dataclass
class SkillFile:
    id: str
    description: str
    inputs: list[SkillInput] = field(default_factory=list)
    output_contract: list[str] = field(default_factory=list)
    requires: SkillRequires = field(default_factory=SkillRequires)
    applicable_agents: list[str] = field(default_factory=list)
    system_prompt_fragment: str = ""


# ---------------------------------------------------------------------------
# Parser — lightweight YAML scan (no pyyaml dependency)
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[str, str]:
    """Same shape as runners._split_frontmatter — duplicated for module independence."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[4:end], text[end + 5:]


def _parse_list_inline(value: str) -> list[str]:
    """Parse a YAML inline list like `[a, b, c]` or `["a", "b"]`."""
    inner = value.strip().strip("[]")
    if not inner:
        return []
    items: list[str] = []
    for item in inner.split(","):
        item = item.strip().strip("\"'")
        if item:
            items.append(item)
    return items


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in ("true", "yes", "1", "on")


def parse_skill_file(text: str, name_hint: str = "") -> SkillFile:
    """Parse a SKILL.md file body. Returns a SkillFile.

    Schema (frontmatter):
      id: string (required; falls back to name_hint)
      description: string
      inputs:
        - name: string
          type: string
          description: string
      output_contract:
        - string
      requires:
        edit: bool
        write: bool
        webfetch: bool
        websearch: bool
        bash_allow: ["pat1", "pat2"]
        bash_deny: []
      applicable_agents: [engineer, architect]
    """
    fm_text, body = _split_frontmatter(text)
    skill = SkillFile(id=name_hint, description="")
    skill.system_prompt_fragment = body.strip()

    if not fm_text:
        return skill

    current_section: Optional[str] = None
    current_input: Optional[SkillInput] = None
    inputs: list[SkillInput] = []
    output_contract: list[str] = []
    requires = SkillRequires()

    def _flush_input() -> None:
        nonlocal current_input
        if current_input is not None:
            inputs.append(current_input)
            current_input = None

    for raw_line in fm_text.splitlines():
        # Top-level fields reset section state
        if raw_line.startswith("id:"):
            skill.id = raw_line.split(":", 1)[1].strip()
            _flush_input()
            current_section = None
            continue
        if raw_line.startswith("description:"):
            skill.description = raw_line.split(":", 1)[1].strip()
            _flush_input()
            current_section = None
            continue
        if raw_line.startswith("applicable_agents:"):
            value = raw_line.split(":", 1)[1].strip()
            if value.startswith("["):
                skill.applicable_agents = _parse_list_inline(value)
            _flush_input()
            current_section = "applicable_agents"
            continue
        if raw_line.startswith("inputs:"):
            _flush_input()
            current_section = "inputs"
            continue
        if raw_line.startswith("output_contract:"):
            _flush_input()
            current_section = "output_contract"
            continue
        if raw_line.startswith("requires:"):
            _flush_input()
            current_section = "requires"
            continue

        stripped = raw_line.strip()
        if not stripped:
            continue

        if current_section == "inputs":
            m = re.match(r"-\s*name\s*:\s*(.+)$", stripped)
            if m:
                _flush_input()
                current_input = SkillInput(name=m.group(1).strip())
                continue
            if current_input is None:
                continue
            tm = re.match(r"type\s*:\s*(.+)$", stripped)
            if tm:
                current_input.type = tm.group(1).strip()
                continue
            dm = re.match(r"description\s*:\s*(.+)$", stripped)
            if dm:
                current_input.description = dm.group(1).strip()
                continue

        elif current_section == "output_contract":
            m = re.match(r"-\s*(.+)$", stripped)
            if m:
                output_contract.append(m.group(1).strip())

        elif current_section == "applicable_agents":
            m = re.match(r"-\s*(.+)$", stripped)
            if m:
                skill.applicable_agents.append(m.group(1).strip())

        elif current_section == "requires":
            handled = False
            for key in ("edit", "write", "webfetch", "websearch"):
                if stripped.startswith(f"{key}:"):
                    val = stripped.split(":", 1)[1]
                    setattr(requires, key, _is_truthy(val))
                    handled = True
                    break
            if handled:
                continue
            bash_allow_m = re.match(r"bash_allow\s*:\s*\[(.*)\]\s*$", stripped)
            if bash_allow_m:
                requires.bash_allow = _parse_list_inline("[" + bash_allow_m.group(1) + "]")
                continue
            bash_deny_m = re.match(r"bash_deny\s*:\s*\[(.*)\]\s*$", stripped)
            if bash_deny_m:
                requires.bash_deny = _parse_list_inline("[" + bash_deny_m.group(1) + "]")
                continue

    _flush_input()

    skill.inputs = inputs
    skill.output_contract = output_contract
    skill.requires = requires
    if not skill.id:
        skill.id = name_hint
    return skill


# ---------------------------------------------------------------------------
# Loader API
# ---------------------------------------------------------------------------

def load_skill(skill_id: str, skills_dir: Optional[Path] = None) -> Optional[SkillFile]:
    """Load `.opencode/skills/<skill_id>.md`. Returns None if file missing."""
    sdir = skills_dir or SKILLS_DIR_DEFAULT
    path = sdir / f"{skill_id}.md"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_skill_file(text, name_hint=skill_id)


def load_all_skills(skills_dir: Optional[Path] = None) -> dict[str, SkillFile]:
    """Enumerate all `.md` files under the skills dir and return id -> SkillFile."""
    sdir = skills_dir or SKILLS_DIR_DEFAULT
    if not sdir.exists():
        return {}
    out: dict[str, SkillFile] = {}
    for path in sorted(sdir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        skill = parse_skill_file(text, name_hint=path.stem)
        out[skill.id or path.stem] = skill
    return out


# ---------------------------------------------------------------------------
# Permission check
# ---------------------------------------------------------------------------

def check_skill_permissions_against_agent(
    skill: SkillFile,
    agent_edit_allowed: bool,
    agent_write_allowed: bool,
    agent_webfetch_allowed: bool,
    agent_websearch_allowed: bool,
    agent_bash_allow: list[str],
    agent_bash_deny: list[str],
) -> tuple[bool, list[str]]:
    """Return (ok, conflicts). Conflicts are human-readable strings.

    Caller supplies the agent's flags (kept generic to avoid a circular import
    between skills.py and runners.py — runners.py provides AgentDef and wraps
    this function).
    """
    conflicts: list[str] = []

    if skill.requires.edit and not agent_edit_allowed:
        conflicts.append(f"skill {skill.id!r} requires edit, agent denies it")
    if skill.requires.write and not agent_write_allowed:
        conflicts.append(f"skill {skill.id!r} requires write, agent denies it")
    if skill.requires.webfetch and not agent_webfetch_allowed:
        conflicts.append(f"skill {skill.id!r} requires webfetch, agent denies it")
    if skill.requires.websearch and not agent_websearch_allowed:
        conflicts.append(f"skill {skill.id!r} requires websearch, agent denies it")

    agent_deny_set = set(agent_bash_deny)
    for pat in skill.requires.bash_allow:
        if pat in agent_deny_set:
            conflicts.append(f"skill {skill.id!r} requires bash {pat!r}, agent denies it")
            continue
        if not _pattern_covered(pat, agent_bash_allow):
            conflicts.append(f"skill {skill.id!r} requires bash {pat!r}, agent does not allow it")

    return (len(conflicts) == 0, conflicts)


def _pattern_covered(needle: str, allow_list: list[str]) -> bool:
    """Treat trailing `*` in allow_list as prefix wildcards.

    `git diff *` covers `git diff main`. `*` covers anything.
    """
    for pat in allow_list:
        if pat == "*":
            return True
        if pat == needle:
            return True
        if pat.endswith(" *") or pat.endswith("/*"):
            prefix = pat[:-2].rstrip()
            if needle.startswith(prefix + " ") or needle == prefix:
                return True
    return False


# ---------------------------------------------------------------------------
# Render skill context for prompt injection
# ---------------------------------------------------------------------------

def render_skill_context(skill: SkillFile, user_inputs: Optional[dict] = None) -> str:
    """Build a `## Current task` preamble for the user prompt."""
    lines = [
        "## Current task",
        f"Skill: {skill.id}",
    ]
    if skill.description:
        lines.append(f"Description: {skill.description}")

    if skill.inputs:
        lines.append("")
        lines.append("## Inputs")
        for inp in skill.inputs:
            v = (user_inputs or {}).get(inp.name)
            v_str = f" = {v!r}" if v is not None else ""
            lines.append(f"- {inp.name} ({inp.type}){v_str}: {inp.description}")

    if skill.output_contract:
        lines.append("")
        lines.append("## Output contract")
        for c in skill.output_contract:
            lines.append(f"- {c}")

    if skill.system_prompt_fragment:
        lines.append("")
        lines.append("## Skill instructions")
        lines.append(skill.system_prompt_fragment)

    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)
