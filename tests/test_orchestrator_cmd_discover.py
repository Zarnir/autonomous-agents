"""M16.2: cmd_discover branch coverage."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator


def _spec_report_ok():
    from spec_parser import ValidationReport
    return ValidationReport()


def _spec_report_bad():
    from spec_parser import ValidationReport
    return ValidationReport(errors=["dup story id"])


# ---------------------------------------------------------------------------
# cmd_discover happy paths
# ---------------------------------------------------------------------------

def test_cmd_discover_happy_path(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "Wrote spec\nVERDICT: SPEC_WRITTEN\n")
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_ok())

    args = argparse.Namespace(
        idea="a todo app", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    rc = orch.cmd_discover(args)
    assert rc == 0
    assert (project_root / "docs" / "specs" / "epics").exists()


def test_cmd_discover_chains_to_develop_when_then_develop(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "VERDICT: SPEC_WRITTEN\n")
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_ok())

    develop_called = {"n": 0}
    def fake_develop(args):
        develop_called["n"] += 1
        return 0
    monkeypatch.setattr(orch, "cmd_develop", fake_develop)

    args = argparse.Namespace(
        idea="thing", target_dir=str(project_root),
        then_develop=True, interactive=False,
    )
    rc = orch.cmd_discover(args)
    assert rc == 0
    assert develop_called["n"] == 1


# ---------------------------------------------------------------------------
# cmd_discover error paths
# ---------------------------------------------------------------------------

def test_cmd_discover_dies_when_target_dir_missing(import_orch, project_root):
    orch = import_orch
    ghost = project_root / "does-not-exist"
    args = argparse.Namespace(
        idea="x", target_dir=str(ghost), then_develop=False, interactive=False,
    )
    with pytest.raises(SystemExit):
        orch.cmd_discover(args)


def test_cmd_discover_returns_1_on_needs_clarification(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "Tell me more? What style of UI?\nNEEDS_CLARIFICATION\n")

    args = argparse.Namespace(
        idea="vague idea", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    rc = orch.cmd_discover(args)
    assert rc == 1


def test_cmd_discover_dies_when_no_verdict_emitted(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "I forgot to emit the verdict line.")

    args = argparse.Namespace(
        idea="x", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    with pytest.raises(SystemExit):
        orch.cmd_discover(args)


# ---------------------------------------------------------------------------
# cmd_discover validation-retry loop
# ---------------------------------------------------------------------------

def test_cmd_discover_retries_on_first_validation_failure_then_succeeds(
    import_orch, project_root, monkeypatch
):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "VERDICT: SPEC_WRITTEN\n")

    call_count = {"n": 0}
    def fake_validate(root):
        call_count["n"] += 1
        return _spec_report_bad() if call_count["n"] == 1 else _spec_report_ok()
    monkeypatch.setattr(orch, "validate_specs", fake_validate)

    args = argparse.Namespace(
        idea="x", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    rc = orch.cmd_discover(args)
    assert rc == 0
    assert call_count["n"] == 2


def test_cmd_discover_returns_1_when_retry_still_invalid(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.setattr(orch, "call_agent",
                        lambda *a, **kw: "VERDICT: SPEC_WRITTEN\n")
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_bad())

    args = argparse.Namespace(
        idea="x", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    rc = orch.cmd_discover(args)
    assert rc == 1


def test_cmd_discover_dies_when_retry_misses_verdict(import_orch, project_root, monkeypatch):
    orch = import_orch
    call_count = {"n": 0}

    def fake_agent(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "VERDICT: SPEC_WRITTEN\n"
        return "I forgot the verdict in retry."
    monkeypatch.setattr(orch, "call_agent", fake_agent)
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_bad())

    args = argparse.Namespace(
        idea="x", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    with pytest.raises(SystemExit):
        orch.cmd_discover(args)


# ---------------------------------------------------------------------------
# cmd_discover context-snippet inclusion
# ---------------------------------------------------------------------------

def test_cmd_discover_includes_readme_context(import_orch, project_root, monkeypatch):
    orch = import_orch
    (project_root / "README.md").write_text("# My Project\nA cool app\n", encoding="utf-8")

    captured = {}
    def fake_call(name, prompt, **kw):
        captured["prompt"] = prompt
        return "VERDICT: SPEC_WRITTEN\n"
    monkeypatch.setattr(orch, "call_agent", fake_call)
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_ok())

    args = argparse.Namespace(
        idea="x", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    orch.cmd_discover(args)
    assert "My Project" in captured["prompt"]
    assert "README.md" in captured["prompt"]


def test_cmd_discover_includes_package_json_context(import_orch, project_root, monkeypatch):
    orch = import_orch
    (project_root / "package.json").write_text('{"name": "neat-app"}', encoding="utf-8")

    captured = {}
    def fake_call(name, prompt, **kw):
        captured["prompt"] = prompt
        return "VERDICT: SPEC_WRITTEN\n"
    monkeypatch.setattr(orch, "call_agent", fake_call)
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_ok())

    args = argparse.Namespace(
        idea="x", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    orch.cmd_discover(args)
    assert "neat-app" in captured["prompt"]


def test_cmd_discover_includes_pyproject_context(import_orch, project_root, monkeypatch):
    orch = import_orch
    (project_root / "pyproject.toml").write_text(
        '[project]\nname = "py-thing"\n', encoding="utf-8"
    )

    captured = {}
    def fake_call(name, prompt, **kw):
        captured["prompt"] = prompt
        return "VERDICT: SPEC_WRITTEN\n"
    monkeypatch.setattr(orch, "call_agent", fake_call)
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_ok())

    args = argparse.Namespace(
        idea="x", target_dir=str(project_root),
        then_develop=False, interactive=False,
    )
    orch.cmd_discover(args)
    assert "py-thing" in captured["prompt"]


# ---------------------------------------------------------------------------
# cmd_discover interactive clarifications
# ---------------------------------------------------------------------------

def test_cmd_discover_interactive_appends_clarifications(import_orch, project_root, monkeypatch):
    orch = import_orch
    monkeypatch.delenv("NONINTERACTIVE", raising=False)

    answers = iter(["solo developer", "side project", "TypeScript + Postgres"])
    monkeypatch.setattr(orch, "_prompt_text", lambda *a, **kw: next(answers))
    monkeypatch.setattr(orch, "_WIZARD_AVAILABLE", True)

    captured = {}
    def fake_call(name, prompt, **kw):
        captured["prompt"] = prompt
        return "VERDICT: SPEC_WRITTEN\n"
    monkeypatch.setattr(orch, "call_agent", fake_call)
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_ok())

    args = argparse.Namespace(
        idea="thing", target_dir=str(project_root),
        then_develop=False, interactive=True,
    )
    rc = orch.cmd_discover(args)
    assert rc == 0
    assert "solo developer" in captured["prompt"]
    assert "TypeScript + Postgres" in captured["prompt"]


def test_cmd_discover_interactive_handles_wizard_abort(import_orch, project_root, monkeypatch):
    orch = import_orch

    def raise_abort(*a, **kw):
        raise orch._WizardAborted()
    monkeypatch.setattr(orch, "_prompt_text", raise_abort)
    monkeypatch.setattr(orch, "_WIZARD_AVAILABLE", True)

    monkeypatch.setattr(orch, "call_agent", lambda *a, **kw: "VERDICT: SPEC_WRITTEN\n")
    monkeypatch.setattr(orch, "validate_specs", lambda root: _spec_report_ok())

    args = argparse.Namespace(
        idea="thing", target_dir=str(project_root),
        then_develop=False, interactive=True,
    )
    rc = orch.cmd_discover(args)
    assert rc == 0
