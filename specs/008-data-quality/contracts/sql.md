# SQL Contract — Provider-Garbage Detection & Quarantine (008)

**Status: PROPOSED — awaiting owner approval** (Schema Governance: propose, don't
execute). Nothing below is written to `sql/schema.sql`, `sql/migrations/`, or any
database until approved. Scope on approval: exactly this one table and its two
indexes; future schema changes require separate approval.

One new table. No changes to any existing table, column, or constraint.

## Migration (increment 2): the findings audit ledger

```sql
-- Data-quality findings: one row per detection (spec 008).
-- Append-only audit ledger: detection facts are immutable; only the resolution
-- fields may be set later. Deliberately NO foreign keys — the ledger must
-- survive deletion of the entities it describes (007 entity-delete never
-- touches audit ledgers; issue #76's declared exception), and entity identity
-- is the declared pair (entity_table, entity_id) per spec 007.
-- Plain relational, not a hypertable: findings are sparse (hundreds on today's
-- prod) — same reasoning as the 006 discovery ledgers.
CREATE TABLE IF NOT EXISTS data_quality_findings (
    id            SERIAL PRIMARY KEY,
    entity_table  TEXT NOT NULL,               -- 'stocks' | 'macro_series' | …
    entity_id     INTEGER NOT NULL,            -- id in the declared table (no FK, by design)
    metric        TEXT NOT NULL,               -- catalog metric name (beta, dividend_yield, vix, …)
    date          DATE NOT NULL,               -- observation date the value belongs to
    rule          TEXT NOT NULL,               -- definitional_bound | cross_field | temporal_spike | cross_sectional_outlier
    verdict       TEXT NOT NULL CHECK (verdict IN ('trash', 'suspect')),
    observed      DOUBLE PRECISION,            -- float8: garbage is unbounded by definition
    expected      DOUBLE PRECISION,            -- recomputed/bound value where applicable
    detail        JSONB,                       -- rule-specific context (inputs, tolerance, z, neighbors)
    context       TEXT,                        -- detecting command/run
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ,                 -- set only by explicit resolution (supersede, never erase)
    resolution    TEXT,
    UNIQUE (entity_table, entity_id, metric, date, rule)   -- idempotence by construction
);

CREATE INDEX IF NOT EXISTS data_quality_findings_metric_verdict_idx
    ON data_quality_findings (metric, verdict);
CREATE INDEX IF NOT EXISTS data_quality_findings_entity_idx
    ON data_quality_findings (entity_table, entity_id);
```

Notes:
- **observed/expected are DOUBLE PRECISION, not NUMERIC**: the ledger records
  values that are garbage precisely because they exceed sane ranges — a
  fixed-precision quality ledger overflowing on the values it convicts would
  repeat issue #79 inside the fix.
- **UNIQUE (entity_table, entity_id, metric, date, rule)**: re-validation and
  backfill re-runs upsert (refresh observed/expected/detail), never duplicate
  (FR-306, SC-305 idempotence).
- **verdict CHECK** is the storage guard; the code-level rule that only tiers 1–2
  may write `trash` (FR-304) is enforced and tested in `gefion.quality.rules`.
- **Deletion story (007 checklist)**: no ON DELETE anywhere — nothing references
  this table and it references nothing. `gefion data entity-delete` excludes it
  (audit ledger). Layer: audit/ops, outside the feeds graph like the discovery
  ledgers; `TABLE_PURPOSE` entry + data-dictionary regen in the same commit.
- **Two-file rule at implementation**: `sql/schema.sql` + migration
  `2026MMDD_NNNNNN_data_quality_findings.sql`, gated behind schema tests written
  first (RED), dictionary regenerated in the same commit.
- No hypertable, no trigger, no view. The catalog (`data-quality/catalog.yaml`)
  is repo configuration, not schema.
