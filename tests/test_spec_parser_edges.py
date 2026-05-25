"""M15.7: spec_parser edge cases — YAML coercion, index ordering, error paths."""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# _parse_yaml_frontmatter
# ---------------------------------------------------------------------------

def test_yaml_parses_block_form_list():
    from spec_parser import _parse_yaml_frontmatter
    text = "depends_on:\n  - EPIC-a\n  - EPIC-b\n"
    result = _parse_yaml_frontmatter(text)
    assert result["depends_on"] == ["EPIC-a", "EPIC-b"]


def test_yaml_parses_flow_form_list():
    from spec_parser import _parse_yaml_frontmatter
    text = "tags: [foo, bar, baz]"
    result = _parse_yaml_frontmatter(text)
    assert result["tags"] == ["foo", "bar", "baz"]


def test_yaml_empty_flow_list():
    from spec_parser import _parse_yaml_frontmatter
    text = "tags: []"
    assert _parse_yaml_frontmatter(text)["tags"] == []


def test_yaml_pipe_block_with_no_items_returns_empty_string():
    from spec_parser import _parse_yaml_frontmatter
    text = "description: |\n"
    assert _parse_yaml_frontmatter(text) == {"description": ""}


def test_yaml_ignores_comments_and_blanks():
    from spec_parser import _parse_yaml_frontmatter
    text = "# top comment\n\nid: foo\ntitle: bar\n"
    result = _parse_yaml_frontmatter(text)
    assert result["id"] == "foo"
    assert result["title"] == "bar"


def test_yaml_skips_lines_without_colon():
    from spec_parser import _parse_yaml_frontmatter
    text = "id: epic-x\njust some prose line\ntitle: foo\n"
    result = _parse_yaml_frontmatter(text)
    assert result == {"id": "epic-x", "title": "foo"}


# ---------------------------------------------------------------------------
# _coerce_scalar
# ---------------------------------------------------------------------------

def test_coerce_scalar_empty_string():
    from spec_parser import _coerce_scalar
    assert _coerce_scalar("") == ""


def test_coerce_scalar_quoted_strings():
    from spec_parser import _coerce_scalar
    assert _coerce_scalar('"hello"') == "hello"
    assert _coerce_scalar("'world'") == "world"


def test_coerce_scalar_booleans():
    from spec_parser import _coerce_scalar
    assert _coerce_scalar("true") is True
    assert _coerce_scalar("True") is True
    assert _coerce_scalar("false") is False


def test_coerce_scalar_null():
    from spec_parser import _coerce_scalar
    assert _coerce_scalar("null") is None
    assert _coerce_scalar("~") is None


def test_coerce_scalar_int_and_float():
    from spec_parser import _coerce_scalar
    assert _coerce_scalar("42") == 42
    assert _coerce_scalar("3.14") == 3.14


def test_coerce_scalar_falls_back_to_string():
    from spec_parser import _coerce_scalar
    assert _coerce_scalar("not_a_number_or_bool") == "not_a_number_or_bool"


# ---------------------------------------------------------------------------
# _humanize_id
# ---------------------------------------------------------------------------

def test_humanize_id_with_dash():
    from spec_parser import _humanize_id
    assert _humanize_id("STORY-login-email") == "Login email"


def test_humanize_id_without_dash():
    from spec_parser import _humanize_id
    assert _humanize_id("loneid") == "Loneid"


# ---------------------------------------------------------------------------
# parse_epic_file edges
# ---------------------------------------------------------------------------

def test_parse_epic_file_returns_epic_with_no_stories(tmp_path):
    from spec_parser import parse_epic_file
    f = tmp_path / "epic.md"
    f.write_text(
        "---\nid: EPIC-x\ntitle: Test\n---\n\nA brief description.\n",
        encoding="utf-8",
    )
    epic = parse_epic_file(f)
    assert epic["id"] == "EPIC-x"
    assert epic["stories"] == []


def test_parse_epic_file_missing_required_field_raises(tmp_path):
    from spec_parser import parse_epic_file, MalformedSpec
    f = tmp_path / "epic.md"
    f.write_text("---\ntitle: Missing id\n---\n", encoding="utf-8")
    with pytest.raises(MalformedSpec) as exc_info:
        parse_epic_file(f)
    assert "missing required field" in str(exc_info.value).lower()


def test_parse_epic_file_includes_depends_on_when_list(tmp_path):
    from spec_parser import parse_epic_file
    f = tmp_path / "epic.md"
    f.write_text(
        "---\nid: EPIC-x\ntitle: T\ndepends_on: [EPIC-a, EPIC-b]\n---\n",
        encoding="utf-8",
    )
    epic = parse_epic_file(f)
    assert epic["depends_on"] == ["EPIC-a", "EPIC-b"]


# ---------------------------------------------------------------------------
# parse_story_chunk
# ---------------------------------------------------------------------------

def test_parse_story_chunk_depends_on_scalar(tmp_path):
    from spec_parser import parse_epic_file
    f = tmp_path / "epic.md"
    f.write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\ndepends_on: STORY-prereq\n\n"
        "Body line\n\n"
        "### Acceptance Criteria\n- [ ] AC1: works\n",
        encoding="utf-8",
    )
    epic = parse_epic_file(f)
    story = epic["stories"][0]
    assert story["depends_on"] == ["STORY-prereq"]


def test_parse_story_chunk_depends_on_list(tmp_path):
    from spec_parser import parse_epic_file
    f = tmp_path / "epic.md"
    f.write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\ndepends_on: [STORY-1, STORY-2]\n\n"
        "### Acceptance Criteria\n- [ ] AC1: works\n",
        encoding="utf-8",
    )
    epic = parse_epic_file(f)
    assert epic["stories"][0]["depends_on"] == ["STORY-1", "STORY-2"]


def test_parse_story_chunk_falls_back_to_humanized_title(tmp_path):
    from spec_parser import parse_epic_file
    f = tmp_path / "epic.md"
    f.write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-fix-bug\n\nDescription text.\n\n"
        "### Acceptance Criteria\n- [ ] AC1: works\n",
        encoding="utf-8",
    )
    epic = parse_epic_file(f)
    assert epic["stories"][0]["title"] == "Fix bug"


def test_parse_story_chunk_complexity_field(tmp_path):
    from spec_parser import parse_epic_file
    f = tmp_path / "epic.md"
    f.write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\ncomplexity: large\n\n"
        "### Acceptance Criteria\n- [ ] AC1: works\n",
        encoding="utf-8",
    )
    epic = parse_epic_file(f)
    assert epic["stories"][0]["estimated_complexity"] == "large"


def test_parse_story_chunk_complexity_invalid_ignored(tmp_path):
    from spec_parser import parse_epic_file
    f = tmp_path / "epic.md"
    f.write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\ncomplexity: gigantic\n\n"
        "### Acceptance Criteria\n- [ ] AC1: works\n",
        encoding="utf-8",
    )
    epic = parse_epic_file(f)
    assert epic["stories"][0]["estimated_complexity"] == "medium"


def test_parse_story_chunk_parses_tasks(tmp_path):
    from spec_parser import parse_epic_file
    f = tmp_path / "epic.md"
    f.write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\n\n"
        "### Acceptance Criteria\n- [ ] AC1: works\n\n"
        "### Tasks\n"
        "- [ ] TASK-impl `src/foo.py` (create)\n"
        "- [ ] TASK-test `tests/test_foo.py` (test)\n",
        encoding="utf-8",
    )
    epic = parse_epic_file(f)
    tasks = epic["stories"][0]["tasks"]
    assert len(tasks) == 2
    assert tasks[0]["files_to_touch"] == ["src/foo.py"]
    assert tasks[1]["type"] == "test"


# ---------------------------------------------------------------------------
# parse_specs — missing dirs, index.yaml ordering
# ---------------------------------------------------------------------------

def test_parse_specs_missing_specs_dir(tmp_path):
    from spec_parser import parse_specs, MalformedSpec
    with pytest.raises(MalformedSpec) as exc_info:
        parse_specs(tmp_path)
    assert "docs/specs/ does not exist" in str(exc_info.value)


def test_parse_specs_missing_epics_dir(tmp_path):
    from spec_parser import parse_specs, MalformedSpec
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    with pytest.raises(MalformedSpec) as exc_info:
        parse_specs(tmp_path)
    assert "epics" in str(exc_info.value)


def test_parse_specs_respects_index_yaml_order(tmp_path):
    from spec_parser import parse_specs
    specs_dir = tmp_path / "docs" / "specs"
    epics = specs_dir / "epics"
    epics.mkdir(parents=True)

    (epics / "01-a.md").write_text(
        "---\nid: EPIC-a\ntitle: A\n---\n", encoding="utf-8")
    (epics / "02-b.md").write_text(
        "---\nid: EPIC-b\ntitle: B\n---\n", encoding="utf-8")
    (epics / "03-c.md").write_text(
        "---\nid: EPIC-c\ntitle: C\n---\n", encoding="utf-8")

    (specs_dir / "index.yaml").write_text(
        "epic_order:\n  - 03-c.md\n  - 01-a.md\n",
        encoding="utf-8",
    )

    spec = parse_specs(tmp_path)
    ids = [e["id"] for e in spec["epics"]]
    assert ids == ["EPIC-c", "EPIC-a", "EPIC-b"]


# ---------------------------------------------------------------------------
# validate_specs — additional paths
# ---------------------------------------------------------------------------

def test_validate_specs_warns_on_vague_acceptance_criterion(tmp_path):
    from spec_parser import validate_specs
    epics_dir = tmp_path / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True)
    (epics_dir / "a.md").write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\n\n"
        "### Acceptance Criteria\n- [ ] AC1: short\n",
        encoding="utf-8",
    )
    report = validate_specs(tmp_path)
    assert any("short" in w or "very short" in w for w in report.warnings)


def test_validate_specs_warns_on_story_with_no_tasks(tmp_path):
    from spec_parser import validate_specs
    epics_dir = tmp_path / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True)
    (epics_dir / "a.md").write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\n\n"
        "### Acceptance Criteria\n- [ ] AC1: this is a long enough criterion\n",
        encoding="utf-8",
    )
    report = validate_specs(tmp_path)
    assert any("no tasks" in w for w in report.warnings)


def test_validate_specs_detects_duplicate_task_id(tmp_path):
    from spec_parser import validate_specs
    epics_dir = tmp_path / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True)
    (epics_dir / "a.md").write_text(
        "---\nid: EPIC-1\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\n\n"
        "### Acceptance Criteria\n- [ ] AC1: this is a long enough criterion\n\n"
        "### Tasks\n- [ ] TASK-x `src/a.py` (create)\n",
        encoding="utf-8",
    )
    (epics_dir / "b.md").write_text(
        "---\nid: EPIC-2\ntitle: T2\n---\n\n"
        "## Story: STORY-b\ntitle: B\n\n"
        "### Acceptance Criteria\n- [ ] AC1: this is a long enough criterion\n\n"
        "### Tasks\n- [ ] TASK-x `src/b.py` (create)\n",
        encoding="utf-8",
    )
    report = validate_specs(tmp_path)
    assert any("duplicate task id" in e for e in report.errors)


def test_validate_specs_detects_unknown_depends_on(tmp_path):
    from spec_parser import validate_specs
    epics_dir = tmp_path / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True)
    (epics_dir / "a.md").write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\ndepends_on: [STORY-missing]\n\n"
        "### Acceptance Criteria\n- [ ] AC1: this is a long enough criterion\n\n"
        "### Tasks\n- [ ] TASK-1 `src/a.py` (create)\n",
        encoding="utf-8",
    )
    report = validate_specs(tmp_path)
    assert any("unknown id" in e and "STORY-missing" in e for e in report.errors)


def test_validate_specs_handles_recursion_error(tmp_path, monkeypatch):
    from spec_parser import validate_specs
    import spec_parser

    def raise_recursion(root):
        raise RecursionError("too deep")
    monkeypatch.setattr(spec_parser, "parse_specs", raise_recursion)

    report = validate_specs(tmp_path)
    assert report.ok is False
    assert any("recursion" in e.lower() for e in report.errors)


def test_validate_specs_handles_generic_exception(tmp_path, monkeypatch):
    from spec_parser import validate_specs
    import spec_parser

    def raise_runtime(root):
        raise RuntimeError("unexpected failure")
    monkeypatch.setattr(spec_parser, "parse_specs", raise_runtime)

    report = validate_specs(tmp_path)
    assert report.ok is False
    assert any("unexpected error" in e.lower() for e in report.errors)


def test_validate_specs_warns_on_epic_with_no_stories(tmp_path):
    from spec_parser import validate_specs
    epics_dir = tmp_path / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True)
    (epics_dir / "empty.md").write_text(
        "---\nid: EPIC-empty\ntitle: T\n---\n\nJust a description.\n",
        encoding="utf-8",
    )
    report = validate_specs(tmp_path)
    assert any("no stories" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# _detect_cycles internals
# ---------------------------------------------------------------------------

def test_detect_cycles_simple_two_node_cycle():
    from spec_parser import _detect_cycles
    cycles = _detect_cycles({"A": ["B"], "B": ["A"]})
    assert len(cycles) == 1
    assert "A" in cycles[0] and "B" in cycles[0]


def test_detect_cycles_no_cycle():
    from spec_parser import _detect_cycles
    assert _detect_cycles({"A": ["B"], "B": []}) == []


def test_detect_cycles_self_loop():
    from spec_parser import _detect_cycles
    cycles = _detect_cycles({"A": ["A"]})
    assert len(cycles) == 1


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------

def test_validation_report_ok_when_empty():
    from spec_parser import ValidationReport
    r = ValidationReport()
    assert r.ok is True
    assert "OK" in r.render()


def test_validation_report_render_warnings_only():
    from spec_parser import ValidationReport
    r = ValidationReport(warnings=["heads up"])
    out = r.render()
    assert "WARN:" in out
    assert "heads up" in out
    assert r.ok is True
