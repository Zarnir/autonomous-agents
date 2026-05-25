"""Unit tests for lib/spec_parser.py.

Each test creates an isolated docs/specs/epics/ tree in tmp_path. No real spec
or production data is touched.
"""

from __future__ import annotations

import pytest

from spec_parser import (
    MalformedSpec,
    parse_specs,
    validate_specs,
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_parse_specs_returns_one_epic_with_one_story(sample_epic, project_root):
    spec = parse_specs(project_root)
    assert spec["methodology"] == "structured"
    assert len(spec["epics"]) == 1
    epic = spec["epics"][0]
    assert epic["id"] == "EPIC-sample"
    assert epic["title"] == "Sample Epic for Tests"
    assert epic["priority"] == "medium"
    assert len(epic["stories"]) == 1
    story = epic["stories"][0]
    assert story["id"] == "STORY-hello-world"
    assert story["title"] == "Print hello world"
    assert story["estimated_complexity"] == "small"
    assert len(story["acceptance_criteria"]) == 1
    assert "hello world" in story["acceptance_criteria"][0].lower()
    assert len(story["tasks"]) == 1
    assert story["tasks"][0]["files_to_touch"] == ["src/hello.py"]


def test_validate_specs_returns_ok_for_valid_input(sample_epic, project_root):
    report = validate_specs(project_root)
    assert report.ok, f"unexpected errors: {report.errors}"


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------

def test_missing_frontmatter_raises_malformed_spec(specs_dir, project_root):
    bad = specs_dir / "epics" / "01-no-fm.md"
    bad.write_text("## Story: STORY-orphan\ntitle: no frontmatter\n", encoding="utf-8")
    with pytest.raises(MalformedSpec) as exc:
        parse_specs(project_root)
    assert "frontmatter" in str(exc.value).lower()


def test_bad_epic_id_prefix_rejected(specs_dir, project_root):
    bad = specs_dir / "epics" / "01-bad-prefix.md"
    bad.write_text(
        "---\nid: NOT-EPIC-prefix\ntitle: bad\n---\n", encoding="utf-8"
    )
    with pytest.raises(MalformedSpec) as exc:
        parse_specs(project_root)
    assert "EPIC-" in str(exc.value)


def test_validate_catches_duplicate_story_ids(specs_dir, project_root):
    (specs_dir / "epics" / "01-a.md").write_text(
        """---
id: EPIC-a
title: A
---

## Story: STORY-dup

title: dup-1
complexity: small

### Acceptance Criteria

- [ ] AC1: this is one valid acceptance criterion line

### Tasks

- [ ] TASK-x `src/a.py` (create)
""",
        encoding="utf-8",
    )
    (specs_dir / "epics" / "02-b.md").write_text(
        """---
id: EPIC-b
title: B
---

## Story: STORY-dup

title: dup-2
complexity: small

### Acceptance Criteria

- [ ] AC1: another valid criterion that must appear here

### Tasks

- [ ] TASK-y `src/b.py` (create)
""",
        encoding="utf-8",
    )
    report = validate_specs(project_root)
    assert not report.ok
    assert any("STORY-dup" in e or "duplicate" in e.lower() for e in report.errors)


def test_validate_catches_dependency_cycle(specs_dir, project_root):
    (specs_dir / "epics" / "01-cycle.md").write_text(
        """---
id: EPIC-cycle
title: Cycle
---

## Story: STORY-a

title: a
complexity: small
depends_on: [STORY-b]

### Acceptance Criteria

- [ ] AC1: criterion line for the first story in the cycle

### Tasks

- [ ] TASK-a `src/a.py` (create)

## Story: STORY-b

title: b
complexity: small
depends_on: [STORY-a]

### Acceptance Criteria

- [ ] AC1: criterion line for the second story closing the cycle

### Tasks

- [ ] TASK-b `src/b.py` (create)
""",
        encoding="utf-8",
    )
    report = validate_specs(project_root)
    assert not report.ok
    joined = " | ".join(report.errors).lower()
    assert "cycle" in joined or "circular" in joined


def test_validate_warns_on_zero_acceptance_criteria(specs_dir, project_root):
    (specs_dir / "epics" / "01-empty.md").write_text(
        """---
id: EPIC-empty
title: Empty
---

## Story: STORY-no-ac

title: no acceptance
complexity: small

### Tasks

- [ ] TASK-x `src/x.py` (create)
""",
        encoding="utf-8",
    )
    report = validate_specs(project_root)
    assert not report.ok
    joined = " | ".join(report.errors).lower()
    assert "acceptance" in joined or "ac" in joined


# ---------------------------------------------------------------------------
# Encoding hardening
# ---------------------------------------------------------------------------

def test_non_utf8_epic_raises_malformed_spec_not_unicode_error(specs_dir, project_root):
    """Regression: invalid UTF-8 used to bubble raw UnicodeDecodeError."""
    bad = specs_dir / "epics" / "01-bad-encoding.md"
    bad.write_bytes(b"---\nid: EPIC-encoding\ntitle: caf\xe9\n---\n## Story: STORY-x\n")
    with pytest.raises(MalformedSpec) as exc:
        parse_specs(project_root)
    assert "utf-8" in str(exc.value).lower()


def test_validate_specs_returns_report_on_encoding_failure(specs_dir, project_root):
    """validate_specs must never raise, even on encoding errors."""
    bad = specs_dir / "epics" / "01-bad-encoding.md"
    bad.write_bytes(b"---\nid: EPIC-x\ntitle: caf\xe9\n---\n")
    report = validate_specs(project_root)
    assert not report.ok
    assert any("utf-8" in e.lower() for e in report.errors)
