#!/usr/bin/env bash
#
# init.sh — bootstrap a target project to use autonomous-agents.
#
# Run this from inside any project where you want to use the pipeline.
# Requires `install.sh` to have been run first (so $AA_HOME is populated).
#
# Creates:
#   ./.opencode/config.json              project-tunable pipeline config
#   ./.opencode/commands/develop.md      slash command -> aa-orchestrator
#   ./.opencode/commands/resume.md       slash command -> aa-orchestrator
#   ./docs/specs/TEMPLATE.md             spec template (only if no specs exist)
#   ./.gitignore                         ensures progress.json + backup ignored
#
# Does NOT touch:
#   ./.opencode/progress.json            generated on first /develop run
#   ./docs/specs/*.md (other than TEMPLATE) - your spec files
#
# Usage:
#   bash init.sh                         # bootstrap current directory
#   bash init.sh /path/to/project        # bootstrap a specific dir
#   bash init.sh --force                 # overwrite existing files
#   bash init.sh --uninstall             # remove project-local config

set -euo pipefail

log() { printf '\033[1;34m[init]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[init]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[init]\033[0m %s\n' "$*" >&2; exit 1; }

AA_HOME="${AA_HOME:-$HOME/.local/share/autonomous-agents}"

TARGET_DIR="$PWD"
FORCE=0
UNINSTALL=0
RUNNER=""  # "opencode" | "claude" | "" (auto-detect)

# Two-pass arg parsing so --runner can take a value
PREV=""
for arg in "$@"; do
  if [ "$PREV" = "--runner" ]; then
    case "$arg" in
      opencode|claude) RUNNER="$arg" ;;
      *) fail "--runner must be 'opencode' or 'claude', got: $arg" ;;
    esac
    PREV=""
    continue
  fi
  case "$arg" in
    --force) FORCE=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --runner) PREV="--runner" ;;
    --runner=opencode|--runner=claude) RUNNER="${arg#*=}" ;;
    --runner=*) fail "--runner must be 'opencode' or 'claude', got: ${arg#*=}" ;;
    --help|-h)
      sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      if [ -d "$arg" ]; then
        TARGET_DIR="$arg"
      elif [ -e "$arg" ]; then
        fail "Path exists but is not a directory: $arg"
      else
        fail "Path does not exist: $arg (typo? create the directory first)"
      fi
      ;;
  esac
done
[ -n "$PREV" ] && fail "--runner expects a value (opencode or claude)"

# Auto-detect runner if not specified
if [ -z "$RUNNER" ]; then
  if command -v claude >/dev/null 2>&1 && ! command -v opencode >/dev/null 2>&1; then
    RUNNER="claude"
  elif command -v opencode >/dev/null 2>&1; then
    RUNNER="opencode"
  else
    RUNNER="opencode"  # default; user can install opencode after
  fi
fi

TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

if [ ! -d "$AA_HOME/templates" ]; then
  fail "Templates not found at $AA_HOME/templates. Run install.sh first."
fi

if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 not found. Install Python 3.10+ first."
fi

if [ "$UNINSTALL" -eq 1 ]; then
  log "Removing autonomous-agents config from $TARGET_DIR..."
  for f in \
      "$TARGET_DIR/.opencode/config.json" \
      "$TARGET_DIR/.opencode/commands/develop.md" \
      "$TARGET_DIR/.opencode/commands/resume.md" \
      "$TARGET_DIR/.claude/commands/develop.md" \
      "$TARGET_DIR/.claude/commands/resume.md"
  do
    if [ -f "$f" ]; then rm -v "$f"; fi
  done
  warn "Note: progress.json and your spec files are NOT removed. Delete manually if desired."
  exit 0
fi

log "Target: $TARGET_DIR"

mkdir -p "$TARGET_DIR/.opencode/commands"
mkdir -p "$TARGET_DIR/docs/specs/epics"

copy_template() {
  local src="$1"
  local dst="$2"
  local label="${3:-$(basename "$dst")}"
  if [ ! -f "$src" ]; then
    warn "  source missing: $src"
    return
  fi
  if [ -f "$dst" ] && [ "$FORCE" -ne 1 ]; then
    warn "  exists (skip): $dst   (use --force to overwrite)"
    return
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  log "  + $label"
}

log "Installing pipeline config (runner: $RUNNER)..."
copy_template "$AA_HOME/templates/config.json" "$TARGET_DIR/.opencode/config.json" ".opencode/config.json"

# Always install OpenCode-style slash commands (they work even when the runner is claude)
copy_template "$AA_HOME/templates/develop.md"  "$TARGET_DIR/.opencode/commands/develop.md"  ".opencode/commands/develop.md"
copy_template "$AA_HOME/templates/resume.md"   "$TARGET_DIR/.opencode/commands/resume.md"   ".opencode/commands/resume.md"
# Conditional — older installs may not have these staged yet; ignore gracefully
[ -f "$AA_HOME/templates/discover.md" ] && copy_template "$AA_HOME/templates/discover.md" "$TARGET_DIR/.opencode/commands/discover.md" ".opencode/commands/discover.md"
[ -f "$AA_HOME/templates/revisit.md" ]  && copy_template "$AA_HOME/templates/revisit.md"  "$TARGET_DIR/.opencode/commands/revisit.md"  ".opencode/commands/revisit.md"
[ -f "$AA_HOME/templates/sprint.md" ]   && copy_template "$AA_HOME/templates/sprint.md"   "$TARGET_DIR/.opencode/commands/sprint.md"   ".opencode/commands/sprint.md"

# Copy agent definitions into the project (OpenCode CLI requires project-local agents,
# and our ClaudeCodeRunner falls back to global but project-local is cleaner). This
# fixes the "Agent definition not found" / "@sprint-planner failed: non-zero exit (1)"
# errors when running sprint cycle.
OPENCODE_AGENTS_SRC="${OPENCODE_HOME:-$HOME/.config/opencode}/agents"
if [ -d "$OPENCODE_AGENTS_SRC" ]; then
  log "Installing agent definitions from $OPENCODE_AGENTS_SRC..."
  mkdir -p "$TARGET_DIR/.opencode/agents"
  copied_agents=0
  for f in "$OPENCODE_AGENTS_SRC"/*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    if [ -f "$TARGET_DIR/.opencode/agents/$name" ] && [ "$FORCE" -ne 1 ]; then
      continue
    fi
    cp "$f" "$TARGET_DIR/.opencode/agents/$name"
    copied_agents=$((copied_agents + 1))
  done
  log "  + ${copied_agents} agent file(s) installed under .opencode/agents/"
else
  warn ""
  warn "No agent source dir found at $OPENCODE_AGENTS_SRC"
  warn "Run install.sh from the autonomous-agents source tree first."
fi

# For Claude Code runner, also drop /develop, /resume into .claude/commands/
if [ "$RUNNER" = "claude" ] && [ -d "$AA_HOME/templates/claude-code" ]; then
  mkdir -p "$TARGET_DIR/.claude/commands"
  for f in "$AA_HOME/templates/claude-code/"*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    copy_template "$f" "$TARGET_DIR/.claude/commands/$name" ".claude/commands/$name"
  done
fi

# Stamp the chosen runner into the project config (best-effort sed; non-fatal on failure)
if [ -f "$TARGET_DIR/.opencode/config.json" ] && [ -n "$RUNNER" ]; then
  python3 - "$TARGET_DIR/.opencode/config.json" "$RUNNER" <<'PY'
import json, sys
path, runner = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
cfg.setdefault("pipeline", {})["runner"] = runner
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
PY
  log "  set pipeline.runner=$RUNNER in .opencode/config.json"
fi

log "Installing spec authoring guide..."
copy_template "$AA_HOME/templates/specs/AUTHORING_GUIDE.md" "$TARGET_DIR/docs/specs/AUTHORING_GUIDE.md" "docs/specs/AUTHORING_GUIDE.md"
copy_template "$AA_HOME/templates/specs/EXAMPLE.md"         "$TARGET_DIR/docs/specs/EXAMPLE.md"         "docs/specs/EXAMPLE.md"
copy_template "$AA_HOME/templates/specs/MIGRATION.md"       "$TARGET_DIR/docs/specs/MIGRATION.md"       "docs/specs/MIGRATION.md"

# index.yaml only if missing (project-specific, user fills it in)
if [ ! -f "$TARGET_DIR/docs/specs/index.yaml" ]; then
  copy_template "$AA_HOME/templates/specs/index.yaml" "$TARGET_DIR/docs/specs/index.yaml" "docs/specs/index.yaml"
else
  log "  exists (skip): docs/specs/index.yaml"
fi

# Project-root planning-tool integration files
log "Installing planning-tool integration files (CLAUDE.md, AGENTS.md)..."
if [ -f "$TARGET_DIR/CLAUDE.md" ] && [ "$FORCE" -ne 1 ]; then
  warn "  exists (skip): CLAUDE.md   (existing file preserved; consider appending the autonomous-agents section manually)"
else
  copy_template "$AA_HOME/templates/root/CLAUDE.md" "$TARGET_DIR/CLAUDE.md" "CLAUDE.md"
fi
if [ -f "$TARGET_DIR/AGENTS.md" ] && [ "$FORCE" -ne 1 ]; then
  warn "  exists (skip): AGENTS.md   (existing file preserved)"
else
  copy_template "$AA_HOME/templates/root/AGENTS.md" "$TARGET_DIR/AGENTS.md" "AGENTS.md"
fi

# M10.4: skill library — copy global skills into project .opencode/skills/.
# Per-project files override globals (same precedence as agent files).
if [ -d "$AA_HOME/templates/skills" ]; then
  log "Installing skill library..."
  mkdir -p "$TARGET_DIR/.opencode/skills"
  for f in "$AA_HOME/templates/skills/"*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    copy_template "$f" "$TARGET_DIR/.opencode/skills/$name" ".opencode/skills/$name"
  done
fi

GITIGNORE="$TARGET_DIR/.gitignore"
need_lines=(
  ".opencode/progress.json"
  ".opencode/progress.backup.json"
  ".opencode/progress.json.tmp"
)
for line in "${need_lines[@]}"; do
  if [ ! -f "$GITIGNORE" ] || ! grep -Fxq "$line" "$GITIGNORE"; then
    echo "$line" >> "$GITIGNORE"
    log "  added to .gitignore: $line"
  fi
done

OPENCODE_AGENTS="${OPENCODE_HOME:-$HOME/.config/opencode}/agents"
if [ ! -f "$OPENCODE_AGENTS/spec.md" ]; then
  warn ""
  warn "Agents not found at $OPENCODE_AGENTS/"
  warn "Did you run install.sh? Re-run from the autonomous-agents source tree:"
  warn "  bash install.sh"
fi

if ! command -v aa-orchestrator >/dev/null 2>&1; then
  AA_BIN="${AA_BIN:-$HOME/.local/bin}"
  warn ""
  warn "aa-orchestrator is not on PATH."
  warn "Add to ~/.zshrc or ~/.bashrc:"
  warn "  export PATH=\"$AA_BIN:\$PATH\""
fi

log ""
log "Project bootstrapped."
log ""
log "Next steps:"
log "  1. Edit $TARGET_DIR/docs/specs/index.yaml (project name + epic order)"
log "  2. Author specs as $TARGET_DIR/docs/specs/epics/NN-name.md"
log "     - read docs/specs/AUTHORING_GUIDE.md for the format"
log "     - copy the structure from docs/specs/EXAMPLE.md"
log "     - if you have legacy specs, see docs/specs/MIGRATION.md"
log "  3. Validate before running:  aa-orchestrator validate"
log "  4. Run the pipeline:         aa-orchestrator develop"
log "     or in OpenCode:           /develop"
log ""
log "Tip: any AI planning tool (Claude Code, Cursor, Cline, Gemini, etc.)"
log "     will auto-discover CLAUDE.md / AGENTS.md and follow the spec format."
