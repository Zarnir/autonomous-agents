"""
Deterministic markdown spec parser.

Replaces the LLM-based @spec agent for the canonical authoring format
(see docs/specs/AUTHORING_GUIDE.md). No LLM round-trip — pure Python.

Input layout:
    docs/specs/
        index.yaml                  # project metadata + epic ordering
        epics/
            01-auth.md              # one epic per file (numeric prefix = priority)
            02-billing.md
            ...

Each epic file has YAML frontmatter and a structured body:

    ---
    id: EPIC-auth
    title: User Authentication
    priority: high
    depends_on: []
    ---

    # User Authentication

    Brief description.

    ## Story: STORY-login-email
    title: Login with email and password
    complexity: medium
    depends_on: []

    As a user, I want ...

    ### Acceptance Criteria
    - [ ] AC1: ...
    - [ ] AC2: ...

    ### Tasks
    - [ ] TASK-handler `app/Http/Controllers/AuthController.php` (create)
    - [ ] TASK-route `routes/api.php` (modify)

Output:
    Same JSON shape `@planner` already consumes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MalformedSpec(Exception):
    """Raised when a spec file violates the authoring contract."""

    def __init__(self, file: Path, line: Optional[int], message: str):
        self.file = file
        self.line = line
        self.message = message
        loc = f"{file}:{line}" if line is not None else str(file)
        super().__init__(f"{loc} -- {message}")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = []
        for e in self.errors:
            lines.append(f"ERROR: {e}")
        for w in self.warnings:
            lines.append(f"WARN:  {w}")
        if not lines:
            lines.append("OK: spec validates clean.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Minimal YAML loader (frontmatter only -- no full YAML dependency)
# ---------------------------------------------------------------------------


def _parse_yaml_frontmatter(text: str) -> dict:
    """
    Parse a YAML-ish frontmatter block. Supports the subset used by spec files:
    - scalar key: value
    - lists in flow form ([a, b, c]) or block form (key:\n  - a\n  - b)
    - quoted strings
    - integers, booleans
    """
    result: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "" or val == "|":
            block_items: list = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip().startswith("- "):
                    block_items.append(_coerce_scalar(nxt.strip()[2:].strip()))
                    j += 1
                    continue
                if nxt.strip() == "":
                    j += 1
                    continue
                break  # pragma: no cover — coverage tool misses this break
            if block_items:
                result[key] = block_items
                i = j
                continue
            else:
                result[key] = ""
                i += 1
                continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                result[key] = [_coerce_scalar(p.strip()) for p in inner.split(",")]
        else:
            result[key] = _coerce_scalar(val)
        i += 1
    return result


def _coerce_scalar(val: str):
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    if val.lower() in ("null", "~"):
        return None
    try:
        if "." not in val:
            return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ---------------------------------------------------------------------------
# File-level parsers
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Returns (frontmatter_dict, body, body_start_line_offset)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text, 0
    fm_text = m.group(1)
    fm = _parse_yaml_frontmatter(fm_text)
    body_offset = text[: m.end()].count("\n")
    return fm, text[m.end():], body_offset


_STORY_HEADING_RE = re.compile(r"^##\s+Story:\s*(STORY-[a-zA-Z0-9_-]+)\s*$", re.MULTILINE)
_TASK_LINE_RE = re.compile(
    r"^\s*-\s*\[[ xX]\]\s*"
    r"(TASK-[a-zA-Z0-9_-]+)\s+"
    r"`([^`]+)`\s*"
    r"\((create|modify|delete|test|config)\)\s*$"
)
_AC_LINE_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*(AC\d+:\s*.+?)\s*$")
_STORY_FIELD_RE = re.compile(r"^([a-z_]+)\s*:\s*(.+)\s*$")


def parse_epic_file(path: Path) -> dict:
    """Parse one epic file. Returns the epic dict for the spec JSON."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise MalformedSpec(path, 1, f"file is not valid UTF-8: {e}")
    except OSError as e:
        raise MalformedSpec(path, 1, f"cannot read file: {e}")
    fm, body, body_offset = _split_frontmatter(text)
    if not fm:
        raise MalformedSpec(path, 1, "missing YAML frontmatter (must start with --- on line 1)")

    for required in ("id", "title"):
        if not fm.get(required):
            raise MalformedSpec(path, 1, f"frontmatter missing required field: {required!r}")

    epic_id = str(fm["id"])
    if not epic_id.startswith("EPIC-"):
        raise MalformedSpec(path, 1, f"epic id {epic_id!r} must start with 'EPIC-'")

    epic = {
        "id": epic_id,
        "title": str(fm["title"]),
        "description": "",
        "priority": str(fm.get("priority", "medium")),
        "stories": [],
    }
    if "depends_on" in fm:
        epic["depends_on"] = fm["depends_on"] if isinstance(fm["depends_on"], list) else []

    story_starts = list(_STORY_HEADING_RE.finditer(body))
    if not story_starts:
        return epic

    pre = body[: story_starts[0].start()]
    epic["description"] = _extract_description(pre)

    for idx, m in enumerate(story_starts):
        story_id = m.group(1)
        story_body_start = m.end()
        story_body_end = (
            story_starts[idx + 1].start() if idx + 1 < len(story_starts) else len(body)
        )
        chunk = body[story_body_start:story_body_end]
        story = parse_story_chunk(
            path, story_id, chunk, epic_id, body_offset + body[: m.start()].count("\n")
        )
        epic["stories"].append(story)

    return epic


def _extract_description(pre_block: str) -> str:
    paragraphs = []
    for line in pre_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        paragraphs.append(line)
    return " ".join(paragraphs).strip()


def parse_story_chunk(
    epic_path: Path, story_id: str, chunk: str, epic_id: str, line_offset: int
) -> dict:
    story = {
        "id": story_id,
        "epic_id": epic_id,
        "title": "",
        "description": "",
        "acceptance_criteria": [],
        "depends_on": [],
        "depends_on_inferred": False,
        "estimated_complexity": "medium",
        "tasks": [],
    }

    section = "fields"
    description_lines: list[str] = []
    for raw in chunk.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("### "):
            heading = stripped[4:].strip().lower()
            if "acceptance" in heading:
                section = "ac"
            elif "task" in heading:
                section = "tasks"
            else:
                section = "other"
            continue

        if section == "fields":
            fm = _STORY_FIELD_RE.match(stripped)
            if fm:
                key, val = fm.group(1), fm.group(2)
                _apply_story_field(story, key, val, epic_path, line_offset)
                continue
            if stripped:
                description_lines.append(stripped)
            continue

        if section == "ac":
            ac_m = _AC_LINE_RE.match(line)
            if ac_m:
                story["acceptance_criteria"].append(ac_m.group(1).strip())
            continue

        if section == "tasks":
            task_m = _TASK_LINE_RE.match(line)
            if task_m:
                tid, path_str, ttype = task_m.group(1), task_m.group(2), task_m.group(3)
                story["tasks"].append(
                    {
                        "id": tid,
                        "story_id": story_id,
                        "title": _humanize_id(tid),
                        "files_to_touch": [path_str],
                        "type": ttype,
                        "status": "pending",
                    }
                )
            continue

    if not story["title"]:
        story["title"] = _humanize_id(story_id)
    story["description"] = " ".join(description_lines).strip()
    return story


def _apply_story_field(story: dict, key: str, val: str, file: Path, line_offset: int) -> None:
    if key == "title":
        story["title"] = val.strip().strip('"').strip("'")
    elif key == "complexity":
        v = val.strip().lower()
        if v in ("small", "medium", "large"):
            story["estimated_complexity"] = v
    elif key == "depends_on":
        v = val.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            story["depends_on"] = [p.strip() for p in inner.split(",") if p.strip()]
        elif v:
            story["depends_on"] = [v]


def _humanize_id(id_: str) -> str:
    parts = id_.split("-", 1)
    rest = parts[1] if len(parts) == 2 else id_
    return rest.replace("-", " ").strip().capitalize()


# ---------------------------------------------------------------------------
# Top-level: parse a project's docs/specs tree
# ---------------------------------------------------------------------------


def parse_specs(project_root: Path) -> dict:
    """
    Parse the canonical spec layout. Returns the spec JSON dict.
    Raises MalformedSpec on the first hard error.
    """
    specs_dir = project_root / "docs" / "specs"
    epics_dir = specs_dir / "epics"

    if not specs_dir.exists():
        raise MalformedSpec(specs_dir, None, "docs/specs/ does not exist. Run init.sh or create it manually.")

    if not epics_dir.exists() or not any(epics_dir.glob("*.md")):
        raise MalformedSpec(epics_dir, None, "docs/specs/epics/ has no .md files. See docs/specs/AUTHORING_GUIDE.md")

    epic_files = sorted(epics_dir.glob("*.md"))

    index_yaml = specs_dir / "index.yaml"
    if index_yaml.exists():
        try:
            idx_text = index_yaml.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise MalformedSpec(index_yaml, 1, f"file is not valid UTF-8: {e}")
        except OSError as e:
            raise MalformedSpec(index_yaml, 1, f"cannot read file: {e}")
        idx_data = _parse_yaml_frontmatter(idx_text)
        order = idx_data.get("epic_order", [])
        if isinstance(order, list) and order:
            ordered: list[Path] = []
            by_name = {f.name: f for f in epic_files}
            for entry in order:
                f = by_name.get(str(entry))
                if f:
                    ordered.append(f)
            for f in epic_files:
                if f not in ordered:
                    ordered.append(f)
            epic_files = ordered

    epics = []
    source_files: list[str] = []
    if index_yaml.exists():
        source_files.append(str(index_yaml.relative_to(project_root)))
    for ef in epic_files:
        epic = parse_epic_file(ef)
        epics.append(epic)
        source_files.append(str(ef.relative_to(project_root)))

    return {
        "methodology": "structured",
        "source_files": source_files,
        "epics": epics,
    }


def validate_specs(project_root: Path) -> ValidationReport:
    """Run the full validation suite. Returns a report; never raises."""
    report = ValidationReport()
    try:
        spec = parse_specs(project_root)
    except MalformedSpec as e:
        report.errors.append(str(e))
        return report
    except RecursionError:
        report.errors.append(
            "spec parser hit Python recursion limit — "
            "likely a very deep dependency chain (>1000 stories). "
            "Flatten or split the spec."
        )
        return report
    except Exception as e:
        report.errors.append(
            f"unexpected error during spec parsing: {type(e).__name__}: {e}"
        )
        return report

    seen_epic_ids: set[str] = set()
    seen_story_ids: set[str] = set()
    seen_task_ids: set[str] = set()

    for epic in spec["epics"]:
        if epic["id"] in seen_epic_ids:
            report.errors.append(f"duplicate epic id: {epic['id']}")
        seen_epic_ids.add(epic["id"])

        if not epic["stories"]:
            report.warnings.append(f"epic {epic['id']!r} has no stories")

        for story in epic["stories"]:
            if story["id"] in seen_story_ids:
                report.errors.append(f"duplicate story id: {story['id']}")
            seen_story_ids.add(story["id"])

            if not story["acceptance_criteria"]:
                report.errors.append(
                    f"story {story['id']!r} has no acceptance criteria -- "
                    "@test cannot generate coverage"
                )
            for ac in story["acceptance_criteria"]:
                if len(ac.split()) < 5:
                    report.warnings.append(
                        f"story {story['id']!r} criterion {ac!r} is very short -- "
                        "may produce weak tests"
                    )

            if not story["tasks"]:
                report.warnings.append(
                    f"story {story['id']!r} has no tasks -- @make will operate "
                    "without scope hints"
                )
            for task in story["tasks"]:
                if task["id"] in seen_task_ids:
                    report.errors.append(f"duplicate task id: {task['id']}")
                seen_task_ids.add(task["id"])

    for epic in spec["epics"]:
        for story in epic["stories"]:
            for dep in story.get("depends_on", []):
                if dep not in seen_story_ids:
                    report.errors.append(
                        f"story {story['id']!r} depends_on unknown id: {dep!r}"
                    )

    graph = {
        s["id"]: list(s.get("depends_on", []))
        for e in spec["epics"]
        for s in e["stories"]
    }
    cycles = _detect_cycles(graph)
    for cyc in cycles:
        report.errors.append(f"dependency cycle: {' -> '.join(cyc)}")

    return report


def _detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    cycles: list[list[str]] = []
    stack: list[str] = []

    def visit(n: str) -> None:
        color[n] = GRAY
        stack.append(n)
        for nxt in graph.get(n, []):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                idx = stack.index(nxt)
                cycles.append(stack[idx:] + [nxt])
            elif color[nxt] == WHITE:
                visit(nxt)
        stack.pop()
        color[n] = BLACK

    for n in list(graph.keys()):
        if color[n] == WHITE:
            visit(n)
    return cycles
