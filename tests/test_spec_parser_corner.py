"""M17.5: spec_parser.py corner cases for 100%."""

from __future__ import annotations

from pathlib import Path

import pytest

from spec_parser import (
    MalformedSpec,
    _parse_yaml_frontmatter,
    parse_epic_file,
    parse_specs,
    validate_specs,
)


def test_yaml_block_list_with_embedded_blank_line():
    text = (
        "tags:\n"
        "  - a\n"
        "\n"
        "  - b\n"
        "next_key: x\n"
    )
    result = _parse_yaml_frontmatter(text)
    assert result["tags"] == ["a", "b"]
    assert result["next_key"] == "x"


def test_parse_epic_file_raises_on_unicode_decode_error(tmp_path, monkeypatch):
    epic = tmp_path / "epic.md"
    epic.write_text("---\nid: EPIC-x\ntitle: T\n---\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("epic.md"):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad byte")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    with pytest.raises(MalformedSpec) as exc_info:
        parse_epic_file(epic)
    assert "UTF-8" in str(exc_info.value)


def test_parse_epic_file_raises_on_oserror(tmp_path, monkeypatch):
    epic = tmp_path / "epic.md"
    epic.write_text("---\nid: EPIC-x\ntitle: T\n---\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("epic.md"):
            raise OSError("disk error")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    with pytest.raises(MalformedSpec) as exc_info:
        parse_epic_file(epic)
    assert "cannot read" in str(exc_info.value).lower()


def test_parse_epic_file_description_skips_comments_and_blanks(tmp_path):
    epic = tmp_path / "epic.md"
    epic.write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "# heading-comment\n"
        "\n"
        "real description text\n"
        "\n"
        "## Story: STORY-a\ntitle: A\n\n"
        "### Acceptance Criteria\n- [ ] AC1: x\n",
        encoding="utf-8",
    )
    epic_dict = parse_epic_file(epic)
    assert "real description text" in epic_dict["description"]
    assert "heading-comment" not in epic_dict["description"]


def test_parse_story_chunk_other_heading_resets_section(tmp_path):
    epic = tmp_path / "epic.md"
    epic.write_text(
        "---\nid: EPIC-x\ntitle: T\n---\n\n"
        "## Story: STORY-a\ntitle: A\n\n"
        "### Notes\nThis is a note section.\n\n"
        "### Acceptance Criteria\n- [ ] AC1: works fine\n",
        encoding="utf-8",
    )
    epic_dict = parse_epic_file(epic)
    story = epic_dict["stories"][0]
    assert len(story["acceptance_criteria"]) == 1


def test_parse_specs_index_yaml_oserror(tmp_path, monkeypatch):
    specs_dir = tmp_path / "docs" / "specs"
    epics = specs_dir / "epics"
    epics.mkdir(parents=True)
    (epics / "a.md").write_text(
        "---\nid: EPIC-a\ntitle: A\n---\n", encoding="utf-8",
    )
    (specs_dir / "index.yaml").write_text("epic_order: []\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("index.yaml"):
            raise OSError("disk")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    with pytest.raises(MalformedSpec) as exc_info:
        parse_specs(tmp_path)
    assert "cannot read" in str(exc_info.value).lower()


def test_parse_specs_index_yaml_unicode_decode(tmp_path, monkeypatch):
    specs_dir = tmp_path / "docs" / "specs"
    epics = specs_dir / "epics"
    epics.mkdir(parents=True)
    (epics / "a.md").write_text(
        "---\nid: EPIC-a\ntitle: A\n---\n", encoding="utf-8",
    )
    (specs_dir / "index.yaml").write_text("epic_order: []\n", encoding="utf-8")

    real_read = Path.read_text
    def flaky(self, *a, **kw):
        if str(self).endswith("index.yaml"):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
        return real_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    with pytest.raises(MalformedSpec) as exc_info:
        parse_specs(tmp_path)
    assert "UTF-8" in str(exc_info.value)


def test_validate_specs_detects_duplicate_epic_id(tmp_path):
    epics_dir = tmp_path / "docs" / "specs" / "epics"
    epics_dir.mkdir(parents=True)
    (epics_dir / "a.md").write_text(
        "---\nid: EPIC-dup\ntitle: First\n---\n\n"
        "## Story: STORY-a\ntitle: A\n\n"
        "### Acceptance Criteria\n- [ ] AC1: long enough text here\n\n"
        "### Tasks\n- [ ] TASK-1 `src/a.py` (create)\n",
        encoding="utf-8",
    )
    (epics_dir / "b.md").write_text(
        "---\nid: EPIC-dup\ntitle: Second\n---\n\n"
        "## Story: STORY-b\ntitle: B\n\n"
        "### Acceptance Criteria\n- [ ] AC1: long enough text here\n\n"
        "### Tasks\n- [ ] TASK-2 `src/b.py` (create)\n",
        encoding="utf-8",
    )
    report = validate_specs(tmp_path)
    assert any("duplicate epic id" in e for e in report.errors)
