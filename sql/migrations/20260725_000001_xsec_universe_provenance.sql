-- Migration: Universe provenance on cross_sectional_features (issue #153)
--
-- A stored decile rank is only meaningful relative to the population it was
-- ranked within. With one modeling universe this was merely implicit; with a
-- second universe it becomes ambiguous. Every ranking row now records the
-- universe it was computed over — name + fingerprint, the spec-015
-- ml_datasets.universe provenance pattern.
--
-- Legacy posture: rows written before this migration keep NULL in both
-- columns. NULL means "population unknown" (pre-015 unfiltered market or
-- post-015 default universe — not distinguishable after the fact). Readers
-- must never silently treat NULL as modeling_default: absence of data is
-- not evidence.
--
-- Also creates the table if missing: cross_sectional_features previously
-- existed only via the 20251205 migration and was absent from sql/schema.sql,
-- so databases initialized fresh by db-init (which baselines migrations
-- without executing them) lacked it entirely.

CREATE TABLE IF NOT EXISTS cross_sectional_features (
    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    feature_name TEXT NOT NULL,
    comparison_group TEXT NOT NULL DEFAULT 'market',
    value DOUBLE PRECISION,
    rank INTEGER,
    percentile DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (data_id, date, feature_name, comparison_group)
);

SELECT create_hypertable('cross_sectional_features', 'date', if_not_exists => TRUE);

ALTER TABLE cross_sectional_features
    ADD COLUMN IF NOT EXISTS universe_name TEXT,
    ADD COLUMN IF NOT EXISTS universe_fingerprint TEXT;

COMMENT ON COLUMN cross_sectional_features.universe_name IS
    'Modeling universe the ranking population was drawn from (spec 015). '
    'NULL = pre-provenance legacy row: population unknown, never to be '
    'read as modeling_default.';
COMMENT ON COLUMN cross_sectional_features.universe_fingerprint IS
    'Fingerprint (sha256:…) of the universe definition at compute time; '
    'NULL for the unfiltered ''all'' universe and for legacy rows.';

CREATE INDEX IF NOT EXISTS cross_sectional_features_brin
    ON cross_sectional_features USING BRIN(date);
CREATE INDEX IF NOT EXISTS cross_sectional_features_data_id_date_idx
    ON cross_sectional_features(data_id, date DESC);
CREATE INDEX IF NOT EXISTS cross_sectional_features_date_idx
    ON cross_sectional_features(date DESC);
CREATE INDEX IF NOT EXISTS cross_sectional_features_comparison_group_idx
    ON cross_sectional_features(comparison_group, date);
CREATE INDEX IF NOT EXISTS cross_sectional_features_feature_date_group_rank_idx
    ON cross_sectional_features(feature_name, date, comparison_group, rank);
