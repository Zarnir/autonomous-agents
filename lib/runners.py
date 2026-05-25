"""Runner adapters for invoking LLM agents.

Two backends are supported:
- OpenCodeRunner: wraps `opencode run --agent <name>` (the original runner).
- ClaudeCodeRunner: wraps `claude -p` with system-prompt injection from the
  agent's `.opencode/agents/<name>.md` frontmatter.

Both runners conform to the `Runner` protocol so `call_agent` can dispatch
based on the `AA_RUNNER` env or `pipeline.runner` config field.
"""

from __future__ import annotations

import os
import pty
import re
import select
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


# ---------------------------------------------------------------------------
# Agent file parsing — shared between runners that need system prompts
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    """A named skill declared in a multi-skill agent's frontmatter."""
    id: str
    description: str = ""
    inputs: list[str] = field(default_factory=list)


@dataclass
class AgentDef:
    """Parsed agent definition from .opencode/agents/<name>.md."""
    name: str
    description: str
    system_prompt: str
    bash_allow: list[str]
    bash_deny: list[str]
    edit_allowed: bool
    write_allowed: bool
    webfetch_allowed: bool
    websearch_allowed: bool
    skills: list[Skill] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    consult_agents: list[str] = field(default_factory=list)

    def find_skill(self, skill_id: str) -> Optional[Skill]:
        for s in self.skills:
            if s.id == skill_id:
                return s
        return None


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split markdown into (frontmatter, body). Returns ('', text) if none."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[4:end], text[end + 5:]


def parse_agent_file(agent_name: str, agents_dir: Optional[Path] = None) -> AgentDef:
    """Parse .opencode/agents/<name>.md once. Cheap enough to repeat per call."""
    agents_dir = agents_dir or Path(".opencode/agents")
    path = agents_dir / f"{agent_name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Agent definition not found: {path}. "
            "Did `install.sh` finish, or are you running from a project that ran `init.sh`?"
        )
    fm_text, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    description = ""
    bash_allow: list[str] = []
    bash_deny: list[str] = []
    edit_allowed = True
    write_allowed = True
    webfetch_allowed = False
    websearch_allowed = False

    skills: list[Skill] = []
    imports: list[str] = []
    consult_agents: list[str] = []
    current_skill: Optional[Skill] = None

    # Lightweight YAML scan — avoid pulling pyyaml since we only need a few fields
    current_section: Optional[str] = None
    for raw_line in fm_text.splitlines():
        # Top-level field detection (resets section)
        if raw_line.startswith("description:"):
            description = raw_line.split(":", 1)[1].strip()
            if current_skill:
                skills.append(current_skill)
                current_skill = None
            current_section = None
            continue
        if raw_line.startswith("permission:"):
            if current_skill:
                skills.append(current_skill)
                current_skill = None
            current_section = "permission"
            continue
        if raw_line.startswith("skills:"):
            current_section = "skills"
            continue
        if raw_line.startswith("imports:"):
            # Imports can be inline (`imports: [a, b]`) or block-style:
            #   imports:
            #     - skill-a
            #     - skill-b
            if current_skill:
                skills.append(current_skill)
                current_skill = None
            value = raw_line.split(":", 1)[1].strip()
            if value.startswith("["):
                inner = value.strip("[]").strip()
                if inner:
                    imports.extend(
                        item.strip().strip("\"'")
                        for item in inner.split(",")
                        if item.strip()
                    )
                current_section = None
            else:
                current_section = "imports"
            continue
        if raw_line.startswith("consult_agents:"):
            # M19: peer agents this agent may delegate to via DELEGATE_TO marker.
            # Same shape as `imports:` — inline `[a, b]` or block `- a`.
            if current_skill:
                skills.append(current_skill)
                current_skill = None
            value = raw_line.split(":", 1)[1].strip()
            if value.startswith("["):
                inner = value.strip("[]").strip()
                if inner:
                    consult_agents.extend(
                        item.strip().strip("\"'")
                        for item in inner.split(",")
                        if item.strip()
                    )
                current_section = None
            else:
                current_section = "consult_agents"
            continue
        stripped = raw_line.strip()
        if current_section == "permission":
            if stripped.startswith("edit:"):
                edit_allowed = "deny" not in stripped.lower()
            elif stripped.startswith("write:"):
                write_allowed = "deny" not in stripped.lower()
            elif stripped.startswith("webfetch:"):
                webfetch_allowed = "deny" not in stripped.lower()
            elif stripped.startswith("websearch:"):
                websearch_allowed = "deny" not in stripped.lower()
            elif stripped.startswith("bash:"):
                current_section = "bash"
                continue
        elif current_section == "bash":
            m = re.match(r'"([^"]+)"\s*:\s*(allow|deny|ask)', stripped)
            if m:
                pattern, verdict = m.group(1), m.group(2)
                if verdict == "allow":
                    bash_allow.append(pattern)
                elif verdict == "deny":
                    bash_deny.append(pattern)
            elif stripped and not stripped[0].isspace() and not stripped.startswith("-"):
                current_section = None
        elif current_section == "imports":
            m = re.match(r"-\s*(.+)$", stripped)
            if m:
                imports.append(m.group(1).strip().strip("\"'"))
                continue
            if stripped and not stripped[0].isspace() and not stripped.startswith("-"):
                current_section = None
        elif current_section == "consult_agents":
            m = re.match(r"-\s*(.+)$", stripped)
            if m:
                consult_agents.append(m.group(1).strip().strip("\"'"))
                continue
            if stripped and not stripped[0].isspace() and not stripped.startswith("-"):
                current_section = None
        elif current_section == "skills":
            # Skill entries look like:
            #   - id: foo
            #     description: bar
            #     inputs: [a, b, c]
            id_m = re.match(r"-\s*id\s*:\s*(.+)$", stripped)
            if id_m:
                if current_skill:
                    skills.append(current_skill)
                current_skill = Skill(id=id_m.group(1).strip(), description="", inputs=[])
                continue
            if current_skill is None:
                # End of skills block (top-level key follows)
                if stripped and not stripped[0].isspace():
                    current_section = None
                continue
            desc_m = re.match(r"description\s*:\s*(.+)$", stripped)
            if desc_m:
                current_skill.description = desc_m.group(1).strip()
                continue
            in_m = re.match(r"inputs\s*:\s*\[(.*)\]\s*$", stripped)
            if in_m:
                current_skill.inputs = [x.strip() for x in in_m.group(1).split(",") if x.strip()]
                continue
            # Unrecognized indented line — keep going (ignored fields are fine)

    if current_skill:
        skills.append(current_skill)

    return AgentDef(
        name=agent_name,
        description=description,
        system_prompt=body.strip(),
        bash_allow=bash_allow,
        bash_deny=bash_deny,
        edit_allowed=edit_allowed,
        write_allowed=write_allowed,
        webfetch_allowed=webfetch_allowed,
        websearch_allowed=websearch_allowed,
        skills=skills,
        imports=imports,
        consult_agents=consult_agents,
    )


# ---------------------------------------------------------------------------
# Runner protocol
# ---------------------------------------------------------------------------

class AgentRunnerError(RuntimeError):
    """Raised when a runner cannot complete an agent invocation."""

    def __init__(self, agent: str, detail: str):
        super().__init__(f"@{agent} failed: {detail}")
        self.agent = agent
        self.detail = detail


class Runner(Protocol):
    name: str

    def run(
        self,
        agent_name: str,
        user_prompt: str,
        *,
        timeout: int,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        skill: Optional[str] = None,
    ) -> str: ...


class SkillResolutionError(RuntimeError):
    """Raised when a skill is requested but cannot be resolved or has permission conflicts."""


def resolve_skill_for_agent(
    agent_def: AgentDef,
    skill_id: str,
    skills_dir: Optional[Path] = None,
) -> tuple[Optional[Skill], Optional["object"]]:
    """Return (inline_skill, skill_file) — exactly one is set on success, both None on miss.

    Precedence:
      1. Agent's inline `skills:` registry (M8.1 path; back-compat).
      2. Global `.opencode/skills/<id>.md` IF the agent imports `skill_id`.
      3. Otherwise None.

    The skill_file branch returns a `lib.skills.SkillFile` (lazy-imported here
    to avoid a circular dep at module load time).
    """
    inline = agent_def.find_skill(skill_id)
    if inline is not None:
        return inline, None
    if skill_id not in agent_def.imports:
        return None, None
    try:
        from skills import load_skill  # local import — keep modules independent
    except ImportError:
        return None, None
    skill_file = load_skill(skill_id, skills_dir=skills_dir)
    return None, skill_file


def _prepend_skill_context(prompt: str, agent_def: AgentDef, skill_id: str) -> str:
    """Add a `## Current task / Skill: <id>` preamble to the prompt.

    Resolution order:
      1. Agent's inline `skills:` registry (back-compat, M8.1)
      2. Global skill library via `agent.imports` (M10.2)
      3. Otherwise: warn inline so the agent knows the skill is undeclared.
    """
    inline_skill, skill_file = resolve_skill_for_agent(agent_def, skill_id)

    if skill_file is not None:
        # Full SKILL.md rendering via the skills module
        try:
            from skills import render_skill_context
            return render_skill_context(skill_file) + prompt
        except ImportError:
            pass

    if inline_skill is not None:
        inputs_str = ", ".join(inline_skill.inputs) if inline_skill.inputs else "(no declared inputs)"
        return (
            f"## Current task\n"
            f"Skill: {inline_skill.id}\n"
            f"Description: {inline_skill.description}\n"
            f"Declared inputs: {inputs_str}\n\n"
            f"---\n\n" + prompt
        )

    # Skill not declared — pass through with a warning preamble
    return (
        f"## Current task\n"
        f"Skill (not declared in agent registry): {skill_id}\n"
        f"Proceed best-effort within the agent's general scope.\n\n"
        f"---\n\n" + prompt
    )


def check_skill_permissions(agent_def: AgentDef, skill_id: str, skills_dir: Optional[Path] = None) -> tuple[bool, list[str]]:
    """Validate that an agent's permissions cover what a global skill requires.

    Returns (ok, conflicts). Returns (True, []) when the skill is inline-only
    (no separate permission contract) or when the skill cannot be resolved.
    """
    inline_skill, skill_file = resolve_skill_for_agent(agent_def, skill_id, skills_dir=skills_dir)
    if skill_file is None:
        return (True, [])  # nothing to enforce
    try:
        from skills import check_skill_permissions_against_agent
    except ImportError:
        return (True, [])
    return check_skill_permissions_against_agent(
        skill_file,
        agent_def.edit_allowed,
        agent_def.write_allowed,
        agent_def.webfetch_allowed,
        agent_def.websearch_allowed,
        agent_def.bash_allow,
        agent_def.bash_deny,
    )


# ---------------------------------------------------------------------------
# OpenCode runner — extracted from the original call_agent logic
# ---------------------------------------------------------------------------

class OpenCodeRunner:
    name = "opencode"

    _pty_warning_shown: bool = False

    def __init__(self, agent_cmd: Optional[str] = None, use_pty: Optional[bool] = None):
        self.agent_cmd = agent_cmd or os.environ.get(
            "OPENCODE_AGENT_CMD", "opencode run --agent"
        )
        # M20: PTY mode is OFF by default. OpenCode 1.15.x exits non-zero with
        # empty stdout when invoked through a PTY pipe, which makes every
        # agent call fail. Users on a working OpenCode build opt back in by
        # setting OPENCODE_USE_PTY=true (or pass `use_pty=True` explicitly).
        if use_pty is None:
            env_val = os.environ.get("OPENCODE_USE_PTY", "").strip().lower()
            use_pty = env_val in ("1", "true", "yes", "on")
        self.use_pty = use_pty
        if self.use_pty and not OpenCodeRunner._pty_warning_shown:
            import sys
            print(
                "[opencode-runner] PTY mode enabled. If agent calls return empty "
                "stdout, unset OPENCODE_USE_PTY (or set it to false) to fall back "
                "to subprocess.run mode.",
                file=sys.stderr,
            )
            OpenCodeRunner._pty_warning_shown = True

    def run(
        self,
        agent_name: str,
        user_prompt: str,
        *,
        timeout: int,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        skill: Optional[str] = None,
    ) -> str:
        cmd = shlex.split(self.agent_cmd) + [agent_name]
        if model:
            cmd += ["--model", model]
        if skill:
            try:
                agent_def = parse_agent_file(agent_name)
            except FileNotFoundError:
                agent_def = None
            if agent_def is not None:
                user_prompt = _prepend_skill_context(user_prompt, agent_def, skill)
        if not self.use_pty:
            return self._run_simple(agent_name, cmd, user_prompt, timeout, cwd)
        return self._run_pty(agent_name, cmd, user_prompt, timeout, cwd)

    def _run_simple(self, agent_name, cmd, prompt, timeout, cwd):
        try:
            proc = subprocess.run(
                cmd, input=prompt, text=True, capture_output=True,
                timeout=timeout, check=False, cwd=cwd,
            )
        except FileNotFoundError:
            raise AgentRunnerError(
                agent_name,
                f"runner binary not found: {cmd[0]!r}. "
                "Set OPENCODE_AGENT_CMD or install OpenCode.",
            )
        except subprocess.TimeoutExpired:
            raise AgentRunnerError(agent_name, f"timeout after {timeout}s")
        if proc.returncode != 0:
            raise AgentRunnerError(
                agent_name,
                f"non-zero exit ({proc.returncode}): "
                f"stderr={proc.stderr.strip()[:400]} stdout={proc.stdout.strip()[:200]}",
            )
        if not proc.stdout.strip():
            raise AgentRunnerError(agent_name, f"empty stdout. stderr: {proc.stderr.strip()[:400]}")
        return proc.stdout

    def _run_pty(self, agent_name, cmd, prompt, timeout, cwd):
        master_fd, slave_fd = pty.openpty()
        r_pipe, w_pipe = os.pipe()
        try:
            proc = subprocess.Popen(
                cmd, stdin=slave_fd, stdout=w_pipe,
                stderr=subprocess.DEVNULL, close_fds=True, cwd=cwd,
            )
        except FileNotFoundError:
            os.close(master_fd); os.close(slave_fd); os.close(r_pipe); os.close(w_pipe)
            raise AgentRunnerError(
                agent_name,
                f"runner binary not found: {cmd[0]!r}. "
                "Set OPENCODE_AGENT_CMD or install OpenCode.",
            )
        os.close(slave_fd); os.close(w_pipe)
        os.write(master_fd, prompt.encode())
        try:
            os.close(master_fd)
        except OSError:
            pass

        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rdy, _, _ = select.select([r_pipe], [], [], 0.5)
            if rdy:
                try:
                    chunk = os.read(r_pipe, 8192)
                    if not chunk:
                        break
                    chunks.append(chunk)
                except OSError:
                    break
            if proc.poll() is not None:  # pragma: no cover — drain loop is a race-condition handler
                while time.monotonic() < deadline:
                    rdy, _, _ = select.select([r_pipe], [], [], 0.2)
                    if not rdy:
                        break
                    try:
                        chunk = os.read(r_pipe, 8192)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    except OSError:
                        break
                break

        os.close(r_pipe)
        proc.wait()
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        text = _strip_ansi(raw)

        if proc.returncode != 0:
            raise AgentRunnerError(
                agent_name,
                f"non-zero exit ({proc.returncode}); "
                f"output_tail={text.strip()[-300:] if text.strip() else '(empty)'}",
            )
        if not text.strip():
            raise AgentRunnerError(agent_name, "empty output (TTY capture may have failed)")
        return text


# ---------------------------------------------------------------------------
# Claude Code runner — invokes `claude -p` with system-prompt injection
# ---------------------------------------------------------------------------

class ClaudeCodeRunner:
    name = "claude"

    def __init__(self, agent_cmd: Optional[str] = None, agents_dir: Optional[Path] = None):
        self.agent_cmd = agent_cmd or os.environ.get("CLAUDE_AGENT_CMD", "claude")
        self.agents_dir = agents_dir or Path(".opencode/agents")

    def _resolve_agents_dir(self, agent_name: str) -> Path:
        """Resolve which directory to load `agent_name.md` from.

        Project-local `.opencode/agents/` wins if it has the file; otherwise
        fall back to the global OpenCode agent install (where `install.sh`
        puts them by default), honoring `OPENCODE_HOME` if set.

        Lets a fresh project work without copying / symlinking agents
        per-project after `init.sh`, while still allowing projects to
        override agents locally.
        """
        primary = self.agents_dir
        if (primary / f"{agent_name}.md").exists():
            return primary
        opencode_home = os.environ.get("OPENCODE_HOME") or str(Path.home() / ".config" / "opencode")
        fallback = Path(opencode_home) / "agents"
        if (fallback / f"{agent_name}.md").exists():
            return fallback
        return primary  # parse_agent_file will raise FileNotFoundError with the right message

    def run(
        self,
        agent_name: str,
        user_prompt: str,
        *,
        timeout: int,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        skill: Optional[str] = None,
    ) -> str:
        agent = parse_agent_file(agent_name, self._resolve_agents_dir(agent_name))
        if skill:
            user_prompt = _prepend_skill_context(user_prompt, agent, skill)
        cmd = shlex.split(self.agent_cmd) + ["-p"]
        if agent.system_prompt:
            cmd += ["--append-system-prompt", agent.system_prompt]
        if model:
            cmd += ["--model", model]

        # acceptEdits permission mode: agent can edit/write without prompting,
        # but bash commands still respect the allow/deny lists.
        if agent.edit_allowed or agent.write_allowed:
            cmd += ["--permission-mode", "acceptEdits"]

        allowed: list[str] = []
        disallowed: list[str] = []
        if agent.edit_allowed:
            allowed.append("Edit")
        else:
            disallowed.append("Edit")
        if agent.write_allowed:
            allowed.append("Write")
        else:
            disallowed.append("Write")
        for pat in agent.bash_allow:
            allowed.append(f"Bash({pat})")
        for pat in agent.bash_deny:
            if pat == "*":
                continue  # wildcard handled by Claude Code's defaults
            disallowed.append(f"Bash({pat})")
        if not agent.webfetch_allowed:
            disallowed.append("WebFetch")
        if not agent.websearch_allowed:
            disallowed.append("WebSearch")

        if allowed:
            cmd += ["--allowedTools", ",".join(allowed)]
        if disallowed:
            cmd += ["--disallowedTools", ",".join(disallowed)]

        try:
            proc = subprocess.run(
                cmd, input=user_prompt, text=True, capture_output=True,
                timeout=timeout, check=False, cwd=cwd,
            )
        except FileNotFoundError:
            raise AgentRunnerError(
                agent_name,
                f"claude CLI not found: {cmd[0]!r}. "
                "Install Claude Code: https://claude.com/claude-code",
            )
        except subprocess.TimeoutExpired:
            raise AgentRunnerError(agent_name, f"timeout after {timeout}s")
        if proc.returncode != 0:
            raise AgentRunnerError(
                agent_name,
                f"non-zero exit ({proc.returncode}): "
                f"stderr={proc.stderr.strip()[:400]} stdout={proc.stdout.strip()[:200]}",
            )
        if not proc.stdout.strip():
            raise AgentRunnerError(
                agent_name, f"empty stdout. stderr: {proc.stderr.strip()[:400]}"
            )
        return proc.stdout


# ---------------------------------------------------------------------------
# Selection / auto-detect
# ---------------------------------------------------------------------------

def select_runner(preference: Optional[str] = None) -> Runner:
    """Pick a runner based on preference, env vars, and PATH.

    Precedence:
      1. explicit `preference` arg ("opencode" | "claude")
      2. AA_RUNNER env
      3. If OPENCODE_AGENT_CMD env set -> opencode
      4. If `claude` on PATH -> claude
      5. If `opencode` on PATH -> opencode
      6. Raise — user must install one
    """
    choice = preference or os.environ.get("AA_RUNNER")

    if choice == "opencode":
        return OpenCodeRunner()
    if choice == "claude":
        return ClaudeCodeRunner()
    if choice:
        raise ValueError(
            f"Unknown runner {choice!r}. Valid: 'opencode' | 'claude'."
        )

    if os.environ.get("OPENCODE_AGENT_CMD"):
        return OpenCodeRunner()
    if shutil.which("claude"):
        return ClaudeCodeRunner()
    if shutil.which("opencode"):
        return OpenCodeRunner()

    raise RuntimeError(
        "No agent runner available. Install OpenCode (https://opencode.ai/) "
        "or Claude Code (https://claude.com/claude-code), or set AA_RUNNER and "
        "the appropriate *_AGENT_CMD env var."
    )
