# Development Guide

## Prerequisites
- Python 3.10+
- Docker & Docker Compose
- Services needed: PostgreSQL (TimescaleDB), Grafana Tempo, Grafana
- Start all: `docker compose up -d postgres` and `docker compose -f docker/tempo/docker-compose.tempo.yml up -d`
- Or use Claude Code skill: `/gefion-services start`

## TDD Workflow (Required)
1. Write tests in `tests/` FIRST
2. Run pytest — verify tests FAIL (red)
3. Implement minimum code in `src/` to pass
4. Run pytest — verify tests PASS (green)
5. Commit tests + implementation together

Enforcement:
- Pre-commit hook blocks commits with `src/` changes but no `tests/` changes
- Claude Code PreToolUse hook blocks writing to `src/` before `tests/`

Database tests require `ENABLE_DB_TESTS=1`. All DB tests use `gefion.db.schema.test_db_url()` for a separate test database.

## Observability (Required)
- All significant modules must `from gefion.observability import create_span, set_attributes`
- Wrap operations: `with create_span("module.function", key=value) as span:`
- Pre-commit hook blocks commits of significant files without observability imports
- Zero overhead when `OTEL_ENABLED=false` (the default)
- CLI auto-loads `.env` and calls `reinitialize()` to enable tracing

## Performance Workflow
1. Ensure Tempo is running (`curl -s http://localhost:3200/ready`)
2. Set `OTEL_ENABLED=true` in `.env` (CLI loads this automatically)
3. Run your code — traces flow to Tempo
4. Check traces: `gefion span-check` or `/gefion-perf` in Claude Code
5. Identify bottlenecks (slow spans, N+1 patterns)
6. Fix and re-run — verify improvement via new traces

TraceQL queries via Tempo MCP:
- `{duration > 500ms}` — all slow spans
- `{span.name =~ "db."}` — database operations
- `{span.name =~ "ui."}` — page loads
- `{status = error}` — errors

## Span Naming Conventions

| Prefix | Example | Description |
|--------|---------|-------------|
| `cli.*` | `cli.data-update` | CLI command execution |
| `ui.*` | `ui.dashboard.render` | UI page rendering |
| `db.*` | `db.get_connection` | Database operations |
| `compute_features` | `compute_features` | Feature computation |
| `alphavantage.*` | `alphavantage.api_call` | External API calls |
| `mcp.*` | `mcp.ml_train` | MCP server tool calls |

## Performance Thresholds

| Operation | Threshold | Rationale |
|-----------|-----------|-----------|
| `ui.*` | 500ms | Page loads should feel instant |
| `db.*` | 500ms | Queries should be fast |
| `charts.*` | 2000ms | Chart rendering has overhead |
| `cli.*` | 5000ms | CLI commands include I/O |
| `experiments.*` | 10000ms | Experiments are expected to be slow |
| default | 1000ms | General operations |

## Automated Hooks

| Hook | When | What |
|------|------|------|
| SessionStart | Claude Code opens | Checks postgres + tempo running |
| PreToolUse | Before code edit | TDD: tests before src changes |
| PreCommit | Before git commit | Observability imports required |
| PostToolUse (Bash) | After pytest | Queries Tempo for slow spans |

## Running Tests

```bash
# Quick (no database)
make test

# Full suite with database
make test-db

# Manual with all options
ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://gefion:gefionpass@localhost:6432/gefion" \
  OTEL_ENABLED=false pytest tests/
```

## Versioning

Semantic versioning (`0.x.y`). The `0.` prefix signals alpha/pre-release.
Releases are fully automated (issue #30) — no manual bumping or tagging.

- **Version source of truth**: git tags (`vX.Y.Z`). `setuptools-scm` derives
  the package version from the latest tag at install/build time;
  `gefion.__version__` reads it from package metadata. Nothing in the repo
  hardcodes a version.
- **How releases happen**: on every push to main, the Release workflow runs
  `python-semantic-release`, which parses Conventional Commits since the last
  tag (`fix:` → patch, `feat:` → minor) and pushes a new tag + GitHub Release
  with generated notes. Tag-only: it never commits back to main.
- **Changelog**: the [releases page](https://github.com/simonibsen/gefion/releases)
  (generated from commit messages — another reason commit subjects matter).
- **While on 0.x**: `major_on_zero = false`, so breaking changes bump minor,
  not to 1.0. Going 1.0 is a deliberate decision.

## Code Style
- Type hints for all function signatures
- Parameterized SQL queries (never string interpolation)
- Wrap JSONB values with `Json()` adapter for PostgreSQL
- Use `gefion.db.pool` for connection pooling in parallel operations
