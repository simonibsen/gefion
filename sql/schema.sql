-- G2 Database Schema - Complete Initialization
--
-- This script creates:
--   1. PRODUCTION TABLES: Current system (stocks, prices, features)
--   2. FUTURE TABLES: AI-driven feature engineering (functions-as-data, meta-learning)
--
-- All tables are safe to create (idempotent with IF NOT EXISTS).
-- Future tables are ready for when those features are implemented.
--
-- Prerequisites:
--   - PostgreSQL with TimescaleDB extension
--   - Run: CREATE EXTENSION IF NOT EXISTS timescaledb;
--
-- Usage:
--   psql -d g2 -f sql/schema.sql
--
-- To skip future tables, comment out the "FUNCTIONS-AS-DATA TABLES" section.

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
    created_at TIMESTAMP DEFAULT NOW(),
    implementation_id INTEGER      -- Optional: Link to dynamic implementation (functions-as-data)
);

-- =============================================================================
-- FUNCTIONS-AS-DATA TABLES (FUTURE - NOT YET IMPLEMENTED)
-- =============================================================================
-- These tables support AI-driven feature engineering documented in:
-- docs/FUNCTIONS_AS_DATA.md
--
-- Status: Documented but not yet implemented
-- Purpose: Enable dynamic function implementations and meta-learning
--
-- To skip these tables, comment out this entire section
-- =============================================================================

-- Function implementations table
-- Stores compute function implementations as data (not just definitions)
-- Enables AI agents to create and test new implementations without code deployment
CREATE TABLE IF NOT EXISTS function_implementations (
    id SERIAL PRIMARY KEY,
    function_name TEXT NOT NULL,   -- e.g., 'momentum_exp', 'rsi_custom'
    version TEXT NOT NULL,         -- e.g., '2024-12-03-v1'
    language TEXT DEFAULT 'python',
    source_code TEXT NOT NULL,     -- Function implementation as string
    signature JSONB,               -- Function signature/interface
    dependencies TEXT[],           -- Required packages
    safety_level TEXT,             -- 'safe', 'review_required', 'sandbox_only'
    created_by TEXT,               -- 'ai_agent', 'human', 'system'
    created_at TIMESTAMP DEFAULT NOW(),
    test_results JSONB,            -- Unit test outcomes
    performance_metrics JSONB,     -- Execution time, memory, sharpe ratio, etc.
    active BOOLEAN DEFAULT FALSE,  -- Only active implementations are used
    approved_by TEXT,              -- Human who reviewed and approved
    approved_at TIMESTAMP,
    UNIQUE (function_name, version)
);

-- Feature patterns table
-- Stores learned patterns from successful implementations (meta-learning)
-- AI queries these patterns to generate better features over time
CREATE TABLE IF NOT EXISTS feature_patterns (
    id SERIAL PRIMARY KEY,
    pattern_type TEXT NOT NULL,    -- 'window_size', 'weighting_scheme', 'indicator_combo', etc.
    pattern_name TEXT NOT NULL,    -- e.g., 'momentum_7_to_14_optimal'
    description TEXT,
    context JSONB,                 -- When does this pattern apply? (asset_class, feature_family, etc.)
    evidence JSONB,                -- Statistical support (tested_windows, avg_sharpe, p_value, etc.)
    confidence NUMERIC(5,2),       -- 0-100 score (Bayesian updated over time)
    first_observed TIMESTAMP DEFAULT NOW(),
    last_validated TIMESTAMP,
    times_validated INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT TRUE,
    UNIQUE (pattern_type, pattern_name)
);

-- Implementation-pattern link table
-- Maps which implementations use which patterns
-- Enables pattern validation and discovery
CREATE TABLE IF NOT EXISTS implementation_patterns (
    implementation_id INTEGER REFERENCES function_implementations(id) ON DELETE CASCADE,
    pattern_id INTEGER REFERENCES feature_patterns(id) ON DELETE CASCADE,
    PRIMARY KEY (implementation_id, pattern_id)
);

-- Pattern performance table
-- Time-series tracking of pattern validation results
-- Enables confidence score updates and pattern decay
CREATE TABLE IF NOT EXISTS pattern_performance (
    id SERIAL PRIMARY KEY,
    pattern_id INTEGER NOT NULL REFERENCES feature_patterns(id) ON DELETE CASCADE,
    evaluated_at TIMESTAMP DEFAULT NOW(),
    metric_name TEXT,              -- 'sharpe', 'information_ratio', 'feature_importance', etc.
    metric_value NUMERIC,
    sample_size INTEGER,
    test_symbols TEXT[]            -- Which symbols were tested
);

-- Add foreign key constraint for feature_definitions.implementation_id
-- (done separately to handle idempotency)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'feature_definitions_implementation_id_fkey'
    ) THEN
        ALTER TABLE feature_definitions
        ADD CONSTRAINT feature_definitions_implementation_id_fkey
        FOREIGN KEY (implementation_id)
        REFERENCES function_implementations(id)
        ON DELETE SET NULL;
    END IF;
END $$;

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

\echo ''
\echo '========================================='
\echo 'G2 Database Initialization Complete'
\echo '========================================='
\echo ''
\echo 'PRODUCTION TABLES (Current System):'
\echo '  - stocks'
\echo '  - stock_prices (hypertable)'
\echo '  - feature_definitions'
\echo '  - computed_features (hypertable)'
\echo '  - company_fundamentals_history (hypertable)'
\echo ''
\echo 'FUTURE TABLES (AI-Driven Feature Engineering):'
\echo '  - function_implementations'
\echo '  - feature_patterns'
\echo '  - implementation_patterns'
\echo '  - pattern_performance'
\echo ''
\echo 'Documentation:'
\echo '  - docs/FUNCTIONS_AS_DATA.md'
\echo '  - docs/ML_ROADMAP.md'
\echo '  - docs/SECURITY_SANDBOXING.md'
\echo ''

-- Display table counts
SELECT
    'PRODUCTION TABLES' as category,
    '' as table_name,
    NULL as row_count
UNION ALL
SELECT
    '',
    'stocks',
    COUNT(*)
FROM stocks
UNION ALL
SELECT
    '',
    'stock_prices',
    COUNT(*)
FROM stock_prices
UNION ALL
SELECT
    '',
    'feature_definitions',
    COUNT(*)
FROM feature_definitions
UNION ALL
SELECT
    '',
    'computed_features',
    COUNT(*)
FROM computed_features
UNION ALL
SELECT
    '',
    'company_fundamentals_history',
    COUNT(*)
FROM company_fundamentals_history
UNION ALL
SELECT
    'FUTURE TABLES',
    '',
    NULL
UNION ALL
SELECT
    '',
    'function_implementations',
    COUNT(*)
FROM function_implementations
UNION ALL
SELECT
    '',
    'feature_patterns',
    COUNT(*)
FROM feature_patterns
UNION ALL
SELECT
    '',
    'implementation_patterns',
    COUNT(*)
FROM implementation_patterns
UNION ALL
SELECT
    '',
    'pattern_performance',
    COUNT(*)
FROM pattern_performance
ORDER BY category DESC, table_name;
