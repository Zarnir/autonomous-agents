# Test suite

Hermetic tests for the orchestrator, spec parser, and install/init scripts.
No real LLM calls. No production data touched. Every test runs in `tmp_path`.

## Layout

| File                   | What it covers                                       |
|------------------------|------------------------------------------------------|
| `conftest.py`          | Shared fixtures (`project_root`, `specs_dir`, `sample_epic`, `progress_file`) and sys.path setup so `import orchestrator` / `import spec_parser` works without packaging |
| `test_spec_parser.py`  | Unit tests for `lib/spec_parser.py` — frontmatter parsing, validation rules, encoding hardening, cycle detection |
| `test_orchestrator.py` | Unit tests for `lib/orchestrator.py` — `next_eligible_story`, `cascade_fail`, convergence rules, persist retry, config robustness, extract_* helpers |
| `test_e2e_smoke.py`    | End-to-end smoke — `develop --dry-run` and `validate` over a real spec tree |
| `test_install.bats`    | Bash tests for `install.sh` / `init.sh` — install layout, uninstall cleanup, update idempotency, gitignore append |

## Running

### Python tests

```bash
pytest tests/ -v
```

Or just the e2e markers:

```bash
pytest -m e2e tests/
```

### Bash tests (install/init)

Requires [bats-core](https://github.com/bats-core/bats-core):

```bash
# macOS
brew install bats-core

# Debian/Ubuntu
sudo apt install bats

# Run
bats tests/test_install.bats
```

## What's NOT tested here

- Real LLM-agent invocation — agents are mocked or skipped. To test the real pipeline, run `aa-orchestrator develop` against the EXAMPLE spec in a throwaway project.
- Concurrent multi-process access to `progress.json` — the optimistic-concurrency guard is exercised at the unit level only.
- Long-running pipelines (>5 minutes) — covered manually.

## Adding a new test

1. Reuse fixtures from `conftest.py` where possible — `project_root` gives you a `tmp_path` with `monkeypatch.chdir` already applied.
2. For new orchestrator behavior, write the unit test in `test_orchestrator.py` first, then implement.
3. For new spec syntax, add both a positive and a negative case to `test_spec_parser.py`.
4. Mark slow tests with `@pytest.mark.slow` so they can be skipped via `pytest -m "not slow"`.
