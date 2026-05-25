"""Unit tests for M19 Part A — phase-agent skill imports.

Asserts that the six phase agents (`@check`, `@simplify`, `@test`, `@make`,
`@guard`, `@commit`) declare the expected `imports:` field, and that each
referenced skill lists every phase agent in its `applicable_agents:`.

Until Part A is implemented these tests are RED — the agents currently have
empty `imports:` and the skills only list `[engineer]` / `[engineer, architect]`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runners import parse_agent_file
from skills import load_skill


REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".opencode" / "agents"
SKILLS_DIR = REPO_ROOT / ".opencode" / "skills"


# Single source of truth for the phase → imports mapping declared in the plan.
PHASE_IMPORTS_EXPECTED: dict[str, list[str]] = {
    "check": ["review-code"],
    "simplify": ["review-code", "refactor"],
    "test": ["write-test"],
    "make": ["fix-bug", "refactor", "debug", "add-instrumentation"],
    "guard": ["review-code"],
    "commit": [],  # mechanical phase — no skill applies
}


@pytest.mark.parametrize("phase,expected", list(PHASE_IMPORTS_EXPECTED.items()))
def test_phase_agent_declares_expected_imports(phase: str, expected: list[str]):
    agent = parse_agent_file(phase, agents_dir=AGENTS_DIR)
    assert agent.imports == expected, (
        f"@{phase} imports mismatch: expected {expected}, got {agent.imports}"
    )


@pytest.mark.parametrize(
    "skill_id,must_include",
    [
        ("review-code", ["check", "simplify", "guard"]),
        ("refactor", ["simplify", "make"]),
        ("write-test", ["test"]),
        ("fix-bug", ["make"]),
        ("debug", ["make"]),
        ("add-instrumentation", ["make"]),
    ],
)
def test_skill_lists_phase_agent_in_applicable_agents(
    skill_id: str, must_include: list[str]
):
    skill = load_skill(skill_id, skills_dir=SKILLS_DIR)
    assert skill is not None, f"skill {skill_id} not found in {SKILLS_DIR}"
    missing = [a for a in must_include if a not in skill.applicable_agents]
    assert not missing, (
        f"{skill_id}.applicable_agents missing phase agents {missing}; "
        f"have {skill.applicable_agents}"
    )


def test_phase_agents_imports_only_reference_existing_skills():
    """Every skill id imported by a phase agent must exist as a real SKILL.md."""
    for phase, imports in PHASE_IMPORTS_EXPECTED.items():
        for skill_id in imports:
            skill = load_skill(skill_id, skills_dir=SKILLS_DIR)
            assert skill is not None, (
                f"@{phase} imports {skill_id!r} but {SKILLS_DIR}/{skill_id}.md "
                "is missing"
            )
