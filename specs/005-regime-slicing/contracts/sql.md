# SQL Contract (PROPOSED — owner approval required) — Regime Slicing (005)

Per Schema Governance, this DDL is **proposed for review only**. It MUST NOT be written to
`sql/schema.sql` or run against any database until the owner approves. On approval, apply the
two-file rule: add to `schema.sql` (canonical, `CREATE IF NOT EXISTS`) *and* a paired migration.

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
    regime_id        INT NOT NULL REFERENCES regime_definitions(id),
    date             DATE NOT NULL,
    entity_id        INT,                             -- NULL for market scope
    label            TEXT NOT NULL,                   -- bucket label or 'undefined'
    dataset_version  TEXT NOT NULL,
    PRIMARY KEY (regime_id, entity_id, date)
);
SELECT create_hypertable('regime_labels', 'date', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS brin_regime_labels_date ON regime_labels USING BRIN (date);
```

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
