#!/usr/bin/env bats
# Bats tests for install.sh and init.sh.
# Run with:  bats tests/test_install.bats
# Install bats:  brew install bats-core  (macOS)  |  apt install bats  (Linux)

setup() {
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  FAKE_HOME="$(mktemp -d)"
  export OPENCODE_HOME="$FAKE_HOME/.config/opencode"
  export AA_HOME="$FAKE_HOME/.local/share/autonomous-agents"
  export AA_BIN="$FAKE_HOME/.local/bin"
}

teardown() {
  rm -rf "$FAKE_HOME"
}

@test "install.sh creates CLI shim, agents, and lib files" {
  run bash "$REPO_ROOT/install.sh"
  [ "$status" -eq 0 ]
  [ -x "$AA_BIN/aa-orchestrator" ]
  [ -f "$AA_HOME/lib/orchestrator.py" ]
  [ -f "$AA_HOME/lib/spec_parser.py" ]
  [ -d "$OPENCODE_HOME/agents" ]
  agent_count=$(find "$OPENCODE_HOME/agents" -name "*.md" | wc -l)
  [ "$agent_count" -gt 0 ]
}

@test "install.sh CLI shim references runtime AA_HOME (not baked path)" {
  run bash "$REPO_ROOT/install.sh"
  [ "$status" -eq 0 ]
  grep -q 'AA_HOME' "$AA_BIN/aa-orchestrator"
}

@test "install.sh --uninstall removes everything it created" {
  bash "$REPO_ROOT/install.sh"
  [ -d "$AA_HOME" ]

  run bash "$REPO_ROOT/install.sh" --uninstall
  [ "$status" -eq 0 ]
  [ ! -d "$AA_HOME" ]
  [ ! -f "$AA_BIN/aa-orchestrator" ]
}

@test "install.sh --update is idempotent" {
  bash "$REPO_ROOT/install.sh"
  shim_1=$(cat "$AA_BIN/aa-orchestrator")
  orch_1=$(cat "$AA_HOME/lib/orchestrator.py" | head -50)

  run bash "$REPO_ROOT/install.sh" --update
  [ "$status" -eq 0 ]

  shim_2=$(cat "$AA_BIN/aa-orchestrator")
  orch_2=$(cat "$AA_HOME/lib/orchestrator.py" | head -50)

  [ "$shim_1" = "$shim_2" ]
  [ "$orch_1" = "$orch_2" ]
}

@test "init.sh bootstraps a clean project" {
  bash "$REPO_ROOT/install.sh"
  PROJECT="$(mktemp -d)"
  run bash "$REPO_ROOT/init.sh" "$PROJECT"
  [ "$status" -eq 0 ]
  [ -d "$PROJECT/.opencode/commands" ]
  [ -f "$PROJECT/.opencode/config.json" ]
  [ -f "$PROJECT/.opencode/commands/develop.md" ]
  [ -f "$PROJECT/.opencode/commands/resume.md" ]
  [ -d "$PROJECT/docs/specs/epics" ]
  [ -f "$PROJECT/docs/specs/AUTHORING_GUIDE.md" ]
  [ -f "$PROJECT/CLAUDE.md" ]
  [ -f "$PROJECT/AGENTS.md" ]
  [ -f "$PROJECT/.gitignore" ]
  grep -q "progress.json" "$PROJECT/.gitignore"
  rm -rf "$PROJECT"
}

@test "init.sh preserves existing CLAUDE.md without --force" {
  bash "$REPO_ROOT/install.sh"
  PROJECT="$(mktemp -d)"
  echo "my custom claude.md" > "$PROJECT/CLAUDE.md"
  run bash "$REPO_ROOT/init.sh" "$PROJECT"
  [ "$status" -eq 0 ]
  grep -q "my custom claude.md" "$PROJECT/CLAUDE.md"
  rm -rf "$PROJECT"
}

@test "init.sh --force overwrites existing CLAUDE.md" {
  bash "$REPO_ROOT/install.sh"
  PROJECT="$(mktemp -d)"
  echo "my custom claude.md" > "$PROJECT/CLAUDE.md"
  run bash "$REPO_ROOT/init.sh" --force "$PROJECT"
  [ "$status" -eq 0 ]
  ! grep -q "my custom claude.md" "$PROJECT/CLAUDE.md"
  rm -rf "$PROJECT"
}

@test "init.sh .gitignore append is idempotent" {
  bash "$REPO_ROOT/install.sh"
  PROJECT="$(mktemp -d)"
  bash "$REPO_ROOT/init.sh" "$PROJECT"
  count_1=$(grep -c "progress.json" "$PROJECT/.gitignore")
  bash "$REPO_ROOT/init.sh" "$PROJECT"
  count_2=$(grep -c "progress.json" "$PROJECT/.gitignore")
  [ "$count_1" -eq "$count_2" ]
  rm -rf "$PROJECT"
}
