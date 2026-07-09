-- Migration: data-quality findings audit ledger (spec 008)
-- Approved: specs/008-data-quality/contracts/sql.md, 2026-07-08.
--
-- One row per detection. Append-only in spirit: detection facts are
-- immutable; only the resolution fields may be set later (supersede, never
-- erase). Deliberately NO foreign keys — the ledger survives deletion of the
-- entities it describes (007 entity-delete never touches audit ledgers), and
-- entity identity is the declared pair (entity_table, entity_id).
-- Plain relational, not a hypertable: findings are sparse (006 ledger
-- precedent). observed/expected are DOUBLE PRECISION so the quality ledger
-- cannot overflow on the garbage it convicts (the #79 lesson, applied to
-- ourselves).
CREATE TABLE IF NOT EXISTS data_quality_findings (
    id            SERIAL PRIMARY KEY,
    entity_table  TEXT NOT NULL,
    entity_id     INTEGER NOT NULL,
    metric        TEXT NOT NULL,
    date          DATE NOT NULL,
    rule          TEXT NOT NULL,
    verdict       TEXT NOT NULL CHECK (verdict IN ('trash', 'suspect')),
    observed      DOUBLE PRECISION,
    expected      DOUBLE PRECISION,
    detail        JSONB,
    context       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ,
    resolution    TEXT,
    UNIQUE (entity_table, entity_id, metric, date, rule)
);

CREATE INDEX IF NOT EXISTS data_quality_findings_metric_verdict_idx
    ON data_quality_findings (metric, verdict);
CREATE INDEX IF NOT EXISTS data_quality_findings_entity_idx
    ON data_quality_findings (entity_table, entity_id);
