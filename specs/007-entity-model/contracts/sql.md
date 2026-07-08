# SQL Contract — First-Class Entities (007)

**Status: APPROVED by owner 2026-07-08** (scope: exactly the two migrations below —
the entity_table column, the computed_features FK retirement with its mandated
detection/deletion sequencing, and the macro_series pair with the plain-relational
exception). Approval covers only this change; future schema changes require
separate approval. Applied at implementation via the two-file rule, gated behind
schema tests written first, with the data dictionary regenerated in the same
commits.

Three changes, sequenced across two migrations so detection/deletion exist before
the constraint they compensate for is removed (plan increments 1–4):

## Migration A (increment 1): the declared entity axis

```sql
ALTER TABLE feature_definitions
    ADD COLUMN IF NOT EXISTS entity_table TEXT NOT NULL DEFAULT 'stocks';
```

Additive; all 21 existing definitions default to `'stocks'` (behavioral no-op).

## Migration B (increment 3–4): FK retirement + macro home

```sql
-- 1. Retire the hard-wired entity model. The constraint name is resolved from
--    pg_constraint at migration time (older databases may differ); schema.sql
--    simultaneously loses the REFERENCES clause so fresh databases match.
ALTER TABLE computed_features
    DROP CONSTRAINT IF EXISTS computed_features_data_id_fkey;

-- 2. First non-stock entity: catalog…
CREATE TABLE IF NOT EXISTS macro_series (
    id           SERIAL PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,
    provider     TEXT NOT NULL,          -- e.g. 'alphavantage:INDEX_DATA', 'fred:VIXCLS'
    kind         TEXT NOT NULL,          -- 'index' | 'rate' | 'breadth' | … (label, not schema)
    cadence      TEXT NOT NULL CHECK (cadence IN ('daily','weekly','monthly')),
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. …and its raw values (required value; optional OHLC — serves both the
--    daily-OHLC class (VIX) and the monthly-single-value class (CPI) with zero
--    DDL for the second series: the SC-207 family test).
CREATE TABLE IF NOT EXISTS macro_series_values (
    series_id    INTEGER NOT NULL REFERENCES macro_series(id) ON DELETE CASCADE,
    date         DATE NOT NULL,
    value        NUMERIC(14,6) NOT NULL,
    open         NUMERIC(14,6),
    high         NUMERIC(14,6),
    low          NUMERIC(14,6),
    PRIMARY KEY (series_id, date)
);
```

Notes:
- `macro_series_values` is deliberately **plain relational** (not a hypertable):
  ~7k rows per series over 26 years — hypertable machinery is unjustified at this
  cardinality (same reasoning as 006's ledger tables; revisit past ~50 series).
- The FK drop MUST NOT ship before the db-health orphan scan and
  `data entity-delete` exist (plan sequencing; spec edge case "orphan creation
  window").
- Two-file rule at implementation: `schema.sql` + both migrations, data dictionary
  regenerated in the same commits, gated behind schema tests written first.
- Ratio precision `NUMERIC(14,6)` matches the fundamentals-widening precedent
  (migration 20260707_000002).
