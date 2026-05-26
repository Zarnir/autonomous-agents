"""M15.1: Phase-function coverage tests.

Exercises run_review_loop, run_test_writer, run_implementation_with_verification,
run_commit, and the parse/extract helpers. Stubs call_agent + call_agent_with_contract
so no real LLM is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _story(sid="STORY-x", acs=None, files_to_touch=None, complexity="small"):
    return {
        "id": sid,
        "title": f"Story {sid}",
        "description": "do the thing",
        "status": "pending",
        "depends_on": [],
        "execution_wave": 1,
        "estimated_complexity": complexity,
        "acceptance_criteria": acs or ["AC1: the thing works correctly"],
        "tasks": [{"id": "TASK-x", "files_to_touch": files_to_touch or ["src/foo.py"], "type": "create"}],
        "artifacts": {},
    }


def _seed_progress(project_root: Path, story=None):
    """Seed a progress.json containing one epic with the given story."""
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0",
        "version": 1,
        "status": "in_progress",
        "epics": [{"id": "EPIC-x", "stories": [story or _story()]}],
        "sprints": [],
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# parse_verdict + is_pass
# ---------------------------------------------------------------------------

def test_parse_verdict_unknown_when_no_marker(import_orch):
    assert "UNKNOWN" in import_orch.parse_verdict("just text")


def test_parse_verdict_extracts_first_line(import_orch):
    v = import_orch.parse_verdict("VERDICT: NEEDS_CHANGES\nmore details below")
    assert v.startswith("NEEDS_CHANGES")


def test_parse_verdict_convergence_suffix(import_orch):
    v = import_orch.parse_verdict("VERDICT: PASS\n[CONVERGENCE]")
    assert "PASS" in v and "[CONVERGENCE]" in v


# M22 — verdict parsing robustness against markdown-formatted output
# (real-world regression seen with MiniMax-M2.7 via OpenCode)

def test_parse_verdict_strips_markdown_bold(import_orch):
    """`**VERDICT:**` (markdown bold) must yield a clean verdict, not `**NEEDS_CHANGES`."""
    v = import_orch.parse_verdict("**VERDICT:** NEEDS_CHANGES")
    assert v == "NEEDS_CHANGES"


def test_parse_verdict_strips_markdown_italic(import_orch):
    """`_VERDICT:_` (markdown italic) yields clean verdict."""
    v = import_orch.parse_verdict("_VERDICT:_ PASS")
    assert v == "PASS"


def test_parse_verdict_does_not_double_convergence(import_orch):
    """If the verdict tail already contains `[CONVERGENCE]`, we must not append a duplicate."""
    v = import_orch.parse_verdict("VERDICT: NEEDS_CHANGES [CONVERGENCE]")
    assert v.count("[CONVERGENCE]") == 1
    assert "NEEDS_CHANGES" in v


def test_parse_verdict_handles_minimax_style_output(import_orch):
    """Real-world regression: MiniMax-M2.7 emits `**Verdict:** X [CONVERGENCE]` —
    historically produced `** NEEDS_CHANGES [CONVERGENCE] [CONVERGENCE]`."""
    raw = (
        "## @check Review\n\n"
        "### STORY-x\n\n"
        "**BLOCK**\n- nothing major\n\n"
        "**Verdict:** NEEDS_CHANGES [CONVERGENCE]\n"
    )
    v = import_orch.parse_verdict(raw)
    assert "NEEDS_CHANGES" in v
    assert "**" not in v
    assert v.count("[CONVERGENCE]") == 1


def test_is_pass_block_overrides(import_orch):
    assert not import_orch.is_pass("PASS but BLOCK something")


def test_is_pass_needs_changes_is_not_pass(import_orch):
    assert not import_orch.is_pass("NEEDS_CHANGES")


def test_is_pass_convergence_after_prior_pass(import_orch):
    assert import_orch.is_pass("[CONVERGENCE] unrelated", prior_was_pass=True)


# ---------------------------------------------------------------------------
# run_review_loop (with stubbed @check / @simplify)
# ---------------------------------------------------------------------------

def test_review_loop_pass_first_cycle(import_orch, monkeypatch):
    orch = import_orch
    calls = []

    def fake_call(name, prompt, **kw):
        calls.append(name)
        return "VERDICT: PASS\n"

    monkeypatch.setattr(orch, "call_agent", fake_call)
    outcome, findings = orch.run_review_loop(_story(), mode="design", impl_files=None)
    assert outcome == "PASS"
    assert "check" in findings and "simplify" in findings
    assert calls == ["check", "simplify"]


def test_review_loop_block_short_circuits(import_orch, monkeypatch):
    orch = import_orch

    def fake_call(name, prompt, **kw):
        if name == "check":
            return "VERDICT: BLOCK — security issue\n"
        return "VERDICT: PASS\n"

    monkeypatch.setattr(orch, "call_agent", fake_call)
    outcome, _ = orch.run_review_loop(_story(), mode="design", impl_files=None)
    assert outcome == "BLOCK"


def test_review_loop_proceed_with_warn_after_cycles(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "VERDICT: NEEDS_CHANGES\nstyle nit\n")
    outcome, _ = orch.run_review_loop(_story(), mode="design", impl_files=None)
    assert outcome == "PROCEED_WITH_WARN"


def test_review_loop_block_after_final_cycle(import_orch, monkeypatch):
    orch = import_orch

    def fake_call(name, prompt, **kw):
        if name == "simplify":
            return "VERDICT: BLOCK [CONVERGENCE]\n"
        return "VERDICT: NEEDS_CHANGES\n"

    monkeypatch.setattr(orch, "call_agent", fake_call)
    outcome, _ = orch.run_review_loop(_story(), mode="design", impl_files=None)
    assert outcome == "BLOCK"


def test_build_review_prompt_includes_prior_when_provided(import_orch):
    p = import_orch.build_review_prompt(
        "check", _story(), "design", impl_files=["src/foo.py"], prior="prior text"
    )
    assert "src/foo.py" in p
    assert "PREVIOUS REVIEW" in p
    assert "prior text" in p


def test_build_review_prompt_omits_prior_section_when_blank(import_orch):
    p = import_orch.build_review_prompt(
        "check", _story(), "design", impl_files=None, prior=""
    )
    assert "PREVIOUS REVIEW" not in p


# ---------------------------------------------------------------------------
# run_test_writer
# ---------------------------------------------------------------------------

def test_test_writer_happy_path(import_orch, monkeypatch):
    orch = import_orch

    canned = (
        "Test files written\n"
        "- `tests/test_foo.py` covers the thing works correctly\n\n"
        "| Acceptance Criterion | Covering Test |\n"
        "| --- | --- |\n"
        "| AC1: the thing works correctly | `tests/test_foo.py::test_works` |\n\n"
        "VERDICT: RED_VERIFIED\n"
    )
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: canned)
    result = orch.run_test_writer(_story(), prior_findings={"check": "", "simplify": ""})
    assert result["status"] == "OK"
    assert "tests/test_foo.py" in result["test_files"]
    assert result["criterion_test_mapping"]


def test_test_writer_env_broken_returns_fail(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: "VERDICT: ENV_BROKEN\n")
    result = orch.run_test_writer(_story(), prior_findings={})
    assert result["status"] == "FAIL"
    assert result["detail"] == "env_broken"


def test_test_writer_incomplete_coverage_retries_then_fails(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: "VERDICT: INCOMPLETE_COVERAGE\n")
    result = orch.run_test_writer(_story(), prior_findings={})
    assert result["status"] == "FAIL"
    assert "no_red_verified" in result["detail"]


def test_test_writer_raises_when_red_but_no_test_files(import_orch, monkeypatch):
    """RED_VERIFIED with valid mapping but no file list → AgentError (contract violation)."""
    orch = import_orch
    canned = (
        "| Acceptance Criterion | Covering Test |\n"
        "| --- | --- |\n"
        "| AC1: the thing works correctly | `tests/test_foo.py::test_works` |\n"
        "VERDICT: RED_VERIFIED\n"
    )
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: canned)
    with pytest.raises(orch.AgentError) as exc_info:
        orch.run_test_writer(_story(), prior_findings={})
    assert "test files" in str(exc_info.value).lower()


def test_test_writer_raises_when_red_but_no_mapping(import_orch, monkeypatch):
    """RED_VERIFIED with test file but no mapping table → AgentError."""
    orch = import_orch
    canned = (
        "Covers thing works correctly\n"
        "- `tests/test_foo.py` for criterion\n"
        "VERDICT: RED_VERIFIED\n"
    )
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: canned)
    # AC without "AC1:" prefix so criterion_appears matches the report
    story = _story(acs=["thing works correctly"])
    with pytest.raises(orch.AgentError) as exc_info:
        orch.run_test_writer(story, prior_findings={})
    assert "mapping" in str(exc_info.value).lower()


def test_test_writer_red_with_uncovered_criteria_loops_to_fail(import_orch, monkeypatch):
    """RED_VERIFIED but validate_criterion_coverage rejects → falls through loop → FAIL."""
    orch = import_orch
    # Output claims RED but lacks the AC keywords → validate fails → loop exhausted
    canned = "Test added\n- `tests/test_foo.py` something else\nVERDICT: RED_VERIFIED\n"
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: canned)
    result = orch.run_test_writer(_story(acs=["AC1: unique sentinel widget deletion"]), prior_findings={})
    assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# validate_criterion_coverage + criterion_appears
# ---------------------------------------------------------------------------

def test_validate_criterion_coverage_empty_story_is_ok(import_orch):
    assert import_orch.validate_criterion_coverage("any text", {"acceptance_criteria": []})


def test_validate_criterion_coverage_detects_missing(import_orch):
    story = {"acceptance_criteria": ["AC1: deletes the unique sentinel widget"]}
    assert not import_orch.validate_criterion_coverage("no relevant content here", story)


def test_validate_criterion_coverage_accepts_when_all_words_present(import_orch):
    story = {"acceptance_criteria": ["deletes widget"]}
    # criterion lowercased = "deletes widget" → words >3: ["deletes", "widget"]
    assert import_orch.validate_criterion_coverage("Test for deletes widget", story)


def test_criterion_appears_word_match(import_orch):
    # Helper expects already-lowercased criterion + report
    assert import_orch.criterion_appears("deletes widgets cleanly", "deletes widgets cleanly")
    assert not import_orch.criterion_appears("deletes widgets cleanly", "creates widgets")


# ---------------------------------------------------------------------------
# run_implementation_with_verification
# ---------------------------------------------------------------------------

def test_impl_happy_path_returns_green(import_orch, monkeypatch):
    orch = import_orch
    story = _story()
    story["artifacts"]["test_files"] = ["tests/test_foo.py"]

    responses = {
        "make": (
            "Edited src/foo.py\n"
            "Implementation:\n"
            "- `src/foo.py`\n"
            "Status: GREEN\n"
        ),
        "guard": "PASS_SCOPE\n",
    }

    def fake_call(name, prompt, **kw):
        return responses[name]

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_call)
    monkeypatch.setattr(orch, "run_tests_independently", lambda files, cwd=None: (True, "all tests pass"))

    result = orch.run_implementation_with_verification(story)
    assert result["status"] == "GREEN_VERIFIED"
    assert "src/foo.py" in result["files"]


def test_impl_blocked_make_retries_then_fails(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: "Status: BLOCKED\nWVB\n")
    result = orch.run_implementation_with_verification(_story())
    assert result["status"] == "FAIL"
    assert result["detail"] == "make_blocked"


def test_impl_partial_make_retries_then_fails(import_orch, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: "Status: PARTIAL\n")
    result = orch.run_implementation_with_verification(_story())
    assert result["status"] == "FAIL"
    assert "partial" in result["detail"]


def test_impl_guard_out_of_scope_retries_and_fails(import_orch, monkeypatch):
    orch = import_orch
    story = _story()
    story["artifacts"]["test_files"] = []

    def fake_call(name, prompt, **kw):
        if name == "make":
            return "Status: GREEN\n"
        return "FAIL_OUT_OF_SCOPE\n"

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_call)
    result = orch.run_implementation_with_verification(story)
    assert result["status"] == "FAIL"
    assert result["detail"] == "guard_out_of_scope"


def test_impl_guard_nothing_changed_fails(import_orch, monkeypatch):
    orch = import_orch
    story = _story()

    def fake_call(name, prompt, **kw):
        if name == "make":
            return "Status: GREEN\n"
        return "FAIL_NOTHING_CHANGED\n"

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_call)
    result = orch.run_implementation_with_verification(story)
    assert result["status"] == "FAIL"
    assert result["detail"] == "guard_nothing_changed"


def test_impl_independent_test_run_fail_loops(import_orch, monkeypatch):
    orch = import_orch
    story = _story()
    story["artifacts"]["test_files"] = ["tests/test_foo.py"]

    def fake_call(name, prompt, **kw):
        if name == "make":
            return "Status: GREEN\n"
        return "PASS_SCOPE\n"

    monkeypatch.setattr(orch, "call_agent_with_contract", fake_call)
    monkeypatch.setattr(orch, "run_tests_independently", lambda files, cwd=None: (False, "tests failed"))

    result = orch.run_implementation_with_verification(story)
    assert result["status"] == "FAIL"
    assert "independent_test_run_failed" in result["detail"]


# ---------------------------------------------------------------------------
# run_commit
# ---------------------------------------------------------------------------

def test_commit_success_returns_hash(import_orch, monkeypatch, project_root):
    orch = import_orch
    _seed_progress(project_root)

    canned = (
        "Status: COMMITTED\n"
        "Commit hash: deadbeef1234567\n"
        "Branch: feat/EPIC-x/STORY-x\n"
    )
    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: canned)

    story = _story()
    story["artifacts"] = {"test_files": ["tests/test_foo.py"], "implementation_files": ["src/foo.py"]}
    result = orch.run_commit(story)
    assert result["ok"] is True
    assert result["hash"] == "deadbeef1234567"
    assert result["branch"].startswith("feat/")


def test_commit_branch_exists_marker(import_orch, monkeypatch, project_root):
    orch = import_orch
    _seed_progress(project_root)

    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: "FAIL_BRANCH_EXISTS\n")
    result = orch.run_commit(_story())
    assert result["ok"] is False
    assert result["detail"] == "fail_branch_exists"


def test_commit_no_repo_marker(import_orch, monkeypatch, project_root):
    orch = import_orch
    _seed_progress(project_root)

    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: "FAIL_NO_REPO\n")
    result = orch.run_commit(_story())
    assert result["ok"] is False
    assert result["detail"] == "fail_no_repo"


def test_commit_committed_without_hash_raises(import_orch, monkeypatch, project_root):
    """Contract violation: COMMITTED with no Commit hash line."""
    orch = import_orch
    _seed_progress(project_root)

    monkeypatch.setattr(orch, "call_agent_with_contract", lambda *a, **kw: "Status: COMMITTED\n(no hash)\n")
    with pytest.raises(orch.AgentError) as exc_info:
        orch.run_commit(_story())
    assert "commit hash" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Extract helpers
# ---------------------------------------------------------------------------

def test_extract_test_files_skips_non_backtick_lines(import_orch):
    out = "Some prose\n- not a file\n- `tests/a.py`\n- not either"
    assert import_orch.extract_test_files(out) == ["tests/a.py"]


def test_extract_test_files_empty_when_none(import_orch):
    assert import_orch.extract_test_files("just text") == []


def test_extract_branch_returns_branch(import_orch):
    out = "Status: COMMITTED\nBranch: feat/EPIC-x/STORY-y\n"
    assert import_orch.extract_branch(out) == "feat/EPIC-x/STORY-y"


def test_extract_branch_returns_none_when_absent(import_orch):
    assert import_orch.extract_branch("no branch line") is None


def test_extract_impl_files_finds_files(import_orch):
    out = (
        "Implementation:\n"
        "- `src/auth.py`\n"
        "- `src/utils.py`\n"
        "**Done**\n"
        "Status: GREEN\n"
    )
    files = import_orch.extract_impl_files(out)
    assert "src/auth.py" in files
    assert "src/utils.py" in files


def test_extract_impl_files_terminates_at_heading(import_orch):
    out = (
        "Implementation:\n"
        "- `src/a.py`\n"
        "## Next section\n"
        "- `src/should_not_appear.py`\n"
    )
    files = import_orch.extract_impl_files(out)
    assert "src/a.py" in files
    assert "src/should_not_appear.py" not in files


def test_extract_criterion_mapping_parses_table(import_orch):
    out = (
        "Some prose\n\n"
        "| Acceptance Criterion | Covering Test |\n"
        "| --- | --- |\n"
        "| AC1: thing | `tests/a.py::test_x` |\n"
        "| AC2: other | `tests/b.py::test_y`, `tests/c.py::test_z` |\n\n"
        "VERDICT: RED_VERIFIED\n"
    )
    mapping = import_orch.extract_criterion_mapping(out)
    assert "AC1: thing" in mapping
    assert "AC2: other" in mapping
    assert len(mapping["AC2: other"]) == 2


def test_extract_criterion_mapping_empty_when_no_table(import_orch):
    assert import_orch.extract_criterion_mapping("nothing tabular here") == {}


# ---------------------------------------------------------------------------
# build_make_prompt / build_guard_prompt — pure strings
# ---------------------------------------------------------------------------

def test_build_make_prompt_includes_files_to_touch(import_orch):
    s = _story(files_to_touch=["src/a.py", "src/b.py"])
    p = import_orch.build_make_prompt(s)
    assert "src/a.py" in p and "src/b.py" in p
    assert s["title"] in p


def test_build_make_prompt_includes_retry_constraint(import_orch):
    s = _story()
    p = import_orch.build_make_prompt(s, retry_constraint="guard_out_of_scope")
    assert "guard_out_of_scope" in p
    assert "RETRY CONSTRAINT" in p


def test_build_make_prompt_no_files_marker(import_orch):
    s = _story(files_to_touch=[])
    s["tasks"] = []
    p = import_orch.build_make_prompt(s)
    assert "[none declared]" in p


def test_build_guard_prompt_lists_declared_files(import_orch):
    s = _story(files_to_touch=["src/x.py"])
    p = import_orch.build_guard_prompt(s)
    assert "src/x.py" in p
    assert s["id"] in p
