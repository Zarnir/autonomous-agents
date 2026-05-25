"""End-to-end smoke test for `aa-orchestrator develop --dry-run`.

Runs the deterministic spec->plan path hermetically. No LLM calls. No agent
subprocesses. Validates that:
  1. The pipeline locates and parses a real spec tree.
  2. The deterministic planner emits a valid progress.json.
  3. The plan contains the expected story in `pending` state.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = REPO_ROOT / "lib" / "orchestrator.py"


@pytest.mark.e2e
def test_develop_dry_run_produces_valid_plan(project_root: Path):
    """A complete one-epic / one-story spec -> progress.json reaches pending."""
    specs = project_root / "docs" / "specs" / "epics"
    specs.mkdir(parents=True)
    (specs / "01-hello.md").write_text(
        """---
id: EPIC-hello
title: Hello World Epic
priority: medium
---

The classic hello-world epic.

## Story: STORY-print-hello

title: Print hello
complexity: small

### Acceptance Criteria

- [ ] AC1: The script prints exactly the string "hello world" when invoked

### Tasks

- [ ] TASK-create-script `src/hello.py` (create)
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "develop", "--dry-run"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"develop --dry-run failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    progress = project_root / ".opencode" / "progress.json"
    assert progress.exists(), f"progress.json not created. stdout: {result.stdout}"

    data = json.loads(progress.read_text(encoding="utf-8"))
    assert data["schema_version"] == "2.0"
    assert len(data["epics"]) == 1
    epic = data["epics"][0]
    assert epic["id"] == "EPIC-hello"
    assert len(epic["stories"]) == 1
    story = epic["stories"][0]
    assert story["id"] == "STORY-print-hello"
    assert story["status"] == "pending"
    assert story.get("execution_wave") == 1


@pytest.mark.e2e
def test_validate_subcommand_reports_ok_for_valid_specs(project_root: Path):
    specs = project_root / "docs" / "specs" / "epics"
    specs.mkdir(parents=True)
    (specs / "01-valid.md").write_text(
        """---
id: EPIC-valid
title: Valid Epic
---

## Story: STORY-valid

title: A valid story
complexity: small

### Acceptance Criteria

- [ ] AC1: this criterion has the required minimum length to pass validation

### Tasks

- [ ] TASK-x `src/x.py` (create)
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "validate"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"validate failed: {result.stderr}"


@pytest.mark.e2e
def test_validate_reports_errors_for_malformed_specs_without_traceback(project_root: Path):
    """Regression: validate should never raise a traceback — it returns a report."""
    specs = project_root / "docs" / "specs" / "epics"
    specs.mkdir(parents=True)
    (specs / "01-no-fm.md").write_text("## Story: STORY-x\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "validate"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "Traceback" not in result.stderr, (
        f"validate crashed instead of reporting: {result.stderr}"
    )
    assert result.returncode != 0


@pytest.mark.e2e
def test_orchestrator_handles_non_utf8_spec_gracefully(project_root: Path):
    """Regression: non-UTF-8 file used to raise raw UnicodeDecodeError."""
    specs = project_root / "docs" / "specs" / "epics"
    specs.mkdir(parents=True)
    (specs / "01-bad.md").write_bytes(b"---\nid: EPIC-x\ntitle: caf\xe9\n---\n")

    result = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "validate"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "Traceback" not in result.stderr, (
        f"non-UTF-8 spec crashed validate: {result.stderr}"
    )
    output = (result.stdout + result.stderr).lower()
    assert "utf-8" in output
