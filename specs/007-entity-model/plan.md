# Implementation Plan: First-Class Entities for the Feature Store

**Branch**: `007-entity-model` | **Date**: 2026-07-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/007-entity-model/spec.md`

## Summary

Retire the feature store's hard-wired entity model (`computed_features.data_id` →
`stocks(id)`) in favor of a **declared** logical key: `feature_definitions` gains
`entity_table` (default `'stocks'`), and identity resolves per feature as
`(entity_table, data_id)`. `macro_series` + `macro_series_values` become the first
non-stock entity home; VIX (AlphaVantage `INDEX_DATA`, FRED fallback) is the proving
case. The registry becomes load-bearing three ways: **identity** (the logical FK),
**feeds** (dictionary-generated graph + Mermaid ERD, solid = DB-enforced, dashed =
registry-declared), and **deletion** (registry-driven `entity-delete`, dry-run first,
replacing the retired cascade). Integrity moves from impossible to loudly detectable:
a `db-health` orphan scan ships in the **same increment** as the FK drop (spec edge
case: no undetectable window). See [design-options.md](./design-options.md) for the
rejected alternatives and the row-vs-table decision rule.

## Technical Context

**Language/Version**: Python 3.10+
**Primary Dependencies**: psycopg (registry/store), existing `alphavantage/` client
(INDEX_DATA fetch + parser), existing `scripts/gen_data_dictionary.py` (feeds/ERD),
typer CLI, existing MCP server. No new third-party dependencies (Simplicity).
**Storage**: PostgreSQL + TimescaleDB. Changes — `feature_definitions.entity_table`
column, `computed_features` FK retirement, new `macro_series` + `macro_series_values`
(plain relational; hypertable exception justified below). **DDL APPROVED by owner
2026-07-08** (contracts/sql.md); applied at implementation via the two-file rule.
**Testing**: pytest; DB tests via `schema.test_db_url()` + `ENABLE_DB_TESTS`; the
migration is proven against a fresh db-init AND an existing database (both paths of
the two-file rule); a manufactured-orphan test drives the health scan.
**Target Platform**: dev machine (synthetic + dev DB); VIX live-call verification and
prod ingest on sloth after merge.
**Project Type**: single project (CLI-first research system).
**Performance Goals**: orphan scan adds <1s to db-health (NOT EXISTS anti-joins on an
indexed key, bounded per entity table); dictionary/ERD generation stays hermetic and
sub-second; VIX ingest is one API call + ~7k row upsert.
**Constraints**: additive migration only (no data moves/renames); registry validation
refuses undeclared entity tables at registration; FK drop, orphan scan, and
entity-delete land in the same increment; parameterized SQL with `psycopg.sql`
identifier composition for dynamic entity-table names; `Json()` for JSONB.
**Scale/Scope**: 21 existing feature definitions default to `'stocks'`; `macro_series`
starts with 1 row (VIX) and must pass the family test (SC-207: second series = zero
DDL); ~7k VIX daily rows over 26 years.

## Constitution Check

*GATE: pass before Phase 0; re-check after Phase 1.*

| Principle | Status | How the plan satisfies it |
|---|---|---|
| I. Database-First | **PASS (gated)** | Registry stays the source of truth — this feature makes it *more* load-bearing (identity/feeds/deletion). DDL follows the two-file rule; **PROPOSED for owner approval, not executed** (contracts/sql.md). `db-init` reaches the same end state fresh or migrated. Exception noted: `macro_series_values` is time-series but plain relational — one series ≈ 7k rows over 26 years; hypertable machinery (chunks, compression jobs) is unjustified at this cardinality (same justification as 006's ledgers). Revisit if the macro family grows past ~50 series. |
| II. TDD (non-negotiable) | **PASS** | Every increment lists tests first; the migration itself is test-gated (schema tests for the new column/tables/absence of the FK); the orphan scan is driven by a manufactured-orphan test; entity-delete by a dry-run/confirm pair. |
| III. CLI-First | **PASS** | New commands `gefion macro ingest|list` and `gefion data entity-delete`; MCP mirrors; UI: Data page macro section deferred with justification (see Interfaces below). `/gefion` operator skill updated. |
| IV. Observability | **PASS** | All new/changed modules import `gefion.observability`; spans on registry validation, loader branching, ingest, orphan scan, delete. |
| V. Consistent CLI Presentation | **PASS** | `get_output()` helpers; `--json` parity everywhere. |
| VI. Simplicity | **PASS** | No new abstraction beyond one column + two small tables; no ORM, no polymorphic framework — identity resolution is one registry lookup; the insert-validation trigger from the spec is explicitly **deferred** (detection via health scan suffices for v1; single write path). |
| Tech Constraints | **PASS** | Parameterized SQL; dynamic table names via `psycopg.sql.Identifier` validated against the registry whitelist; `Json()` for JSONB; type hints + docstrings. |

No unjustified violations. Gated items: (1) DDL owner approval; (2) hypertable
exception recorded above.

## Project Structure

### Documentation (this feature)

```text
specs/007-entity-model/
├── spec.md              # Feature spec (clarified)
├── design-options.md    # Visual design record (Mermaid) + row-vs-table rule
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/           # Phase 1 (sql.md, cli.md, mcp.md, interfaces.md)
└── tasks.md             # Phase 2 (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
src/gefion/entities/                 # NEW small package: the entity layer
├── __init__.py
├── registry.py          # entity-table declaration + validation (whitelist from
│                        #   feature_definitions.entity_table ∪ known tables);
│                        #   safe Identifier composition for dynamic table names
├── orphans.py           # per-entity-table orphan scan (consumed by db-health)
└── deletion.py          # registry-driven entity delete (dry-run/confirm)

src/gefion/macro/                    # NEW small package: macro series L1
├── __init__.py
├── catalog.py           # macro_series catalog CRUD (rows are config — SC-207)
└── ingest.py            # INDEX_DATA fetch (existing client) + FRED fallback,
                         #   upsert into macro_series_values, materialize feature

src/gefion/regimes/discovery/signals.py   # loader branches on entity_table (R5)
src/gefion/features/…                     # registration validates entity_table (R1)
src/gefion/cli.py                          # macro ingest|list, data entity-delete,
                                           #   db-health orphan section
mcp-server/server.py                       # mirrored tools
scripts/gen_data_dictionary.py             # feeds graph + Mermaid ERD (R8)

sql/schema.sql + sql/migrations/NNNNNN_entity_model.sql        # PROPOSED
sql/migrations/NNNNNN_macro_series.sql                          # PROPOSED

tests/
├── test_entity_registry.py        # declaration validation, whitelist, identifiers
├── test_entity_orphans.py         # manufactured orphan → detected; clean → zero
├── test_entity_deletion.py        # dry-run impact, confirm order, cascade parity
├── test_entity_schema.py          # column present, FK absent, macro tables shaped
├── test_macro_ingest.py           # parser, upsert, feature materialization, family test
├── test_signals_entity_branching.py  # single-entity series loader semantics
└── test_data_dictionary_feeds.py  # feeds graph + ERD generation (hermetic)
```

**Structure Decision**: two deliberately small packages. `entities/` is the
cross-cutting layer (registry semantics, orphans, deletion — none of it
macro-specific, per the spec's generality clarification); `macro/` is the first
tenant. Discovery/regimes need only the loader branch — they already consume
features by name.

## Increment sequencing (drives tasks.md)

Ordering is safety-driven: detection and deletion exist **before** the constraint
they compensate for is removed.

1. **Registry declaration (additive, behavior no-op)**: `entity_table` column
   (default `'stocks'`), registration-time validation, schema tests. FK still
   present — pure widening.
2. **Detection + deletion**: orphan scan in db-health; `data entity-delete`
   (dry-run/confirm; for stocks, parity with the existing cascade verified by test).
3. **FK retirement**: the migration drops the constraint — legal only now that #2
   ships; fresh-DB and migrated-DB paths both tested.
4. **Macro home + VIX**: `macro_series`/`macro_series_values`; live INDEX_DATA
   verification call is the FIRST task of the increment (fallback pivot is a
   config change, not a redesign); ingest command; `macro_vix` feature
   materialization; loader branching (R5); discovery/regime consumption proven
   (SC-202/203); the family test exercised with a second synthetic catalog row.
5. **Feeds graph + ERD + docs + curriculum**: generator additions (hermetic, from
   schema.sql + feature-definitions git exports), ARCHITECTURE/DEVELOPMENT/
   DATA_DICTIONARY/USER_GUIDE/MCP_WORKFLOWS updates, Module 1 curriculum
   (row-vs-table rule + peer-group litmus, owner directive), `/gefion` routing.

## Interfaces, Documentation & Learning Impact *(mandatory)*

- **Three interfaces**: parity matrix in contracts/interfaces.md. Summary:
  `macro ingest|list` and `data entity-delete` ship CLI + MCP in their increments;
  UI: the Regimes/Discovery pages consume `macro_vix` with zero changes (it is just
  a feature); a dedicated macro panel on the Data page is **deferred** — macro
  ingest is an operator/cron action with no visual workflow yet (justification per
  the plan-template rule; revisit when a second series lands). db-health's orphan
  section surfaces in the existing health outputs (CLI + MCP `health_check`).
- **Documentation** (same increment as code, per delivery rule):
  ARCHITECTURE.md (layering + declared-entity rule), DEVELOPMENT.md (add-a-table
  checklist additions incl. row-vs-table rule; "add a data source" recipe with VIX
  as the worked example), DATA_DICTIONARY.md (regen: feeds + ERD), README +
  USER_GUIDE (new commands), MCP_WORKFLOWS (new tools), `/gefion` operator skill.
- **Learning materials**: Module 1 (data layer) gains the entity-model concepts,
  the feeds graph, and the row-vs-table decision rule with checkpoint "why is CPI a
  `macro_series` row but sectors would be their own entity table?" (owner
  directive, recorded in spec Documentation Impact).
- **Delivery rule**: surfaces + docs land per increment with the code.

## Complexity Tracking

> No constitution violations require justification beyond the gated/recorded items
> in the Constitution Check (DDL approval; hypertable exception for
> `macro_series_values`).

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
