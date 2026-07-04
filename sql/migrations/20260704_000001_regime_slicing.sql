-- Migration: Regime Slicing (spec 005)
--
-- Adds first-class regime tables for conditional evaluation across
-- market/sector/asset states. Owner-approved 2026-07-04.
--
-- Changes:
--   1. New table: regime_definitions (the recipe — AST expression, bucketing,
--      persistence, provenance/metadata)
--   2. New hypertable: regime_labels (computed per-(date, entity) states)
--
-- Mirrors the canonical DDL in sql/schema.sql (two-file rule).

-- =============================================================================
-- 1. REGIME DEFINITIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS regime_definitions (
    id                    SERIAL PRIMARY KEY,
    name                  TEXT UNIQUE NOT NULL,
    scope                 TEXT NOT NULL CHECK (scope IN ('market','sector','industry','asset')),
    expression            JSONB NOT NULL,
    bucketing             JSONB NOT NULL,
    persistence           JSONB,
    origin                TEXT NOT NULL DEFAULT 'human',
    descriptive_metadata  JSONB,
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 2. REGIME LABELS (hypertable)
-- =============================================================================

CREATE TABLE IF NOT EXISTS regime_labels (
    regime_id        INTEGER NOT NULL REFERENCES regime_definitions(id),
    date             DATE NOT NULL,
    entity_id        INTEGER NOT NULL DEFAULT 0,     -- 0 = market-wide; else stock id
    label            TEXT NOT NULL,
    dataset_version  TEXT NOT NULL,
    PRIMARY KEY (regime_id, entity_id, date)
);
SELECT create_hypertable('regime_labels', 'date', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS regime_labels_brin ON regime_labels USING BRIN(date);
