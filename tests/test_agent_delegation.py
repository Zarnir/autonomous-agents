"""M19 Part B — sub-agent delegation tests.

Covers:
  - `consult_agents:` parser (inline + block forms) on `AgentDef`
  - `call_agent_with_delegation` harness behavior:
      * passthrough when no delegation marker
      * runs declared sub-agent when marker present
      * refuses delegation to non-listed agent
      * enforces max-delegations-per-phase cap
      * sub-agents cannot themselves delegate (no nesting)
  - phase agents now declare expected `consult_agents:` per the plan
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".opencode" / "agents"


# ---------------------------------------------------------------------------
# Parser tests — `consult_agents:` field on AgentDef
# ---------------------------------------------------------------------------

def _write_agent_with_block_consult(agents: Path, name: str = "delegator-block") -> Path:
    path = agents / f"{name}.md"
    path.write_text(
        """---
description: Agent that consults peers in block form.
mode: all
consult_agents:
  - architect
  - engineer
permission:
  edit: deny
  write: deny
  bash:
    "ls *": allow
---

You may delegate to architect or engineer.
""",
        encoding="utf-8",
    )
    return path


def _write_agent_with_inline_consult(agents: Path, name: str = "delegator-inline") -> Path:
    path = agents / f"{name}.md"
    path.write_text(
        """---
description: Agent that consults peers in inline form.
mode: all
consult_agents: [architect, scrum-master]
permission:
  edit: deny
  write: deny
  bash:
    "ls *": allow
---

You may delegate to architect or scrum-master.
""",
        encoding="utf-8",
    )
    return path


def test_parse_agent_file_extracts_consult_agents_block(tmp_path):
    from runners import parse_agent_file

    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent_with_block_consult(agents)
    agent = parse_agent_file("delegator-block", agents_dir=agents)
    assert agent.consult_agents == ["architect", "engineer"]


def test_parse_agent_file_extracts_consult_agents_inline(tmp_path):
    from runners import parse_agent_file

    agents = tmp_path / "agents"
    agents.mkdir()
    _write_agent_with_inline_consult(agents)
    agent = parse_agent_file("delegator-inline", agents_dir=agents)
    assert agent.consult_agents == ["architect", "scrum-master"]


def test_parse_agent_file_consult_agents_after_skills_block(tmp_path):
    """Cover the parser branch where `consult_agents:` follows a `skills:` block —
    the in-progress current_skill must be flushed before consult_agents is parsed."""
    from runners import parse_agent_file

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "hybrid.md").write_text(
        """---
description: Has both skills and consult_agents.
mode: all
skills:
  - id: foo
    description: a foo
    inputs: [x]
consult_agents: [architect]
permission:
  edit: deny
  bash:
    "ls *": allow
---

body.
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("hybrid", agents_dir=agents)
    assert {s.id for s in agent.skills} == {"foo"}
    assert agent.consult_agents == ["architect"]


def test_parse_agent_file_consult_agents_block_then_top_level_field(tmp_path):
    """Cover parser branch where a top-level field follows the consult_agents block,
    resetting current_section to None."""
    from runners import parse_agent_file

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "ordered.md").write_text(
        """---
description: consult_agents block followed by another field.
mode: all
consult_agents:
  - architect
  - engineer
permission:
  edit: deny
  bash:
    "ls *": allow
---

body.
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("ordered", agents_dir=agents)
    assert agent.consult_agents == ["architect", "engineer"]
    # Permission section was still parsed (consult section was terminated).
    assert agent.edit_allowed is False


def test_parse_agent_file_consult_agents_block_terminated_by_unknown_field(tmp_path):
    """An unrecognized top-level YAML key after a `consult_agents:` block must
    terminate the section (covers the in-section terminator branch)."""
    from runners import parse_agent_file

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "ordered2.md").write_text(
        """---
description: consult_agents followed by an unknown key.
consult_agents:
  - architect
some_unknown_field: ignored
permission:
  edit: deny
  bash:
    "ls *": allow
---

body.
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("ordered2", agents_dir=agents)
    assert agent.consult_agents == ["architect"]
    # Confirm the permission block downstream still parsed cleanly.
    assert agent.edit_allowed is False


def test_parse_agent_file_consult_agents_default_empty(tmp_path):
    from runners import parse_agent_file

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "lonely.md").write_text(
        """---
description: Agent with no consult_agents.
mode: all
permission:
  edit: deny
  bash:
    "ls *": allow
---

I work alone.
""",
        encoding="utf-8",
    )
    agent = parse_agent_file("lonely", agents_dir=agents)
    assert agent.consult_agents == []


# ---------------------------------------------------------------------------
# Per-phase consult_agents mapping declared by the plan
# ---------------------------------------------------------------------------

PHASE_CONSULT_EXPECTED: dict[str, list[str]] = {
    "check": ["architect"],
    "simplify": ["architect"],
    "test": ["engineer", "architect"],
    "make": ["architect", "engineer", "check"],
    "guard": ["architect"],
    "commit": [],
}


@pytest.mark.parametrize("phase,expected", list(PHASE_CONSULT_EXPECTED.items()))
def test_phase_agent_declares_consult_agents(phase: str, expected: list[str]):
    from runners import parse_agent_file

    agent = parse_agent_file(phase, agents_dir=AGENTS_DIR)
    assert agent.consult_agents == expected, (
        f"@{phase} consult_agents mismatch: expected {expected}, got {agent.consult_agents}"
    )


# ---------------------------------------------------------------------------
# call_agent_with_delegation harness
# ---------------------------------------------------------------------------

@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _stage_agent(project_root: Path, name: str, consult: list[str]):
    """Write a fake .opencode/agents/<name>.md with the given consult_agents."""
    agents = project_root / ".opencode" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    consult_str = ", ".join(consult) if consult else ""
    (agents / f"{name}.md").write_text(
        f"""---
description: stub agent {name}.
mode: all
consult_agents: [{consult_str}]
permission:
  edit: deny
  bash:
    "ls *": allow
---

You are @{name}.
""",
        encoding="utf-8",
    )


def test_delegation_passthrough_when_no_marker(import_orch, monkeypatch, project_root):
    """No DELEGATE_TO marker -> parent output returned unchanged, no sub-agent calls."""
    orch = import_orch
    _stage_agent(project_root, "make", consult=["architect"])

    calls = []

    def fake_contract(name, prompt, expected_verdicts, **kw):
        calls.append(("contract", name))
        return "Status: GREEN\nNo delegation needed.\n"

    def fake_call(name, prompt, **kw):
        calls.append(("call", name))
        return "should not be invoked"

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_contract)
    monkeypatch.setattr(orch, "call_agent", fake_call)

    out = orch.call_agent_with_delegation(
        "make",
        "do the thing",
        expected_verdicts=["GREEN", "BLOCKED"],
    )
    assert "Status: GREEN" in out
    assert ("contract", "make") in calls
    assert all(kind != "call" for kind, _ in calls)


def test_delegation_invokes_listed_subagent_and_resumes_parent(
    import_orch, monkeypatch, project_root
):
    orch = import_orch
    _stage_agent(project_root, "make", consult=["architect"])
    _stage_agent(project_root, "architect", consult=[])

    contract_calls = []
    sub_prompts: list = []

    parent_outputs = iter([
        (
            "Thinking about this...\n"
            "DELEGATE_TO: @architect\n"
            "QUESTION:\n"
            "Should I use a queue or a list here?\n"
            "END_DELEGATE\n"
        ),
        "Status: GREEN\nUsed a queue per architect.\n",
    ])

    def fake_contract(name, prompt, expected_verdicts, **kw):
        contract_calls.append((name, prompt))
        return next(parent_outputs)

    def fake_call(name, prompt, **kw):
        sub_prompts.append((name, prompt))
        return "Use a queue.\nVERDICT: ADVICE_GIVEN\n"

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_contract)
    monkeypatch.setattr(orch, "call_agent", fake_call)

    out = orch.call_agent_with_delegation(
        "make",
        "Implement the worker.",
        expected_verdicts=["GREEN"],
    )

    assert "Status: GREEN" in out
    assert len(contract_calls) == 2
    assert len(sub_prompts) == 1
    sub_name, sub_prompt = sub_prompts[0]
    assert sub_name == "architect"
    assert "queue or a list" in sub_prompt

    second_prompt = contract_calls[1][1]
    assert "Consultation result from @architect" in second_prompt
    assert "Use a queue." in second_prompt


def test_delegation_refused_for_non_listed_agent(
    import_orch, monkeypatch, project_root
):
    orch = import_orch
    _stage_agent(project_root, "make", consult=["architect"])  # bogus not listed

    sub_calls = []

    def fake_contract(name, prompt, expected_verdicts, **kw):
        return (
            "DELEGATE_TO: @bogus\n"
            "QUESTION:\n"
            "anything\n"
            "END_DELEGATE\n"
            "Status: GREEN\n"
        )

    def fake_call(name, prompt, **kw):
        sub_calls.append(name)
        return "should not be reached"

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_contract)
    monkeypatch.setattr(orch, "call_agent", fake_call)

    out = orch.call_agent_with_delegation(
        "make",
        "task",
        expected_verdicts=["GREEN"],
    )
    assert sub_calls == []
    assert "Status: GREEN" in out


def test_delegation_cap_enforced(import_orch, monkeypatch, project_root):
    orch = import_orch
    _stage_agent(project_root, "make", consult=["architect"])
    _stage_agent(project_root, "architect", consult=[])

    contract_count = {"n": 0}
    sub_count = {"n": 0}

    def fake_contract(name, prompt, expected_verdicts, **kw):
        contract_count["n"] += 1
        return (
            "DELEGATE_TO: @architect\n"
            "QUESTION:\n"
            f"question {contract_count['n']}\n"
            "END_DELEGATE\n"
            "Status: GREEN\n"
        )

    def fake_call(name, prompt, **kw):
        sub_count["n"] += 1
        return "advice\n"

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_contract)
    monkeypatch.setattr(orch, "call_agent", fake_call)
    monkeypatch.setattr(orch, "MAX_DELEGATIONS_PER_PHASE", 2)

    out = orch.call_agent_with_delegation(
        "make",
        "task",
        expected_verdicts=["GREEN"],
    )
    assert sub_count["n"] == 2, f"expected cap=2, got {sub_count['n']}"
    assert contract_count["n"] == 3
    assert "Status: GREEN" in out


def test_subagent_cannot_nest_delegation(import_orch, monkeypatch, project_root):
    """Sub-agents reach call_agent, not call_agent_with_delegation, so their
    DELEGATE_TO markers must NOT trigger a further delegation."""
    orch = import_orch
    _stage_agent(project_root, "make", consult=["architect"])
    _stage_agent(project_root, "architect", consult=["engineer"])
    _stage_agent(project_root, "engineer", consult=[])

    contract_calls = []
    sub_calls = []

    parent_outputs = iter([
        (
            "DELEGATE_TO: @architect\n"
            "QUESTION:\n"
            "advise\n"
            "END_DELEGATE\n"
        ),
        "Status: GREEN\n",
    ])

    def fake_contract(name, prompt, expected_verdicts, **kw):
        contract_calls.append(name)
        return next(parent_outputs)

    def fake_call(name, prompt, **kw):
        sub_calls.append(name)
        return (
            "DELEGATE_TO: @engineer\n"
            "QUESTION:\n"
            "deeper question\n"
            "END_DELEGATE\n"
            "VERDICT: ADVICE_GIVEN\n"
        )

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_contract)
    monkeypatch.setattr(orch, "call_agent", fake_call)

    out = orch.call_agent_with_delegation(
        "make",
        "task",
        expected_verdicts=["GREEN"],
    )
    assert sub_calls == ["architect"]
    assert contract_calls == ["make", "make"]
    assert "Status: GREEN" in out


def test_parse_delegation_marker_rejects_empty_question(import_orch):
    """Whitespace-only QUESTION is treated as malformed → marker returns None."""
    orch = import_orch
    # END_DELEGATE must be on its own line after the question; whitespace-only
    # question collapses to empty after strip → returns None.
    raw = (
        "DELEGATE_TO: @architect\n"
        "QUESTION:\n"
        " \n"
        "END_DELEGATE\n"
    )
    assert orch._parse_delegation_marker(raw) is None


def test_subagent_failure_returns_parent_output(import_orch, monkeypatch, project_root):
    """If the sub-agent raises AgentError, we log and return the parent's output."""
    orch = import_orch
    _stage_agent(project_root, "make", consult=["architect"])
    _stage_agent(project_root, "architect", consult=[])

    parent_output = (
        "DELEGATE_TO: @architect\n"
        "QUESTION:\n"
        "anything\n"
        "END_DELEGATE\n"
        "Status: GREEN\n"
    )

    def fake_contract(name, prompt, expected_verdicts, **kw):
        return parent_output

    def fake_call(name, prompt, **kw):
        raise orch.AgentError("architect", "stub failure for coverage")

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_contract)
    monkeypatch.setattr(orch, "call_agent", fake_call)

    out = orch.call_agent_with_delegation(
        "make",
        "task",
        expected_verdicts=["GREEN"],
    )
    # Parent output returned as-is despite sub-agent failure.
    assert out == parent_output


def test_max_delegations_per_phase_loaded_from_config(import_orch, monkeypatch, project_root):
    """load_config() honors pipeline.max_delegations_per_phase."""
    orch = import_orch
    cfg_path = project_root / ".opencode" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        '{"pipeline": {"max_delegations_per_phase": 5}}',
        encoding="utf-8",
    )
    # Use monkeypatch so CONFIG_FILE + MAX_DELEGATIONS_PER_PHASE are restored
    # after this test — otherwise downstream tests see the stale state.
    monkeypatch.setattr(orch, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(orch, "MAX_DELEGATIONS_PER_PHASE", orch.MAX_DELEGATIONS_PER_PHASE)
    orch.load_config()
    assert orch.MAX_DELEGATIONS_PER_PHASE == 5


def test_delegation_routes_through_call_agent_for_cost_tracking(
    import_orch, monkeypatch, project_root
):
    """Cost tracking lives in call_agent. Sub-agents must go through it."""
    orch = import_orch
    _stage_agent(project_root, "make", consult=["architect"])
    _stage_agent(project_root, "architect", consult=[])

    parent_outputs = iter([
        (
            "DELEGATE_TO: @architect\n"
            "QUESTION:\n"
            "anything\n"
            "END_DELEGATE\n"
        ),
        "Status: GREEN\n",
    ])

    call_agent_calls = []

    def fake_contract(name, prompt, expected_verdicts, **kw):
        return next(parent_outputs)

    def fake_call(name, prompt, **kw):
        call_agent_calls.append(name)
        return "advice\n"

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_contract)
    monkeypatch.setattr(orch, "call_agent", fake_call)

    orch.call_agent_with_delegation(
        "make",
        "task",
        expected_verdicts=["GREEN"],
    )
    assert call_agent_calls == ["architect"]
