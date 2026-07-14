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

## Where work lives (tracking convention)

Three homes, one rule each — so "where does this go?" is never ambiguous:

- **GitHub Issues** = the single tracker for anything *actionable*: bugs and
  discrete enhancements. Global state (not per-branch), closeable from a commit
  (`Closes #N`). Use labels: `bug` / `enhancement` / `tech-debt`, `epic` for
  spec-worthy features, and `priority: high|medium|low`.
- **`specs/NNN-*/`** = the design home. When an `epic` issue is ripe, it
  graduates to a spec (spec ⇄ issue cross-link). Designs stay in the repo,
  reviewed through PRs.
- **`.specify/memory/backlog.md`** = a thin roadmap **pointer** only (links to
  open issues + in-flight specs + explicit out-of-scope decisions). It is *not*
  a worklist — don't add actionable items there; file an issue.

Why not a checked-in worklist: per-branch state drifts (done on one branch, open
on another) and can't be closed by a PR. State belongs in issues; design belongs
in `specs/`. (Migration: 2026-07-09, backlog.md items → issues #86–#90.)

## Deletion policy (first-class deletes)

Owner principle (2026-07-08, issue #76): **deletions are first-class actions —
anything we create must be cleanly deletable, together with its associated
data.** Archive-only lifecycles accumulate junk; ad-hoc SQL deletes either fail
on RESTRICT FKs or orphan dependents.

1. **Every plan adding a created artifact includes its deletion story in the
   same increment** — a spec that creates something says how it dies.
2. **Deletes are dependency-aware, dry-run by default, `--confirm` to
   execute** (the `data cull` / `entity-delete` mold): the dry-run reports the
   full blast radius and changes nothing.
3. **Honest exceptions are declared, not implied**: append-only audit ledgers
   (the discovery candidate/diagnostics ledgers, trust grades, quality
   findings) are never deleted by artifact deletion — removing the artifact
   must not remove the accounting. Runs with admitted candidates refuse
   deletion with deliberately no `--force`.
4. **New tables declare their delete story at DDL-approval time**: the
   ON DELETE behavior is chosen deliberately (CASCADE for derived/owned rows,
   RESTRICT for accounting), never defaulted by omission.

Landed doors: `data cull`, `data entity-delete` (007), `regime delete` +
`regime discover delete` (#75/#76). Known gaps (tracked on #76): per-model ML
artifact delete, experiment delete, feature definition/function delete,
disk-artifact reaping.

## Patterns & Gotchas (living reference)

- **DB-is-source-of-truth functions (spec 011)**: market-level function
  bodies live in `feature_functions` (scope='market'); repo code
  (`gefion/macro/market_bodies.py`) is seed-only (create-if-absent — never
  clobbers operator edits; `macro derive --reseed` is the explicit recovery).
  The legacy SQL lives on FROZEN inside the equality-gate test
  (`test_macro_derived.py`) as a permanent regression reference.
- **Function scopes — three, not two** (#120 trace review): `feature_functions.scope`
  is `'stock'` (dispatched per symbol by the sweep), `'market'` (dispatched per date
  over the cross-section, spec 011), or `'materialized'` (never dispatched — values
  written by their own pipeline: macro ingest/derive, ml predict; the row exists so
  the registry knows every function name in use). Anything that creates a
  feature_definition whose values come from elsewhere MUST register a marker via
  `gefion.macro.derived.ensure_materialized_function` — a definition whose
  function_name has no registry row is presumed stock-scope and swept per symbol,
  which is how ~124k futile attempts/night happened. Also: `sql/schema.sql` is the
  fresh-install snapshot — fresh DBs record migrations WITHOUT executing them
  (baseline), so a migration that changes DDL must land in schema.py AND schema.sql.
- **UI rendering of CLI processes** (issue #88): don't write bespoke output
  renderers in views — `gefion.ui.components.cli_output.render_cli_output(key,
  title)` (or `render_cli_state(state, title)`) gives any launched CLI command
  running/complete/error states, `--json` structured display (phase events with
  `step`/`total_steps` progress hints, final summary as metrics/tables), and
  plain-text fallback for free.

> **This is a living document.** When you rediscover a convention or hit a gotcha during
> implementation, add it here so the next author (human or AI) looks it up instead of
> re-deriving it. Append freely; keep entries short and concrete.

### Adding a capability across all surfaces (definition of done)

A user-facing capability is not done until it exists on **CLI, MCP, and UI** and is
documented. Typical order: schema (if needed) → service module → CLI → MCP → UI → docs.

**CLI command** — register a sub-app once, then add commands:
```python
foo_app = typer.Typer(help="Foo commands")
app.add_typer(foo_app, name="foo", cls=SortedGroup)

@foo_app.command("bar")
def foo_bar(
    name: str = typer.Argument(..., help="..."),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL override"),
    json_output: Optional[bool] = typer.Option(None, "--json", help="Output as JSON"),
) -> None:
    from gefion.output import get_output
    out = get_output(json_output)
    ...
    out.success("done"); out.json({...}) if out.json_mode else None
```
Use `out.table(columns=[Column(...)], rows=..., json_data=...)` for tabular output.
Add `--db-url` to any DB-touching command so tests can target the test DB.

**MCP tool** (`mcp-server/server.py`) — wraps the CLI; three edits + a handler:
1. `Tool(name="foo_bar", description=..., inputSchema={...})` in `list_tools()`
2. `elif name == "foo_bar": result = await _foo_bar(arguments)` in `call_tool()`
3. handler: `async def _foo_bar(args): return await _execute_with_health_check(['postgres'], lambda: GefionExecutor().run("foo", "bar", ...))`

**UI view** (`src/gefion/ui/views/`) — `def render_foo():`; register in `src/gefion/ui/app.py`
`PAGES` list + the dispatch `elif current_page == "Foo": ... render_foo()`. Add the view
filename to `expected_views` in `tests/test_ui_components.py`.

### Writing a DB-backed test

```python
import os, psycopg, pytest
from gefion.db import schema

@pytest.fixture
def conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled")
    c = psycopg.connect(schema.test_db_url()); c.autocommit = True
    # clean up rows you touch, before and after
    yield c
    c.close()
```
- Guard every DB test with `ENABLE_DB_TESTS`. Never hardcode a DB URL — use `schema.test_db_url()`.
- For **CLI** tests, pass `--db-url schema.test_db_url()` (see `test_cli_cross_sectional.py`).
- Run: `ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://gefion:gefionpass@localhost:6432/gefion" OTEL_ENABLED=false .venv/bin/python -m pytest`
- Pre-flight: drop `gefion_test` first so the suite runs against a fresh DB (conftest recreates it).

### Adding a database table (checklist)

0. **Row vs table first** (spec 007). Before proposing DDL, ask whether the new
   data is a *row* in an existing home or a new *table*:
   - **Attribute test**: does it need columns no existing peer has? Same shape →
     row.
   - **Peer-group litmus**: would every consumer treat it as "just another one
     of those" (CPI next to VIX in `macro_series`)? → row. Would consumers
     *branch* on it? → its own table.
   - **Consumer-branching smell**: if loaders/queries would grow `if kind ==
     'x'` arms, the shape is wrong — split it out.
   - **Cost asymmetry**: a wrong row is an UPDATE/DELETE; a wrong table is a
     migration. When in doubt, start as a row.
1. **Get owner approval** — schema changes require it (Schema Governance).
2. **Two-file rule**: add the DDL to `sql/schema.sql` **and** a migration
   `sql/migrations/YYYYMMDD_NNNNNN_name.sql`, kept in sync.
3. **Declare the layer** — add the table to `TABLE_LAYER` in
   `scripts/gen_data_dictionary.py` (catalog / raw / derived / registry) and a
   purpose line to `TABLE_PURPOSE`, so it joins the feeds graph. Naming: entity-ness
   is declared, not spelled — no `ent_`/`dim_` prefixes; plain names (`stocks`,
   `macro_series`).
4. **Declare feeder edges** — if features read it, their definitions carry
   `source_table` (and `entity_table` where it IS an entity home); the feeds
   graph flags raw tables with no declared consumers.
5. **Declare the deletion story** (owner principle: deletions are first-class —
   anything created must be cleanly deletable with its associated data).
   Choose ON DELETE rules deliberately: CASCADE for owned children (e.g.
   `macro_series_values`), RESTRICT/NO ACTION where a delete must be refused,
   and confirm `gefion data entity-delete` covers it for entity tables (a
   natural key entry in `gefion.entities.deletion._NATURAL_KEYS` if it has one).
   Audit ledgers are the declared exception — never deleted with artifacts.
6. **Regenerate the data dictionary** — `.venv/bin/python scripts/gen_data_dictionary.py --write`
   and commit `docs/DATA_DICTIONARY.md`. **Easy to forget; the pre-push hook fails if you do.**
7. Verify: drop `gefion_test`, run the schema/DB tests (db-init builds from `schema.sql`).

### Adding a data source (recipe — VIX is the worked example)

Non-stock series never masquerade as stocks. The macro home makes a new series
configuration, not schema:

1. **Verify the provider first** — one live call before building anything
   (the INDEX_DATA lesson: premium endpoint, key not entitled → pivoted to
   `fred:VIXCLS` as a config change, recorded in specs/007 research.md).
2. **Row, not table** (per the rule above): `gefion macro ingest --name vix
   --provider fred:VIXCLS --full` creates the catalog row, fetches, upserts
   values, and materializes the `macro_vix` feature
   (`entity_table='macro_series'`) into `computed_features`.
3. **Export the definition** — `gefion feat-def-export` so the registry stays
   the hermetic source of truth (exports carry `entity_table`) and the feeds
   graph picks up the edge.
4. **Verify consumption** — the feature is now a discovery atom and a
   `regime interaction --by macro_vix` conditioning variable with zero
   equity-pipeline changes; `gefion db-health` entity_integrity stays at zero.
5. A genuinely new *kind* of source (needs its own attributes/lifecycle, or
   consumers would branch) goes through the add-a-table checklist instead.

### Gotchas

- **`create_span(name, **attrs)`** — the first positional arg is the span name. Passing
  `name=...` as an attribute raises `TypeError: got multiple values for argument 'name'`.
  Use a different attribute key (e.g. `regime=...`).
- **A PRIMARY KEY column cannot be NULL.** For an "applies to all / no specific entity"
  row, use a sentinel (e.g. `entity_id INTEGER NOT NULL DEFAULT 0`), not NULL.
- **A TimescaleDB unique/primary key must include the partition column** (usually `date`).
- **JSONB**: wrap values with `Json()` (`from psycopg.types.json import Json`) on write;
  they come back as `dict`.

### Enforcement map (what runs when)

| Hook | When | What it checks |
|------|------|----------------|
| PreToolUse | before Edit/Write to `src/` | TDD — a `tests/` change must accompany `src/` |
| PreCommit | `git commit` | observability import in significant files |
| Pre-push | `git push` | smoke tests (data-dictionary generator, CLI init, config, health) + **data-dictionary drift** (`gen_data_dictionary.py --check`) |
| CI | on PR | full unit + DB suite |
| `tests/test_docs_drift.py` | test suite | documented `gefion <cmd>` commands and `experiment_`/`docs_` MCP tools must exist/be documented |

### More patterns (added while building spec 005 US3)

- **Two JSON-output styles coexist in `cli.py`.** Newer commands use
  `get_output(json_output)` → `out.success/out.json/out.table`; older ones (e.g. the
  `experiment` group) use `emit(...)` / `emit_json(...)` and `rich.Console` directly.
  Match whichever style the command group you're editing already uses.
- **Extending an existing MCP tool with a new argument** = two edits + a test:
  add the property to the tool's `inputSchema`, thread it into the handler's `cmd`
  building, and test by slicing the handler body from the server source
  (`src.index("async def _tool(") .. next "async def"`), asserting the flag appears.
- **Experiments UI results flow**: the results detail view parses the CLI's stdout JSON
  into `data` and renders sections in order — add a new `_render_*(data)` helper and call
  it just before `_render_lifecycle_status(exp_id)` in `views/experiments.py`.
- **`compute_holdout_pvalue` contract** (`experiments/statistical.py`): paired one-sided
  t-test (`ttest_rel` when score lists are equal length); the caller declares direction —
  `"less"` for loss-like scores, `"greater"` for return-like; NaN (identical arms) → 1.0.
  Default holdout scores are **per-symbol** (`paired_result`); regime-conditional
  evaluation needs **per-observation** scores with dates (`paired_result_by_date`).
- **Shell: `cp` may be aliased to `cp -i`** — a scripted overwrite can silently no-op on
  the interactive prompt. Restore files with `git checkout -- <file>` or the Edit tool,
  not `cp`.
- **Stale `.git/index.lock`**: a zero-byte lock with no running git process is leftover
  from an interrupted command — verify with `ps aux | grep git`, then `rm -f`.

### Deployment patterns (added deploying v0.12.0 to prod — see docs/DEPLOYMENT.md)

- **A fresh install is the only honest dependency test.** Long-lived dev machines hide
  undeclared deps (click arrived transitively) and stale image tags (`tempo:latest`
  cached at 2.9 vs fresh-pulled 3.x). After dependency or compose changes, sanity-check
  with a clean venv / fresh pull.
- **Pin service images in compose files** — `latest` means "whatever was current when
  this machine last pulled," which diverges silently between hosts.
- **Ubuntu venv bootstrap**: no `python3.X-venv` package + no sudo →
  `python3 -m venv --without-pip .venv` then pipe get-pip.py into `.venv/bin/python`.
- **Long remote jobs**: run in tmux; stdout through a pipe is block-buffered, so the
  log fills in bursts — verify liveness via the DB row counts (or `ps`), not the log.
- **Remote vs local commands**: when a session mixes ssh and local work, keep each
  Bash call single-target so it's obvious where a command ran.
- **OTEL span export can exceed gRPC limits on huge jobs**: the 207M-row feature build
  produced trace batches larger than the OTLP gRPC 4 MB max (`RESOURCE_EXHAUSTED`,
  spans dropped; data unaffected). For bulk jobs either raise Tempo's
  `max_recv_msg_size`, shrink the exporter's `max_export_batch_size`, or run with
  `OTEL_ENABLED=false` and rely on DB-pulse monitoring.
