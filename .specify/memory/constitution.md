<!--
  Sync Impact Report
  Version change: 1.7.1 → 1.8.0 (db-init + migration governance)
  Added: db-init as single idempotent entry point (Section I), two-file rule for migrations (Schema Governance)
  Renamed: g2 → gefion throughout
  Templates requiring updates: none
  Follow-up TODOs: none
-->

# Gefion Constitution

## Core Principles

### I. Database-First Architecture

The database is the single source of truth. Feature definitions, compute functions, and all configuration live in the database and are exported to git for version control - never the reverse.

- All feature logic MUST be stored in `feature_functions` and `feature_definitions` tables
- Git exports (`feature-functions/`, `feature-definitions/`) are backups, not primary sources
- Schema changes MUST go through `sql/schema.sql` (canonical DDL) and `sql/migrations/` (incremental changes); no ad-hoc DDL (see Schema Governance below)
- `gefion db-init` is the single idempotent entry point for database setup: it creates tables from `schema.sql`, runs pending migrations, and seeds reference data — a fresh database or an existing one must both reach the correct state from this one command
- TimescaleDB hypertables MUST be used for time-series data (`stock_ohlcv`, `computed_features`, `predictions`)
- Compute functions MUST be pure: no side effects, no file I/O; the dispatcher handles all DB interaction

### II. Test-Driven Development (NON-NEGOTIABLE)

Every code change follows strict Red-Green-Refactor. No exceptions.

- Tests MUST be written before implementation: create/modify files in `tests/` before touching `src/`
- New tests MUST fail before implementation begins (Red)
- Implementation MUST be the minimum code to make tests pass (Green)
- Tests and implementation MUST be committed together
- Enforcement is layered: CLAUDE.md instructions, pre-commit hooks, and Claude Code PreToolUse hooks all enforce TDD order
- Bypassing (via `--no-verify`) requires explicit justification in the commit message

#### Test Database Isolation

Database tests MUST use a dedicated test database, never the development database.

- All test DB connections MUST use `schema.test_db_url()` — hardcoded database URLs in test files are forbidden
- The test database (`gefion_test` by default) is created automatically by `conftest.py` when `ENABLE_DB_TESTS=1`
- Resolution order: `TEST_DATABASE_URL` env var > `DATABASE_URL` with `_test` suffix > default `gefion_test`
- All database test files MUST have an `ENABLE_DB_TESTS` guard (pytestmark or fixture-level skip)

### III. CLI-First Interface

All functionality MUST be accessible through the `gefion` CLI before any other interface.

- Every operation (data update, feature compute, ML train, backtest) MUST have a CLI command
- CLI commands MUST support both human-readable and JSON output formats
- The MCP server wraps CLI commands for natural language access; it does not bypass them
- New capabilities MUST be usable from the command line without requiring a UI or API
- Major CLI commands MUST have corresponding MCP tool definitions to ensure natural language accessibility
- Claude Code skills (`.claude/commands/`) MUST be prefixed with `gefion-` (e.g., `gefion-services.md`) to namespace them from third-party skills
- When new MCP tools are added, the `/gefion` operator skill MUST be reviewed and updated to include the new capabilities in its workflow guidance and tool routing

### IV. Observability

Every significant operation MUST be traceable and debuggable.

- New modules MUST import from `gefion.observability`
- Significant operations MUST use the `@traced` decorator for OpenTelemetry spans
- All modules MUST use structured logging via `logger = logging.getLogger(__name__)`
- Traces MUST be queryable through Grafana Tempo for performance investigation
- Database operations MUST be instrumented to identify slow queries

#### Trace-Driven Development

Traces are not just telemetry — they are a development tool. Actively inspecting traces during development catches performance regressions, verifies code paths, and ensures instrumentation is correct.

- **Tempo MUST be running during development** — start it with `/services start`; it is not optional infrastructure
- **Trace inspection is part of the dev loop** — after implementing or modifying a feature, verify its traces via `gefion span-check` or the Tempo API before considering the work complete
- **Span parenting is mandatory** — every child span MUST propagate its parent context. Orphaned spans (spans with no parent inside an operation that should have one) are defects, not style issues. Pass `context` or use `@traced` within an already-traced call stack to ensure linkage
- **Performance awareness** — use `gefion trace-search` and `gefion trace-compare` to identify slow operations and verify optimizations. If a trace shows unexpected duration or span count, investigate before merging

### V. Consistent CLI Presentation

All `gefion` CLI output MUST have a unified look and feel. Colors, progress bars, status indicators, tables, and error formatting MUST use a shared library - not ad-hoc inline formatting.

- All terminal output (colors, spinners, progress bars, tables, panels) MUST go through a common presentation module
- New commands MUST NOT introduce their own formatting with raw ANSI codes or one-off Rich/click styling
- Status indicators MUST use a consistent vocabulary and color scheme across all commands (e.g., success = green, error = red, warning = yellow)
- Progress bars for long-running operations (data update, feature compute, ML training) MUST use the same style
- JSON output mode (`--json`) MUST bypass all presentation formatting and return clean structured data

### VI. Simplicity

Start simple. Avoid premature abstraction. YAGNI.

- Solve the current problem with the minimum necessary complexity
- Three similar lines of code are better than a premature abstraction
- Do not design for hypothetical future requirements
- Do not add error handling for scenarios that cannot occur
- Prefer editing existing files over creating new ones
- Every added dependency or abstraction MUST be justified by a concrete current need

## Technology Constraints

- **Language**: Python 3.10+
- **Database**: PostgreSQL with TimescaleDB extension
- **ML**: scikit-learn, XGBoost, LightGBM (no deep learning unless justified)
- **Data source**: AlphaVantage API (premium tier for production)
- **Observability**: OpenTelemetry with Grafana Tempo backend
- **Queries**: Parameterized SQL only; string interpolation for SQL is forbidden
- **JSONB**: Wrap values with `Json()` adapter for PostgreSQL
- **Type hints**: Required for all function signatures
- **Docstrings**: Required for all public functions

## Development Workflow

1. **Specify**: Define what you're building and why before writing code
2. **Test first**: Write failing tests that describe the expected behavior
3. **Implement**: Write the minimum code to make tests pass
4. **Verify**: Run the full test suite (`make test-db` for DB tests, `make test` for unit tests)
5. **Observe**: Check traces with `gefion span-check` after performance-sensitive changes
6. **Commit**: Tests and implementation together in one commit

### Plan Structure

Plans MUST list test files before implementation files. Each implementation step MUST be paired with a test. Success criteria MUST include "All tests pass."

## Documentation Requirements

Major features MUST include documentation updates as part of the implementation. Code without corresponding docs is not complete.

- **README.md**: Update if the feature adds new CLI commands, changes setup steps, or alters the architecture diagram
- **.specify/memory/progress.md**: Update current capabilities and status
- **.specify/memory/backlog.md**: Remove completed items; add follow-up work if applicable
- **docs/USER_GUIDE.md**: Update for any new or changed CLI commands
- **docs/ML_QUICKSTART.md**: Update if ML pipeline behavior changes
- **docs/ARCHITECTURE.md**: Update if the feature introduces new modules, tables, or data flows
- **What counts as a major feature**: New CLI commands, new modules in `src/gefion/`, new database tables, new MCP tools, new strategies, or changes to existing user-facing behavior
- **What does NOT require doc updates**: Internal refactors with no behavior change, bug fixes, test-only changes, dependency bumps
- **`.specify/` is version-controlled**: All files under `.specify/` (memory, specs, templates, scripts) MUST be committed to git and kept consistent across branches

## Schema Governance

The database schema is the backbone of gefion. Because it controls data flow, feature computation, and ML pipelines, all schema changes require explicit owner approval.

- **No autonomous schema changes**: Claude Code MUST NOT create, alter, or drop tables, columns, indexes, hypertables, or constraints without owner approval
- **Propose, don't execute**: When a feature requires schema changes, present the proposed DDL for review. Do not write it to `sql/schema.sql` or run it against the database until approved
- **Scope of approval**: Each approval covers the specific change discussed. Approval of one migration does not authorize future schema changes
- **Two-file rule**: Every schema change MUST update both `sql/schema.sql` (canonical DDL for fresh databases) and add a migration in `sql/migrations/` (incremental change for existing databases). The two MUST be kept in sync — `schema.sql` represents the final state, the migration gets there from the previous state
- **Migration naming**: `YYYYMMDD_NNNNNN_descriptive_name.sql` — tracked in the `schema_migrations` table
- **db-init handles both**: `gefion db-init` runs `schema.sql` (CREATE IF NOT EXISTS — safe on existing DBs) then applies pending migrations. This ensures any database reaches the current schema regardless of its starting state
- **What counts as a schema change**:
  - Adding, renaming, or removing tables or columns
  - Changing column types, defaults, or constraints
  - Adding or removing indexes
  - Creating or modifying hypertables, triggers, or views
  - Any raw DDL (`CREATE`, `ALTER`, `DROP`)
- **What does NOT require approval**:
  - DML operations (`INSERT`, `UPDATE`, `DELETE`) through normal application code
  - Registering new feature definitions or functions (these are data, not schema)
  - Read-only queries for exploration or analysis

## Secrets Management

Files containing secrets (API keys, database passwords, tokens) MUST NOT be committed to version control.

- **`.env` files are gitignored**: `.env`, `.env.prod`, and any environment-specific env files MUST be listed in `.gitignore`
- **Use `.env.example` for templates**: Tracked example files MUST contain placeholder values, never real credentials
- **No secrets in code or config**: Secrets MUST be loaded from environment variables or `.env` files at runtime, never hardcoded in source files, docker-compose files, or other tracked configuration
- **Review before committing**: Any new file that may contain secrets (env files, credential configs, key files) MUST be added to `.gitignore` before first commit

## Governance

This constitution supersedes all ad-hoc practices. Amendments require:

1. A clear rationale for the change
2. Impact assessment on existing code and workflows
3. Version bump following semantic versioning (MAJOR for principle removals/redefinitions, MINOR for additions, PATCH for clarifications)
4. Update to dependent artifacts (CLAUDE.md, templates, hooks)

All code changes MUST comply with these principles. Complexity that violates a principle MUST be explicitly justified and documented.

**Version**: 1.8.0 | **Ratified**: 2026-02-28 | **Last Amended**: 2026-03-27
