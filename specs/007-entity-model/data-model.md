# Phase 1 Data Model: First-Class Entities (007)

**All DDL is PROPOSED for owner approval (Schema Governance) — see contracts/sql.md.
Nothing below is written to `sql/schema.sql` or executed until approved.**

## Changed: FeatureDefinition → `feature_definitions`

| Field | Type | Notes |
|---|---|---|
| *(existing columns unchanged)* | | name, function_name, params, source_table, source_column, store_*, active, version… |
| `entity_table` | TEXT NOT NULL DEFAULT `'stocks'` | **NEW** — the declared entity axis: which table `computed_features.data_id` resolves against for this feature. Validated at registration (table exists, integer `id` PK). Independent of `source_table` (what the computation reads). |

Backfill: none needed — the default covers all 21 existing definitions (behavioral
no-op, SC-201).

## Changed: FeatureValue → `computed_features`

| Field | Change |
|---|---|
| `data_id` | Column unchanged (INTEGER NOT NULL). The **hard FK to `stocks(id)` is retired** (constraint dropped by introspected name in the migration; `REFERENCES` clause removed from schema.sql). Identity is the logical key `(feature → entity_table, data_id)`. |

Integrity: registration validation (R1) + db-health orphan scan (R6), shipping in
the same increment as the drop. Deletion parity: registry-driven entity delete (R7).

## New: MacroSeries (catalog) → `macro_series`

| Field | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | the entity id feature values point at |
| `name` | TEXT UNIQUE NOT NULL | kebab/lower, e.g. `vix` (feature `macro_vix` reads it) |
| `provider` | TEXT NOT NULL | e.g. `alphavantage:INDEX_DATA`, `fred:VIXCLS` |
| `kind` | TEXT NOT NULL | `index` \| `rate` \| `breadth` \| `price` \| … (label, not schema — consumer branching on kind beyond provenance/cadence is the design smell from design-options.md) |
| `cadence` | TEXT NOT NULL | `daily` \| `weekly` \| `monthly` |
| `description` | TEXT | |
| `created_at` | TIMESTAMPTZ DEFAULT NOW() | |

Rows are configuration, not schema (SC-207): the second series is an INSERT.

## New: MacroSeriesValues (raw L1) → `macro_series_values`

| Field | Type | Notes |
|---|---|---|
| `series_id` | INTEGER NOT NULL REFERENCES `macro_series(id)` ON DELETE CASCADE | catalog is the parent; deleting a series takes its raw values (its *feature* values go via entity-delete) |
| `date` | DATE NOT NULL | |
| `value` | NUMERIC(14,6) NOT NULL | THE series value (close for OHLC series; the single number for CPI-class series) — what feature definitions read |
| `open` / `high` / `low` | NUMERIC(14,6) NULL | optional OHLC extras (VIX has them; CPI doesn't) |
| PK | `(series_id, date)` | |

Plain relational, not hypertable: one series ≈ 7k rows / 26 years; hypertable
machinery unjustified at this cardinality (Constitution Check exception; revisit
past ~50 series).

## Relationships

```
feature_definitions.entity_table ──declares──▶ {stocks | macro_series | future…}
computed_features.data_id ──resolves via feature's entity_table──▶ entity row   (dashed: logical)
macro_series 1─∞ macro_series_values                                            (solid: FK, CASCADE)
stocks 1─∞ stock_ohlcv / stocks_fundamentals / …                                 (solid: existing FKs)
computed_features ✂ stocks                                                        (hard FK RETIRED)
```

## Validation rules

- `entity_table` refused at registration unless the table exists with an integer
  `id` PK (R1); dynamic identifiers composed via `psycopg.sql.Identifier` only.
- Aggregation is always per-feature ⇒ never crosses entity tables (FR-208 by
  construction).
- Orphan = a `computed_features` row whose feature's `entity_table` has no row with
  that `data_id` → surfaced by db-health `entity_integrity`, never silent.

## State transitions

None (no lifecycle columns added; macro_series rows are created/deleted, not
status-flipped — deletion goes through `data entity-delete`).
