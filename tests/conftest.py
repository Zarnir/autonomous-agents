"""Shared pytest fixtures.

Adds `lib/` to sys.path so tests can `import orchestrator` and `import spec_parser`
without packaging or editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty project root with cwd pointed at it.

    orchestrator/spec_parser use relative paths like Path("docs/specs") and
    Path(".opencode/progress.json"), so tests must chdir into a clean root.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def specs_dir(project_root: Path) -> Path:
    """Project root with docs/specs/ and docs/specs/epics/ scaffolded but empty."""
    specs = project_root / "docs" / "specs"
    (specs / "epics").mkdir(parents=True)
    return specs


@pytest.fixture
def sample_epic(specs_dir: Path) -> Path:
    """Write a minimal valid epic with one story and one task, return its path."""
    epic_path = specs_dir / "epics" / "01-sample.md"
    epic_path.write_text(
        """---
id: EPIC-sample
title: Sample Epic for Tests
priority: medium
---

A trivial epic used by the test suite.

## Story: STORY-hello-world

title: Print hello world
complexity: small

### Acceptance Criteria

- [ ] AC1: The script prints exactly the string "hello world" when invoked

### Tasks

- [ ] TASK-create-script `src/hello.py` (create)
""",
        encoding="utf-8",
    )
    return epic_path


@pytest.fixture
def progress_file(project_root: Path) -> Path:
    """Project root with .opencode/ scaffolded; progress.json not yet written."""
    (project_root / ".opencode").mkdir()
    return project_root / ".opencode" / "progress.json"


@pytest.fixture
def stub_agent_path() -> Path:
    """Path to the canned-response stub agent (used by e2e smoke test)."""
    p = Path(__file__).parent / "stubs" / "stub_agent.py"
    assert p.exists(), f"stub agent missing at {p}"
    return p
