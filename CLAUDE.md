# Claude Code Guidelines for Gefion

## TDD Required (MANDATORY)

**Test-Driven Development is required for ALL code changes in this project.**

### The TDD Workflow

For any new feature, file, or code change:

1. **Write tests FIRST** - Create or modify test files in `tests/` before touching `src/`
2. **Run tests** - Verify the new tests FAIL (they test something that doesn't exist yet)
3. **Implement code** - Write the minimum code in `src/` to make tests pass
4. **Run tests again** - Verify all tests PASS
5. **Commit together** - Tests and implementation in the same commit

### What This Means in Practice

- **NEVER** create a new file in `src/gefion/` without first creating its test file
- **NEVER** add a new function without first writing a test for it
- **NEVER** modify behavior without first writing a test that captures the expected change

### Example: Adding a New View

```
WRONG ORDER:
1. Create src/gefion/ui/views/newview.py
2. Write render_newview() function
3. Add test later (or forget)

CORRECT ORDER:
1. Add "newview.py" to expected_views list in tests/test_ui_components.py
2. Add test_newview_has_render_function() test
3. Run pytest - see tests FAIL
4. Create src/gefion/ui/views/newview.py with render_newview()
5. Run pytest - see tests PASS
```

### Enforcement Mechanisms

This project has multiple TDD enforcement layers:

1. **This file (CLAUDE.md)** - Instructions you must follow
2. **Pre-commit hook** - Blocks commits with src/ changes but no tests/ changes
3. **Claude Code PreToolUse hook** - Blocks writing to src/ before tests/
4. **Plan mode** - Plans must list test files before implementation files

### Bypassing (Use Sparingly)

If you absolutely must bypass TDD enforcement:
- Pre-commit: `git commit --no-verify` (explain why in commit message)
- Claude hook: Only for pure refactors with existing test coverage

## Plan Mode Requirements

When in plan mode, structure your plans with TDD order:

### Required Plan Structure

```markdown
# Feature Name

## Overview
Brief description of what we're building.

## Tests to Write FIRST
List test files and test cases that will be created/modified:
- `tests/test_feature.py` - test_feature_does_x, test_feature_handles_y

## Implementation Files
List source files to create/modify AFTER tests:
- `src/gefion/module/feature.py` - FeatureClass, helper_function

## Implementation Steps
1. Write test_feature_does_x in tests/test_feature.py
2. Run pytest - verify it FAILS
3. Create src/gefion/module/feature.py with minimal implementation
4. Run pytest - verify it PASSES
5. Write test_feature_handles_y
6. Run pytest - verify it FAILS
7. Extend implementation
8. Run pytest - verify all tests PASS

## Success Criteria
- [ ] All tests pass
- [ ] Feature works as specified
```

### Plan Review Checklist

Before exiting plan mode, verify:
- [ ] Tests section comes BEFORE implementation section
- [ ] Each implementation step is paired with a test
- [ ] Success criteria includes "All tests pass"

## Other Guidelines

### Documentation (definition of done)
- A user-facing change (new/renamed command, MCP tool, or workflow change)
  is not done until README/docs/USER_GUIDE reflect it — and, if the learning
  path changed, `.claude/commands/gefion-learn.md` too — in the same PR
- `tests/test_docs_drift.py` enforces the mechanical part (commands and MCP
  tools referenced in docs must exist and be documented); narrative drift is
  on the author

### Code Style
- Follow existing patterns in the codebase
- Use type hints for all function signatures
- Add docstrings for public functions

### Observability (NON-NEGOTIABLE)
- New modules MUST import from `gefion.observability` — the pre-commit hook blocks commits of significant files without it
- Use `with create_span("module.function", key=value) as span:` for significant operations
- Use `set_attributes(span, result_count=N)` to record results
- Child spans MUST propagate parent context — orphaned spans are defects
- After implementing a feature, inspect its traces via `gefion span-check` or `/gefion-perf` before considering it complete

### Performance Feedback Loop
- **Tempo MUST be running** during development (`/gefion-services start`)
- The UI should run with `OTEL_ENABLED=true` during development
- After pytest runs, a hook automatically checks Tempo for slow spans (>1s)
- Use `/gefion-perf` to investigate slow traces and identify bottlenecks
- Common fixes: `COUNT(*)` on hypertables → `pg_stat_user_tables.n_live_tup`; unbounded JOINs → bound with date range
- Verify fixes via traces (before vs after duration)

### Database
- Use parameterized queries (never string interpolation for SQL)
- Wrap JSONB values with `Json()` adapter for PostgreSQL

### Testing
- Database tests require `ENABLE_DB_TESTS=1` environment variable
- Tests automatically use a separate `gefion_test` database (derived from `DATABASE_URL` + `_test` suffix)
- All DB test connections MUST use `schema.test_db_url()` — never hardcode database URLs
- Use `OTEL_ENABLED=false` to disable tracing in tests
- Run full test suite: `ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://gefion:gefionpass@localhost:6432/gefion" OTEL_ENABLED=false .venv/bin/python -m pytest`
- **Pre-flight for DB test changes**: the suite must pass against a freshly-created test database (what a DB-backed CI job sees). Drop it first, then run the full suite — conftest recreates and initializes it:
  `psql "postgresql://gefion:gefionpass@localhost:6432/postgres" -c 'DROP DATABASE IF EXISTS gefion_test WITH (FORCE)'`
- If db-init fails against `gefion_test` at session start (e.g., a half-initialized database left by an aborted run), drop the database as above and rerun

## Active Technologies
- Python 3.10+ + Streamlit (UI framework), subprocess (process execution) (001-ui-reliability)
- JSONL files in `~/.gefion/` (conversation history, error log); PostgreSQL (system state queries) (001-ui-reliability)
- Python 3.10+ (existing codebase) + scikit-learn, XGBoost, LightGBM, optuna (Bayesian search), psycopg, typer (CLI), streamlit (UI), jinja2 (D3 templates) (004-autonomous-experiments)
- PostgreSQL with TimescaleDB (existing); principles catalog as YAML files in repo (004-autonomous-experiments)
- Python 3.10+ + psycopg (DB), numpy / pandas / scipy / statsmodels (effective-N, (005-regime-slicing)
- PostgreSQL + TimescaleDB. New `regime_definitions` (relational) and `regime_labels` (005-regime-slicing)
- Python 3.10+ + numpy/scipy (tests + enumeration), psycopg (ledgers), existing gefion (006-agentic-regime-discovery)
- PostgreSQL + TimescaleDB. New tables — `regime_discovery_runs`, (006-agentic-regime-discovery)
- Python 3.10+ + psycopg (registry/store), existing `alphavantage/` clien (007-entity-model)
- PostgreSQL + TimescaleDB. Changes — `feature_definitions.entity_table` (007-entity-model)
- Python 3.10+ (existing codebase) + psycopg (ledger + reads), numpy (robust z: median/MAD), (008-data-quality)
- PostgreSQL + TimescaleDB. ONE new table — `data_quality_findings` (008-data-quality)
- Python 3.10+ (existing codebase) + numpy (metrics), existing `gefion.backtest` package (009-short-side-execution)
- **None new.** Backtests run in-memory and return a result payload; (009-short-side-execution)
- Python 3.10+ (existing codebase) + numpy (bootstrap + statistics — stationary bootstrap, (010-spa-reverdict)
- ONE new table — `spa_reverdicts` (append-only per-run results; (010-spa-reverdict)
- ONE new column — `feature_functions.scope` ('stock'|'market', (011-market-dispatcher)
- PostgreSQL + TimescaleDB — existing tables only (013-sector-signals)
- Python 3.10+ (existing codebase) + psycopg (DB), existing `gefion.features.dispatcher` (014-generated-market-features)

## Recent Changes
- 001-ui-reliability: Added Python 3.10+ + Streamlit (UI framework), subprocess (process execution)
