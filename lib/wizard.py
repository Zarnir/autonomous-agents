"""Interactive prompt helpers + pipeline state detection (M12).

This module is the only place in the codebase that calls `input()`. Everywhere
else stays non-interactive (env / flags). The wizard subcommands import these
helpers and the `detect_state` function to drive interactive UX.

Design principles:
- **Stdlib only.** No `prompt_toolkit` / `inquirer` / `click` — `input()` + `sys.stdout`.
- **Non-interactive override.** Setting `NONINTERACTIVE=1` makes every prompt
  return its default without blocking. Used by CI and tests.
- **Clean Ctrl-C.** SIGINT during a prompt raises `WizardAborted` instead of
  KeyboardInterrupt so callers can persist state before exiting.
- **State detection.** `detect_state(project_root)` returns a `PipelineState`
  enum and a `next_action_hint` describing what the user should do next.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

class WizardAborted(RuntimeError):
    """Raised when the user aborts a wizard prompt with Ctrl-C or EOF."""


def _is_noninteractive() -> bool:
    return os.environ.get("NONINTERACTIVE", "").strip().lower() in ("1", "true", "yes")


def _read_line(prompt: str) -> str:
    """Wrapped `input()` that converts EOFError/KeyboardInterrupt into WizardAborted."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt) as e:
        raise WizardAborted("user aborted prompt") from e


def prompt_yes_no(message: str, default: bool = False) -> bool:
    """Ask a yes/no question. Returns the user's choice (or `default` on Enter)."""
    if _is_noninteractive():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        raw = _read_line(message.rstrip() + suffix).strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  please answer y or n (or press Enter for default)")


def prompt_text(
    message: str,
    default: str = "",
    validator: Optional[Callable[[str], Optional[str]]] = None,
) -> str:
    """Ask for a free-form string.

    `validator` returns None if the value is acceptable, or an error message string
    explaining why it must be retried.
    """
    if _is_noninteractive():
        if validator is not None:
            err = validator(default)
            if err:
                raise WizardAborted(f"noninteractive default rejected by validator: {err}")
        return default
    suffix = f" [{default}] " if default else " "
    while True:
        raw = _read_line(message.rstrip() + suffix).strip()
        value = raw or default
        if validator is None:
            return value
        err = validator(value)
        if err is None:
            return value
        print(f"  {err}")


def prompt_choice(message: str, options: list[str], default_index: int = 0) -> str:
    """Ask the user to pick one of a list of options. Returns the chosen string."""
    if not options:
        raise ValueError("prompt_choice: options must be non-empty")
    if _is_noninteractive():
        return options[default_index]
    print(message.rstrip())
    for i, opt in enumerate(options):
        marker = " (default)" if i == default_index else ""
        print(f"  {i + 1}) {opt}{marker}")
    while True:
        raw = _read_line(f"Choice [1-{len(options)}]: ").strip()
        if not raw:
            return options[default_index]
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print(f"  please enter a number between 1 and {len(options)}")


# ---------------------------------------------------------------------------
# Pipeline state detection
# ---------------------------------------------------------------------------

class PipelineState(Enum):
    NOT_INITIALIZED = "not_initialized"
    BOOTSTRAPPED_NO_SPEC = "bootstrapped_no_spec"
    SPEC_WRITTEN_NO_PLAN = "spec_written_no_plan"
    SPEC_INVALID = "spec_invalid"
    PLAN_PENDING = "plan_pending"
    SPRINT_PLANNED = "sprint_planned"
    SPRINT_IN_PROGRESS = "sprint_in_progress"
    SPRINT_COMPLETED_MORE_REMAINS = "sprint_completed_more_remains"
    ALL_COMPLETE = "all_complete"
    GATE_FAILED = "gate_failed"
    OPEN_RFCS = "open_rfcs"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass
class StateReport:
    state: PipelineState
    summary: str           # one-line human description of where the user is
    next_action: str       # one-line suggestion of what to do next
    command: Optional[str] # the actual `aa-orchestrator ...` command to run, or None


def _has_open_rfcs(root: Path) -> bool:
    rfc_dir = root / "docs" / "rfc"
    if not rfc_dir.exists():
        return False
    for path in rfc_dir.glob("*.md"):
        try:
            head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:10])
        except (OSError, UnicodeDecodeError):
            continue
        if "status: open" in head.lower():
            return True
    return False


def detect_state(project_root: Optional[Path] = None) -> StateReport:
    """Inspect filesystem + progress.json; return a StateReport.

    No LLM calls. No file mutations. Safe to call repeatedly.
    """
    root = project_root or Path.cwd()
    opencode_dir = root / ".opencode"
    progress_file = opencode_dir / "progress.json"
    epics_dir = root / "docs" / "specs" / "epics"

    # 1. Not initialized
    if not opencode_dir.exists() and not (root / "docs" / "specs").exists():
        return StateReport(
            state=PipelineState.NOT_INITIALIZED,
            summary="no autonomous-agents config in this directory",
            next_action="bootstrap the project (creates .opencode/, docs/specs/, slash commands)",
            command="bash $AA_HOME/init.sh --runner claude",
        )

    # 2. Open RFCs always demand attention
    if _has_open_rfcs(root):
        return StateReport(
            state=PipelineState.OPEN_RFCS,
            summary="one or more RFCs are open under docs/rfc/ — pipeline should not advance",
            next_action="invoke @architect to triage each open RFC",
            command="aa-orchestrator rfc",
        )

    # 3. Progress.json examination (if present)
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return StateReport(
                state=PipelineState.SPEC_INVALID,
                summary="progress.json is corrupt or unreadable",
                next_action="back up progress.json then re-run develop --force",
                command="aa-orchestrator develop --force",
            )

        status = data.get("status")
        if status == "gate_failed":
            return StateReport(
                state=PipelineState.GATE_FAILED,
                summary="a production gate failed — sprint cannot advance",
                next_action="inspect data.gate_failures, fix manually, then re-run sprint or develop",
                command="aa-orchestrator status",
            )
        if status == "budget_exceeded":
            return StateReport(
                state=PipelineState.BUDGET_EXCEEDED,
                summary="cost cap reached mid-run — state was persisted before halting",
                next_action="raise pipeline.max_budget_usd in config, then resume",
                command="aa-orchestrator resume",
            )
        if status == "completed":
            return StateReport(
                state=PipelineState.ALL_COMPLETE,
                summary="all stories complete — review docs/sprints/ and docs/releases/",
                next_action="nothing to do; project is shipped",
                command=None,
            )

        sprints = data.get("sprints", [])
        all_pending = all(
            s.get("status") == "pending"
            for epic in data.get("epics", [])
            for s in epic.get("stories", [])
        )
        any_pending = any(
            s.get("status") == "pending"
            for epic in data.get("epics", [])
            for s in epic.get("stories", [])
        )

        if sprints:
            last = sprints[-1]
            last_status = last.get("status")
            if last_status == "planned":
                return StateReport(
                    state=PipelineState.SPRINT_PLANNED,
                    summary=f"sprint {last.get('number')} is planned but not started",
                    next_action="execute the planned sprint",
                    command="aa-orchestrator sprint start",
                )
            if last_status == "in_progress":
                return StateReport(
                    state=PipelineState.SPRINT_IN_PROGRESS,
                    summary=f"sprint {last.get('number')} is in progress",
                    next_action="resume the in-flight sprint",
                    command="aa-orchestrator resume",
                )
            if last_status == "completed" and any_pending:
                return StateReport(
                    state=PipelineState.SPRINT_COMPLETED_MORE_REMAINS,
                    summary=f"sprint {last.get('number')} done; pending stories remain",
                    next_action="plan the next sprint",
                    command="aa-orchestrator sprint plan",
                )

        if all_pending and not sprints:
            return StateReport(
                state=PipelineState.PLAN_PENDING,
                summary="plan built, no sprint started yet",
                next_action="plan the first sprint",
                command="aa-orchestrator sprint plan",
            )

    # 4. Spec exists but no plan yet
    if epics_dir.exists() and any(epics_dir.glob("*.md")):
        return StateReport(
            state=PipelineState.SPEC_WRITTEN_NO_PLAN,
            summary="spec files exist but no progress.json — never planned",
            next_action="validate then build the plan",
            command="aa-orchestrator validate && aa-orchestrator develop --dry-run",
        )

    # 5. Bootstrapped but no spec
    if opencode_dir.exists():
        return StateReport(
            state=PipelineState.BOOTSTRAPPED_NO_SPEC,
            summary="bootstrap done; no spec written yet under docs/specs/epics/",
            next_action="generate a spec from a one-line idea (or write one by hand)",
            command='aa-orchestrator discover "<your one-line product idea>"',
        )

    # Fallback — shouldn't happen given the checks above
    return StateReport(
        state=PipelineState.NOT_INITIALIZED,
        summary="unknown project state",
        next_action="run init.sh to bootstrap",
        command="bash $AA_HOME/init.sh --runner claude",
    )
