# Phase 0 Research: First-Class Entities (007)

The load-bearing *what/why* decisions were fixed in design review (spec
Clarifications + design-options.md). This resolves the remaining *technical how*.

## R1 — Entity-table declaration & validation

**Decision**: `feature_definitions.entity_table TEXT NOT NULL DEFAULT 'stocks'`.
Registration-time validation (feature-definition import/registration paths) refuses
a declaration unless the named table (a) exists in `information_schema.tables`,
(b) has an integer `id` primary key. The set of legal entity tables is therefore
*self-maintaining* — no separate registry-of-registries table (Simplicity). Dynamic
table names in any subsequent SQL are composed with `psycopg.sql.Identifier` only
after passing this validation (never interpolated strings).

**Rationale**: the spec's edge case ("entity table that does not exist: refused at
registration time") plus the tech constraint (parameterized SQL) with the minimum
machinery.

**Alternatives**: an `entity_tables` catalog table (rejected — a registry to govern
the registry; YAGNI at 2 entity tables); CHECK constraint enumerating tables
(rejected — every new entity kind would need DDL on `feature_definitions`).

## R2 — FK retirement mechanics

**Decision**: migration drops `computed_features_data_id_fkey` by introspected name
(`ALTER TABLE … DROP CONSTRAINT IF EXISTS` with the name resolved from
`pg_constraint` at migration time, since older databases may differ); `schema.sql`
loses the `REFERENCES stocks(id) ON DELETE CASCADE` clause so fresh databases match.
Both paths tested: fresh db-init and migration-on-existing. Sequenced as increment
3, strictly **after** the orphan scan and entity-delete exist (spec edge case: no
undetectable-orphan window; FR-211 same-increment coupling interpreted as "detection
ships before or with the drop, never after").

**Rationale**: two-file rule with an honest answer to constraint-name drift; safety
ordering is the plan's core sequencing principle.

## R3 — Shape of `macro_series_values` (the family test's first exam)

**Decision**: one row per (series, date) with a **required `value`** and **optional
OHLC**: `(series_id, date, value NUMERIC NOT NULL, open, high, low NUMERIC NULL)`.
For VIX, `value = close` and OHLC is populated; for CPI (monthly, single number),
only `value` is. Feature definitions read `value` by default (`source_column`).

**Rationale**: serves both known cadence/shape classes with zero DDL for the second
series (SC-207); avoids both a too-narrow clone of VIX's OHLC shape and an
over-general EAV/`(series, date, field, value)` design that would push pivoting
complexity into every consumer.

**Alternatives**: EAV narrow table (rejected — consumers pivot forever); strict
OHLC columns (rejected — CPI would need NULL gymnastics or DDL); JSONB payload
(rejected — loses SQL-level typing for the one column everything reads).

## R4 — VIX provider

**Decision**: AlphaVantage `INDEX_DATA&symbol=VIX` through the **existing client**
(new fetch method + `catalog.py` parser). The endpoint is premium; the plan's first
implementation task in the macro increment is **one live verification call** on the
production key. Fallback if unavailable: FRED `VIXCLS` (free, close-only — fits R3's
`value`-only path) via a minimal client. The pivot is an ingest-config change, not a
redesign (the entity model is provider-agnostic).

**Rationale**: reuse over new dependency; verify-before-build was already recorded
in the backlog item; the fallback path exercises R3's shape flexibility.

## R5 — Market-level loader branching

**Decision**: the market-series loader (`_feature_series` / `load_market_data`)
resolves each feature's `entity_table` from the registry in the same query. For
`'stocks'` features, behavior is byte-identical to today (median across entities,
symbol-universe filtering). For non-stock features: no symbol filtering (the
universe chain governs *stocks*), and the per-date aggregate over the feature's own
entities — which for a single-entity series degenerates to the value itself, per
FR-208. Cross-entity-table aggregation cannot arise: aggregation is always
per-feature, and a feature has exactly one entity table.

**Scope note**: ML dataset builds join features per *symbol* and will simply not
see macro features — correct and unchanged. Broadcasting market-level features into
per-symbol datasets is a named follow-on, not part of 007 (spec FR-207 lists
discovery/regimes/interaction as the consumers).

## R6 — Orphan scan

**Decision**: for each `DISTINCT entity_table` in the registry, one anti-join:
values of features declaring that table whose `data_id` has no row in it. Reported
in `db-health` under a new `entity_integrity` section with per-table counts and an
actionable warning naming `data entity-delete` / investigation steps — the exact
pattern (and code path) of the `dimension_coverage` section shipped this week.
Runtime bound: anti-join on integer keys, one pass per entity table (2 tables now).

## R7 — Registry-driven entity deletion

**Decision**: `gefion data entity-delete <entity_table> <key>` — key is the natural
key where one exists (`stocks.symbol`, `macro_series.name`), id otherwise. Dry-run
by default: reports feature-value counts per feature (registry edges), plus any
hard-FK dependents discovered from `pg_constraint` (e.g., `stock_ohlcv` rows for a
stock) so the operator sees the *full* blast radius. `--confirm` deletes registry
edges (feature values) first, then defers to the database for hard-FK dependents
(cascade where declared; clear refusal listing blockers where RESTRICT). For
stocks, a parity test proves cleanup ≥ the retired cascade. Never touches audit
ledgers (discovery candidates/diagnostics/grades) — deleting an artifact never
deletes accounting (owner principle's declared exception, issue #76).

**Alternatives**: full leaf→root cascade of *all* dependents in v1 (rejected —
that is `data cull`'s domain and issue #76's roadmap; 007 replaces exactly what the
retired FK provided, plus visibility).

## R8 — Feeds graph + Mermaid ERD generation (hermetic)

**Decision**: extend `scripts/gen_data_dictionary.py`, keeping it hermetic (CI runs
it without a database): hard-FK edges parsed from `sql/schema.sql`; registry edges
(`source_table`, `entity_table`) read from the **git exports** of feature
definitions (`feature-definitions/*.json`) — Database-First's export discipline
makes those the reviewable mirror of the DB rows. Output: a layer-grouped Mermaid
`flowchart` set (dimension / raw / feature store / consumers) — solid arrows for
DB-enforced edges, dashed for registry-declared — plus the consumer-less-raw-table
flag (SC-204: `stocks_fundamentals` today). Mermaid `flowchart` over `erDiagram`
because the latter cannot render the dashed/solid distinction that FR-209a makes
load-bearing.

## R9 — Write-path validation (deferred trigger)

**Decision**: no insert trigger in v1. The dispatcher is the single write path into
the feature store; registration-time validation (R1) plus the orphan scan (R6)
bound the risk. The trigger remains a named option if the health check ever shows
recurring orphans.

**Rationale**: Simplicity; the spec marked the trigger "considered but not required
for v1"; detection proved sufficient twice on production this week.
