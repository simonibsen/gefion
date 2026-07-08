-- Migration: retire the computed_features→stocks hard FK + macro home (spec 007)
-- Approved: contracts/sql.md 2026-07-08. MUST NOT ship before the db-health
-- orphan scan and `gefion data entity-delete` exist (they landed first —
-- T006–T010): the constraint's replacements precede its removal, so there is
-- never an undetectable-orphan window.
--
-- Entity identity becomes declared, not hard-wired: the pair
-- (feature_definitions.entity_table, computed_features.data_id) is the
-- logical FK, validated at registration and audited by the orphan scan.

-- 1. Drop the FK by introspected name (older databases may not use the
--    default computed_features_data_id_fkey name).
DO $$
DECLARE
    fk_name TEXT;
BEGIN
    SELECT conname INTO fk_name
    FROM pg_constraint
    WHERE contype = 'f'
      AND conrelid = 'computed_features'::regclass
      AND confrelid = 'stocks'::regclass;
    IF fk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE computed_features DROP CONSTRAINT %I', fk_name);
    END IF;
END $$;

-- 2. First non-stock entity: the macro-series catalog…
CREATE TABLE IF NOT EXISTS macro_series (
    id           SERIAL PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,
    provider     TEXT NOT NULL,          -- e.g. 'alphavantage:INDEX_DATA', 'fred:VIXCLS'
    kind         TEXT NOT NULL,          -- 'index' | 'rate' | 'breadth' | … (label, not schema)
    cadence      TEXT NOT NULL CHECK (cadence IN ('daily','weekly','monthly')),
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. …and its raw values. Deliberately plain relational (not a hypertable):
--    ~7k rows per series over 26 years. Required value + optional OHLC serves
--    both the daily-OHLC class (VIX) and the monthly-single-value class (CPI)
--    with zero DDL for the second series (SC-207).
CREATE TABLE IF NOT EXISTS macro_series_values (
    series_id    INTEGER NOT NULL REFERENCES macro_series(id) ON DELETE CASCADE,
    date         DATE NOT NULL,
    value        NUMERIC(14,6) NOT NULL,
    open         NUMERIC(14,6),
    high         NUMERIC(14,6),
    low          NUMERIC(14,6),
    PRIMARY KEY (series_id, date)
);
