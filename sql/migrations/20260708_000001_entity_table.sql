-- Migration: the declared entity axis (spec 007, Migration A)
--
-- feature_definitions gains entity_table: which table computed_features.data_id
-- resolves against for this feature — who the value BELONGS TO, independent of
-- source_table (what the computation reads). Owner-approved 2026-07-08
-- (specs/007-entity-model/contracts/sql.md).
--
-- Purely additive; every existing definition defaults to 'stocks', so this is a
-- behavioral no-op (SC-201). The hard computed_features->stocks FK is retired in
-- a LATER migration, only after the orphan scan and entity-delete exist (safety
-- ordering).
--
-- Mirrors the canonical DDL in sql/schema.sql (two-file rule).

ALTER TABLE feature_definitions
    ADD COLUMN IF NOT EXISTS entity_table TEXT NOT NULL DEFAULT 'stocks';
