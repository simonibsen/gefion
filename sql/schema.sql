-- G2 Database Schema
--
-- Creates core tables for the G2 trading system:
--   - stocks: Stock symbols and metadata
--   - stock_ohlcv: OHLCV price data (hypertable)
--   - feature_definitions: Metadata-driven feature configuration
--   - computed_features: Computed technical indicators (hypertable)
--
-- Prerequisites:
--   - PostgreSQL with TimescaleDB extension
--   - Run: CREATE EXTENSION IF NOT EXISTS timescaledb;
--
-- Usage:
--   psql -d g2 -f sql/schema.sql

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- =============================================================================
-- DIMENSION TABLES
-- =============================================================================

-- Stocks dimension table
-- Stores stock symbols and metadata
CREATE TABLE IF NOT EXISTS stocks (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    status TEXT
);

-- Feature definitions table
-- Metadata-driven feature configuration (calc_store pattern)
-- Features are defined as DATA, not code
CREATE TABLE IF NOT EXISTS feature_definitions (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    function_name TEXT NOT NULL,  -- Routes to compute function (e.g., 'indicator', 'derivative')
    params JSONB,                  -- Function-specific parameters
    source_table TEXT,             -- Where to read source data from
    source_column TEXT,            -- Column to read from source table
    store_table TEXT DEFAULT 'computed_features',
    store_column TEXT,             -- Column to store result in
    store_type TEXT DEFAULT 'double precision',
    active BOOLEAN DEFAULT TRUE,
    version TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================================
-- TIME-SERIES TABLES (HYPERTABLES)
-- =============================================================================

-- Stock prices hypertable
-- OHLCV price data partitioned by date
CREATE TABLE IF NOT EXISTS stock_ohlcv (
    id BIGSERIAL,
    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    open NUMERIC(18,6),
    high NUMERIC(18,6),
    low NUMERIC(18,6),
    close NUMERIC(18,6),
    adjusted_close NUMERIC(18,6),
    dividend_amount NUMERIC(18,6),
    split_coefficient NUMERIC(18,6),
    volume BIGINT,
    source TEXT,
    PRIMARY KEY (id, date),
    UNIQUE (data_id, date)
);

-- Convert to hypertable (30-day chunks)
SELECT create_hypertable('stock_ohlcv', 'date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('stock_ohlcv', INTERVAL '30 days');

-- Computed features hypertable
-- Tall table storing all computed features (indicators, derivatives, etc.)
-- Uses feature_id to reference feature_definitions
CREATE TABLE IF NOT EXISTS computed_features (
    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    feature_id INTEGER NOT NULL REFERENCES feature_definitions(id),
    value DOUBLE PRECISION,
    source TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (feature_id, data_id, date)
);

-- Convert to hypertable (30-day chunks)
SELECT create_hypertable('computed_features', 'date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('computed_features', INTERVAL '30 days');

-- =============================================================================
-- INDEXES
-- =============================================================================

-- Stock prices indexes
-- BRIN index for large date range scans (space-efficient)
CREATE INDEX IF NOT EXISTS stock_ohlcv_brin ON stock_ohlcv USING BRIN(date);

-- Composite B-tree index for single-stock time-series queries
-- Optimized for: SELECT ... WHERE data_id = X AND date BETWEEN Y AND Z ORDER BY date DESC
CREATE INDEX IF NOT EXISTS stock_ohlcv_data_id_date_idx
    ON stock_ohlcv(data_id, date DESC);

-- Computed features indexes
-- BRIN index for date range scans
CREATE INDEX IF NOT EXISTS computed_features_brin ON computed_features USING BRIN(date);

-- Primary lookup index
CREATE INDEX IF NOT EXISTS computed_features_idx
    ON computed_features(feature_id, data_id, date);

-- Composite B-tree index for feature-specific queries with DESC ordering
-- Optimized for: SELECT ... WHERE feature_id = X AND data_id = Y AND date BETWEEN ... ORDER BY date DESC
CREATE INDEX IF NOT EXISTS computed_features_feature_data_date_idx
    ON computed_features(feature_id, data_id, date DESC);

-- =============================================================================
-- SUMMARY
-- =============================================================================

\echo ''
\echo '========================================='
\echo 'G2 Database Initialization Complete'
\echo '========================================='
\echo ''
\echo 'Tables Created:'
\echo '  - stocks (dimension table)'
\echo '  - stock_ohlcv (hypertable)'
\echo '  - feature_definitions'
\echo '  - computed_features (hypertable)'
\echo ''

-- Display table counts
SELECT
    'stocks' as table_name,
    COUNT(*) as row_count
FROM stocks
UNION ALL
SELECT
    'stock_ohlcv',
    COUNT(*)
FROM stock_ohlcv
UNION ALL
SELECT
    'feature_definitions',
    COUNT(*)
FROM feature_definitions
UNION ALL
SELECT
    'computed_features',
    COUNT(*)
FROM computed_features
ORDER BY table_name;
