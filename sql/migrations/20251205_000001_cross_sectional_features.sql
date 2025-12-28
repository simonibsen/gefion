-- Migration: Add cross_sectional_features table
--
-- Cross-sectional features compare stocks to their peers at the same point in time
-- (vs time-series features which compare a stock to its own history).
--
-- Examples: return_vs_market, return_vs_sector, market_rank
--
-- Prerequisites:
--   - Base schema (stocks table exists)
--   - TimescaleDB extension enabled
--
-- Usage:
--   psql -d g2 -f sql/migrations/002_cross_sectional_features.sql

-- =============================================================================
-- CROSS-SECTIONAL FEATURES TABLE
-- =============================================================================

-- Cross-sectional features hypertable
-- Stores market-relative and sector-relative feature values with rankings
CREATE TABLE IF NOT EXISTS cross_sectional_features (
    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    feature_name TEXT NOT NULL,
    value DOUBLE PRECISION,
    rank INTEGER,
    percentile DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (data_id, date, feature_name)
);

-- Convert to hypertable (30-day chunks)
SELECT create_hypertable('cross_sectional_features', 'date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('cross_sectional_features', INTERVAL '30 days');

-- =============================================================================
-- INDEXES
-- =============================================================================

-- BRIN index for date range scans
CREATE INDEX IF NOT EXISTS cross_sectional_features_brin
    ON cross_sectional_features USING BRIN(date);

-- Composite index for feature-specific queries
-- Optimized for: SELECT ... WHERE feature_name = X AND date = Y ORDER BY rank
CREATE INDEX IF NOT EXISTS cross_sectional_features_feature_date_rank_idx
    ON cross_sectional_features(feature_name, date, rank);

-- Composite index for stock-specific queries with DESC ordering
-- Optimized for: SELECT ... WHERE data_id = X AND date BETWEEN Y AND Z ORDER BY date DESC
CREATE INDEX IF NOT EXISTS cross_sectional_features_data_id_date_idx
    ON cross_sectional_features(data_id, date DESC);

\echo ''
\echo '========================================='
\echo 'Cross-Sectional Features Migration Complete'
\echo '========================================='
\echo ''
\echo 'Table Created:'
\echo '  - cross_sectional_features (hypertable)'
\echo ''
