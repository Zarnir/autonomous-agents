#!/usr/bin/env bash
#
# install.sh — one-time global install for autonomous-agents.
#
# Installs:
#   - Agent definitions to       $OPENCODE_HOME/agents/   (default: ~/.config/opencode/agents/)
#   - Orchestrator script to     $AA_HOME/lib/            (default: ~/.local/share/autonomous-agents/lib/)
#   - CLI symlink                $AA_BIN/aa-orchestrator  (default: ~/.local/bin/aa-orchestrator)
#
# After install, run `init.sh` in any project to bootstrap it.
#
# Override locations with environment variables before running:
#   OPENCODE_HOME=/path/to/opencode bash install.sh
#   AA_HOME=/path/to/aa-tool        bash install.sh
#   AA_BIN=/path/to/bin             bash install.sh
#
# Usage:
#   bash install.sh                 # install
#   bash install.sh --uninstall     # remove
#   bash install.sh --update        # re-copy from current source

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCODE_HOME="${OPENCODE_HOME:-$HOME/.config/opencode}"
AA_HOME="${AA_HOME:-$HOME/.local/share/autonomous-agents}"
AA_BIN="${AA_BIN:-$HOME/.local/bin}"

ACTION="install"
case "${1:-}" in
  --uninstall) ACTION="uninstall" ;;
  --update)    ACTION="update" ;;
  --help|-h)
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

log() { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }

# --- preflight -----------------------------------------------------------

if [ ! -d "$SOURCE_DIR/.opencode/agents" ]; then
  fail "Cannot find .opencode/agents in $SOURCE_DIR. Run install.sh from the autonomous-agents project root."
fi

if [ ! -f "$SOURCE_DIR/lib/orchestrator.py" ]; then
  fail "Cannot find lib/orchestrator.py in $SOURCE_DIR."
fi

if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 not found. Install Python 3.10+ first."
fi

PYTHON_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
  warn "Python $PYTHON_VERSION found — orchestrator targets 3.10+. May still work."
fi

# Runner availability (warn, do not fail — user may install after)
if ! command -v opencode >/dev/null 2>&1 && ! command -v claude >/dev/null 2>&1; then
  warn ""
  warn "Neither 'opencode' nor 'claude' was found on PATH."
  warn "The orchestrator invokes one of them at run time via OPENCODE_AGENT_CMD."
  warn "  Install OpenCode: https://opencode.ai/  (default runner)"
  warn "  Or install Claude Code: https://claude.com/claude-code"
  warn "Install can still continue; configure the runner before running 'aa-orchestrator develop'."
fi

# --- uninstall -----------------------------------------------------------

if [ "$ACTION" = "uninstall" ]; then
  log "Uninstalling autonomous-agents…"
  # Enumerate agent files from the SOURCE tree (authoritative list)
  if [ -d "$SOURCE_DIR/.opencode/agents" ]; then
    for f in "$SOURCE_DIR"/.opencode/agents/*.md; do
      [ -f "$f" ] || continue
      name=$(basename "$f")
      target="$OPENCODE_HOME/agents/$name"
      if [ -f "$target" ]; then
        rm -v "$target"
      fi
    done
  else
    # Source unavailable — fall back to globbing installed agents directory
    warn "Source tree not found; removing all .md files under $OPENCODE_HOME/agents/ that we may have installed"
    for f in "$OPENCODE_HOME"/agents/*.md; do
      [ -f "$f" ] || continue
      # Conservative: only remove if it has the autonomous-agents marker
      if head -20 "$f" 2>/dev/null | grep -q "autonomous-agents" ; then
        rm -v "$f"
      fi
    done
  fi
  if [ -d "$AA_HOME" ]; then
    rm -rv "$AA_HOME"
  fi
  if [ -L "$AA_BIN/aa-orchestrator" ] || [ -f "$AA_BIN/aa-orchestrator" ]; then
    rm -v "$AA_BIN/aa-orchestrator"
  fi
  log "Uninstall complete."
  exit 0
fi

# --- install / update ----------------------------------------------------

log "Source:           $SOURCE_DIR"
log "OpenCode home:    $OPENCODE_HOME"
log "Tool home:        $AA_HOME"
log "CLI bin:          $AA_BIN"
log ""

mkdir -p "$OPENCODE_HOME/agents"
mkdir -p "$AA_HOME/lib"
mkdir -p "$AA_HOME/templates"
mkdir -p "$AA_BIN"

# Helper: copy with backup on update (preserves user customizations)
copy_template() {
  local src="$1" dst="$2"
  if [ "$ACTION" = "update" ] && [ -f "$dst" ]; then
    if ! cmp -s "$src" "$dst"; then
      cp "$dst" "$dst.bak.$(date +%s)"
      warn "  ~ $dst differed from source; backed up before overwrite"
    fi
  fi
  cp "$src" "$dst"
}

# Agents
log "Installing agents → $OPENCODE_HOME/agents/"
for f in "$SOURCE_DIR"/.opencode/agents/*.md; do
  name=$(basename "$f")
  cp "$f" "$OPENCODE_HOME/agents/$name"
  log "  + $name"
done

# Orchestrator + spec parser
log "Installing orchestrator -> $AA_HOME/lib/"
cp "$SOURCE_DIR/lib/orchestrator.py" "$AA_HOME/lib/orchestrator.py"
chmod +x "$AA_HOME/lib/orchestrator.py"
if [ -f "$SOURCE_DIR/lib/spec_parser.py" ]; then
  cp "$SOURCE_DIR/lib/spec_parser.py" "$AA_HOME/lib/spec_parser.py"
fi
if [ -f "$SOURCE_DIR/lib/runners.py" ]; then
  cp "$SOURCE_DIR/lib/runners.py" "$AA_HOME/lib/runners.py"
fi
if [ -f "$SOURCE_DIR/lib/cost_tracker.py" ]; then
  cp "$SOURCE_DIR/lib/cost_tracker.py" "$AA_HOME/lib/cost_tracker.py"
fi
if [ -f "$SOURCE_DIR/lib/retry.py" ]; then
  cp "$SOURCE_DIR/lib/retry.py" "$AA_HOME/lib/retry.py"
fi
if [ -f "$SOURCE_DIR/lib/wizard.py" ]; then
  cp "$SOURCE_DIR/lib/wizard.py" "$AA_HOME/lib/wizard.py"
fi

# Templates
log "Installing templates -> $AA_HOME/templates/"
mkdir -p "$AA_HOME/templates/specs"
mkdir -p "$AA_HOME/templates/root"

# Reference config + slash command stubs (OpenCode .opencode/commands/*.md)
copy_template "$SOURCE_DIR/.opencode/config.json" "$AA_HOME/templates/config.json"
copy_template "$SOURCE_DIR/.opencode/commands/develop.md"  "$AA_HOME/templates/develop.md"
copy_template "$SOURCE_DIR/.opencode/commands/resume.md"   "$AA_HOME/templates/resume.md"
copy_template "$SOURCE_DIR/.opencode/commands/discover.md" "$AA_HOME/templates/discover.md"
copy_template "$SOURCE_DIR/.opencode/commands/revisit.md"  "$AA_HOME/templates/revisit.md"
copy_template "$SOURCE_DIR/.opencode/commands/sprint.md"   "$AA_HOME/templates/sprint.md"

# Spec authoring guide + example + migration playbook
copy_template "$SOURCE_DIR/docs/specs/AUTHORING_GUIDE.md" "$AA_HOME/templates/specs/AUTHORING_GUIDE.md"
copy_template "$SOURCE_DIR/docs/specs/EXAMPLE.md" "$AA_HOME/templates/specs/EXAMPLE.md"
copy_template "$SOURCE_DIR/docs/specs/MIGRATION.md" "$AA_HOME/templates/specs/MIGRATION.md"
copy_template "$SOURCE_DIR/templates/index.yaml" "$AA_HOME/templates/specs/index.yaml"

# Project-root planning-tool integration files
copy_template "$SOURCE_DIR/templates/root/CLAUDE.md" "$AA_HOME/templates/root/CLAUDE.md"
copy_template "$SOURCE_DIR/templates/root/AGENTS.md" "$AA_HOME/templates/root/AGENTS.md"

# Claude Code slash command templates (M1.1)
if [ -d "$SOURCE_DIR/templates/claude-code" ]; then
  mkdir -p "$AA_HOME/templates/claude-code"
  for f in "$SOURCE_DIR/templates/claude-code/"*.md; do
    [ -f "$f" ] || continue
    copy_template "$f" "$AA_HOME/templates/claude-code/$(basename "$f")"
  done
fi

# Skill library (M10.4) — global skill files that any agent may import.
# Staged under $AA_HOME/templates/skills/; init.sh copies them per-project.
if [ -d "$SOURCE_DIR/.opencode/skills" ]; then
  mkdir -p "$AA_HOME/templates/skills"
  log "Installing skill library → $AA_HOME/templates/skills/"
  for f in "$SOURCE_DIR/.opencode/skills/"*.md; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    copy_template "$f" "$AA_HOME/templates/skills/$name"
    log "  + skills/$name"
  done
fi

# lib/skills.py (M10.1) — orchestrator imports this at runtime
if [ -f "$SOURCE_DIR/lib/skills.py" ]; then
  cp "$SOURCE_DIR/lib/skills.py" "$AA_HOME/lib/skills.py"
  log "  + lib/skills.py"
fi

# init.sh installed alongside templates so users can re-init projects
if [ -f "$SOURCE_DIR/init.sh" ]; then
  cp "$SOURCE_DIR/init.sh" "$AA_HOME/init.sh"
  chmod +x "$AA_HOME/init.sh"
fi

# CLI shim — re-resolves AA_HOME at runtime so env override still works
log "Creating CLI shim → $AA_BIN/aa-orchestrator"
cat > "$AA_BIN/aa-orchestrator" <<EOF
#!/usr/bin/env bash
# auto-generated by autonomous-agents/install.sh
# AA_HOME default below was set at install time; can be overridden in env
: "\${AA_HOME:=$AA_HOME}"
exec python3 "\$AA_HOME/lib/orchestrator.py" "\$@"
EOF
chmod +x "$AA_BIN/aa-orchestrator"

# PATH check
if ! echo ":$PATH:" | grep -q ":$AA_BIN:"; then
  warn ""
  warn "$AA_BIN is not on your PATH."
  warn "Add this to your shell rc (~/.zshrc or ~/.bashrc):"
  warn "  export PATH=\"$AA_BIN:\$PATH\""
fi

log ""
log "Install complete."
log ""
log "Next steps:"
log "  1. cd into any project"
log "  2. Run:  bash $AA_HOME/init.sh"
log "  3. Edit docs/specs/*.md with your SCRUM/waterfall spec"
log "  4. Run:  aa-orchestrator develop"
log ""
log "Or invoke from inside OpenCode:"
log "  /develop          (after init.sh adds the slash command)"
