-- G2 Database Schema
--
-- Complete schema for g2 application including all tables, hypertables, and indexes.
-- Safe to run multiple times (uses IF NOT EXISTS).
--
-- Prerequisites:
--   - TimescaleDB extension must be enabled
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
CREATE TABLE IF NOT EXISTS stock_prices (
    id BIGSERIAL,
    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    open NUMERIC(18,6),
    high NUMERIC(18,6),
    low NUMERIC(18,6),
    close NUMERIC(18,6),
    adjusted_close NUMERIC(18,6),
    volume BIGINT,
    source TEXT,
    PRIMARY KEY (id, date),
    UNIQUE (data_id, date)
);

-- Convert to hypertable (30-day chunks)
SELECT create_hypertable('stock_prices', 'date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('stock_prices', INTERVAL '30 days');

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

-- Company fundamentals history hypertable (optional)
-- Wide table for fundamental data time series
CREATE TABLE IF NOT EXISTS company_fundamentals_history (
    id BIGSERIAL,
    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    market_cap DOUBLE PRECISION,
    pe_ratio DOUBLE PRECISION,
    peg_ratio DOUBLE PRECISION,
    dividend_yield DOUBLE PRECISION,
    eps DOUBLE PRECISION,
    revenue_per_share DOUBLE PRECISION,
    profit_margin DOUBLE PRECISION,
    operating_margin DOUBLE PRECISION,
    roe DOUBLE PRECISION,
    roa DOUBLE PRECISION,
    beta DOUBLE PRECISION,
    shares_outstanding BIGINT,
    source TEXT,
    PRIMARY KEY (id, date),
    UNIQUE (data_id, date)
);

-- Convert to hypertable (30-day chunks)
SELECT create_hypertable('company_fundamentals_history', 'date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('company_fundamentals_history', INTERVAL '30 days');

-- =============================================================================
-- INDEXES
-- =============================================================================

-- Stock prices indexes
-- BRIN index for large date range scans (space-efficient)
CREATE INDEX IF NOT EXISTS stock_prices_brin ON stock_prices USING BRIN(date);

-- Composite B-tree index for single-stock time-series queries
-- Optimized for: SELECT ... WHERE data_id = X AND date BETWEEN Y AND Z ORDER BY date DESC
CREATE INDEX IF NOT EXISTS stock_prices_data_id_date_idx
    ON stock_prices(data_id, date DESC);

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

-- Company fundamentals indexes
CREATE INDEX IF NOT EXISTS company_fundamentals_history_brin
    ON company_fundamentals_history USING BRIN(date);

-- =============================================================================
-- SUMMARY
-- =============================================================================

-- Display table counts
SELECT
    'stocks' as table_name,
    COUNT(*) as row_count
FROM stocks
UNION ALL
SELECT
    'stock_prices',
    COUNT(*)
FROM stock_prices
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
