# SQL Contract — Regime Slicing (005)

**Status: APPROVED by owner 2026-07-04** (scope: exactly the two tables below, with the four
decisions — CHECK-enum scope, JSONB columns, `entity_id=0` sentinel, provenance on labels).
Approval covers only this change; future schema changes require separate approval.

On implementation, apply the two-file rule: add to `schema.sql` (canonical, `CREATE IF NOT
EXISTS`) *and* a paired migration `sql/migrations/20260704_NNNNNN_regime_slicing.sql`, kept in
sync. Sequenced as the first (test-guarded) implementation task so db-init/migration applies
cleanly before any data-layer code.

## `regime_definitions`
```sql
CREATE TABLE IF NOT EXISTS regime_definitions (
    id                    SERIAL PRIMARY KEY,
    name                  TEXT UNIQUE NOT NULL,
    scope                 TEXT NOT NULL CHECK (scope IN ('market','sector','industry','asset')),
    expression            JSONB NOT NULL,            -- RegimeExpression AST
    bucketing             JSONB NOT NULL,
    persistence           JSONB,                     -- {min_dwell, mode} — nullable (off by default)
    origin                TEXT NOT NULL DEFAULT 'human',
    dataset_provenance    JSONB,
    descriptive_metadata  JSONB,
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## `regime_labels` (hypertable)
```sql
CREATE TABLE IF NOT EXISTS regime_labels (
    regime_id        INT  NOT NULL REFERENCES regime_definitions(id),
    date             DATE NOT NULL,
    entity_id        INT  NOT NULL DEFAULT 0,          -- 0 = market-wide (no specific entity)
    label            TEXT NOT NULL,                    -- bucket label or 'undefined'
    dataset_version  TEXT NOT NULL,
    PRIMARY KEY (regime_id, entity_id, date)           -- includes partition col `date` (TimescaleDB req.)
);
SELECT create_hypertable('regime_labels', 'date', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS brin_regime_labels_date ON regime_labels USING BRIN (date);
```
Note: `entity_id` uses sentinel `0` for market scope rather than NULL, because a PRIMARY KEY
column cannot be NULL. Sector/industry/asset scopes use the stock id.

## Migration
```
sql/migrations/YYYYMMDD_NNNNNN_regime_slicing.sql   -- mirrors the above; tracked in schema_migrations
```

## Constraints honored
- Parameterized queries only in application code; JSONB values wrapped with `Json()`.
- `entity_id` NULL-vs-value encodes scope granularity (market → NULL; sector/industry/asset → id).
- Exactly one label per (regime_id, entity_id, date) enforced by PK.
- No `COUNT(*)` on `regime_labels` for coverage — use `pg_stat_user_tables.n_live_tup` /
  per-bucket aggregates bounded by regime_id.
