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
    status TEXT,
    name TEXT,
    sector TEXT,
    industry TEXT,
    exchange TEXT,
    asset_type TEXT,
    updated_at TIMESTAMP
);
-- Ensure columns exist on existing tables (idempotent for upgrades)
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sector TEXT;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS industry TEXT;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS exchange TEXT;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS asset_type TEXT;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS stocks_sector_idx ON stocks(sector);
CREATE INDEX IF NOT EXISTS stocks_industry_idx ON stocks(industry);
CREATE INDEX IF NOT EXISTS stocks_exchange_idx ON stocks(exchange) WHERE exchange IS NOT NULL;

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

-- Function registry for reusable feature functions
CREATE TABLE IF NOT EXISTS feature_functions (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    description TEXT,
    language TEXT NOT NULL,        -- e.g., python, sql (python_expr also supported for legacy)
    function_body TEXT NOT NULL,   -- code or template
    inputs JSONB,                  -- expected inputs schema
    output_name TEXT DEFAULT 'value',
    output_type TEXT DEFAULT 'double precision',
    param_schema JSONB,            -- JSON schema for params
    defaults JSONB,                -- default params
    dependencies JSONB,            -- packages/UDFs needed
    checksum TEXT,                 -- hash of body+version
    tags TEXT[],                   -- e.g., {volume, indicator}
    min_app_version TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    created_by TEXT,
    called_by TEXT,                -- parent meta-function for plugin architecture
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(name, version)
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

-- Company fundamentals hypertable
-- Time-series of fundamental data (market cap, PE, etc.) from AlphaVantage OVERVIEW
-- Updated weekly by data-update; source for cross-sectional computed features
CREATE TABLE IF NOT EXISTS stocks_fundamentals (
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    date DATE NOT NULL,
    market_cap BIGINT,
    pe_ratio NUMERIC(10,2),
    forward_pe NUMERIC(10,2),
    peg_ratio NUMERIC(10,4),
    book_value NUMERIC(12,4),
    dividend_yield NUMERIC(8,6),
    eps NUMERIC(10,4),
    revenue_per_share NUMERIC(10,4),
    profit_margin NUMERIC(8,6),
    operating_margin NUMERIC(8,6),
    return_on_equity NUMERIC(8,6),
    beta NUMERIC(8,4),
    ev_to_ebitda NUMERIC(10,2),
    shares_outstanding BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (data_id, date)
);
SELECT create_hypertable('stocks_fundamentals', 'date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('stocks_fundamentals', INTERVAL '90 days');
CREATE INDEX IF NOT EXISTS stocks_fundamentals_data_date_idx
    ON stocks_fundamentals(data_id, date DESC);

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
-- STRATEGY MANAGEMENT
-- =============================================================================

-- Strategy registry - maps strategy names to Python implementations
CREATE TABLE IF NOT EXISTS strategy_registry (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    module_path TEXT NOT NULL,
    class_name TEXT NOT NULL,
    default_params JSONB DEFAULT '{}',
    param_schema JSONB,
    description TEXT,
    tags TEXT[],
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Strategy configurations - parameterized instances of strategies
CREATE TABLE IF NOT EXISTS strategy_configs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL REFERENCES strategy_registry(name) ON DELETE CASCADE,
    params JSONB NOT NULL DEFAULT '{}',
    description TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

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

-- Feature definitions indexes
-- Partial index for active feature lookups (most common query pattern)
-- Optimized for: SELECT ... FROM feature_definitions WHERE active = TRUE AND function_name IN (...)
CREATE INDEX IF NOT EXISTS idx_feature_definitions_active_function
    ON feature_definitions(active, function_name)
    WHERE active = TRUE;

-- Ensure called_by column exists (for upgrades from older schema)
ALTER TABLE feature_functions ADD COLUMN IF NOT EXISTS called_by TEXT;

-- Feature functions index for plugin discovery
-- Optimizes: WHERE called_by = 'meta_function' AND enabled = TRUE AND status = 'active'
CREATE INDEX IF NOT EXISTS idx_feature_functions_called_by_enabled_status
    ON feature_functions (called_by, enabled, status)
    WHERE called_by IS NOT NULL;

-- Strategy registry indexes
CREATE INDEX IF NOT EXISTS idx_strategy_registry_enabled
    ON strategy_registry(enabled, name)
    WHERE enabled = TRUE;

CREATE INDEX IF NOT EXISTS idx_strategy_configs_active
    ON strategy_configs(active, strategy_name)
    WHERE active = TRUE;

-- =============================================================================
-- ML TABLES
-- =============================================================================

-- ML dataset registry
CREATE TABLE IF NOT EXISTS ml_datasets (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    universe JSONB,
    feature_names TEXT[] NOT NULL,
    lookback_days INTEGER NOT NULL,
    horizons_days INTEGER[] NOT NULL,
    label_spec JSONB NOT NULL,
    split_spec JSONB NOT NULL,
    artifact_uri TEXT NOT NULL,
    checksum TEXT,
    UNIQUE (name, version)
);
CREATE INDEX IF NOT EXISTS ml_datasets_name_idx ON ml_datasets(name);

-- ML run tracking
CREATE TABLE IF NOT EXISTS ml_runs (
    id SERIAL PRIMARY KEY,
    run_type TEXT NOT NULL,  -- 'train' | 'predict' | 'eval'
    status TEXT NOT NULL DEFAULT 'running',
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    dataset_id INTEGER REFERENCES ml_datasets(id),
    run_config JSONB NOT NULL,
    code_version TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS ml_runs_type_status_idx ON ml_runs(run_type, status);

-- ML model registry
CREATE TABLE IF NOT EXISTS ml_models (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    train_run_id INTEGER REFERENCES ml_runs(id),
    dataset_id INTEGER REFERENCES ml_datasets(id),
    algorithm TEXT,
    hyperparams JSONB,
    metrics JSONB,
    artifact_uri TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    UNIQUE (name, version)
);
CREATE INDEX IF NOT EXISTS ml_models_active_idx ON ml_models(active, name);

-- Unified predictions table (hypertable)
-- Stores both quantile and trend_class predictions with JSONB values
CREATE TABLE IF NOT EXISTS predictions (
    model_id INTEGER NOT NULL REFERENCES ml_models(id),
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    prediction_date DATE NOT NULL,
    horizon_days INTEGER NOT NULL,
    prediction_type TEXT NOT NULL,
    prediction_values JSONB NOT NULL,
    metadata JSONB DEFAULT '{}',
    run_id INTEGER REFERENCES ml_runs(id),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (model_id, data_id, prediction_date, horizon_days, prediction_type),
    CONSTRAINT check_horizon_positive CHECK (horizon_days > 0),
    CONSTRAINT check_prediction_type CHECK (prediction_type IN ('quantile', 'trend_class'))
);
SELECT create_hypertable('predictions', 'prediction_date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('predictions', INTERVAL '30 days');

-- Prediction outcomes for evaluation
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    prediction_date DATE NOT NULL,
    outcome_date DATE NOT NULL,
    horizon_days INTEGER NOT NULL,
    actual_return NUMERIC(10,4),
    model_id INTEGER REFERENCES ml_models(id),
    created_at TIMESTAMP DEFAULT NOW(),
    run_id INTEGER REFERENCES ml_runs(id),
    PRIMARY KEY (data_id, prediction_date, horizon_days)
);
SELECT create_hypertable('prediction_outcomes', 'prediction_date', if_not_exists => TRUE);

-- Model performance metrics (one row per model+horizon combination)
CREATE TABLE IF NOT EXISTS model_performance (
    model_id INTEGER NOT NULL REFERENCES ml_models(id),
    model_name TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    PRIMARY KEY (model_id, horizon_days),
    q10_calibration NUMERIC(5,2),
    q50_calibration NUMERIC(5,2),
    q90_calibration NUMERIC(5,2),
    quantile_loss NUMERIC(10,6),
    avg_iqr NUMERIC(10,4),
    eval_start_date DATE,
    eval_end_date DATE,
    num_predictions INTEGER,
    updated_at TIMESTAMP DEFAULT NOW(),
    eval_run_id INTEGER REFERENCES ml_runs(id)
);
CREATE INDEX IF NOT EXISTS model_performance_name_horizon_idx ON model_performance(model_name, horizon_days);

CREATE INDEX IF NOT EXISTS predictions_symbol_date_idx
    ON predictions(data_id, prediction_date, horizon_days);
CREATE INDEX IF NOT EXISTS predictions_type_idx
    ON predictions(prediction_type, prediction_date DESC);
CREATE INDEX IF NOT EXISTS predictions_run_id_idx
    ON predictions(run_id);
CREATE INDEX IF NOT EXISTS prediction_outcomes_symbol_date_idx
    ON prediction_outcomes(data_id, prediction_date, horizon_days);
-- Legacy index name kept for reference (now covered by predictions_symbol_date_idx)
-- quantile_predictions_symbol_date_idx
-- trend_class_predictions_symbol_date_idx

-- =============================================================================
-- VOLATILITY THRESHOLDS
-- =============================================================================

-- Per-stock adaptive thresholds based on historical volatility
CREATE TABLE IF NOT EXISTS volatility_thresholds (
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    horizon_days INTEGER NOT NULL,
    calculation_date DATE NOT NULL,
    historical_volatility NUMERIC(10,6),
    bb_width NUMERIC(10,6),
    weak_threshold NUMERIC(10,6),
    strong_threshold NUMERIC(10,6),
    volatility_percentile NUMERIC(5,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (data_id, horizon_days, calculation_date)
);
SELECT create_hypertable('volatility_thresholds', 'calculation_date', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS volatility_thresholds_symbol_date_idx
    ON volatility_thresholds(data_id, calculation_date, horizon_days);

-- =============================================================================
-- SIGNAL STRENGTH VIEW
-- =============================================================================

-- Dynamic signal strength computation from unified predictions table
CREATE OR REPLACE VIEW signal_strength_view AS
WITH params AS (
    SELECT
        0.4::numeric AS quantile_weight,
        0.4::numeric AS classifier_weight,
        0.2::numeric AS persistence_weight
),
quantile_signals AS (
    SELECT
        p.data_id,
        p.prediction_date,
        p.horizon_days,
        (p.prediction_values->>'q10')::NUMERIC(10,4) AS q10,
        (p.prediction_values->>'q50')::NUMERIC(10,4) AS q50,
        (p.prediction_values->>'q90')::NUMERIC(10,4) AS q90,
        (p.prediction_values->>'q90')::NUMERIC(10,4) - (p.prediction_values->>'q10')::NUMERIC(10,4) AS iqr_width,
        vt.strong_threshold,
        vt.weak_threshold,
        vt.historical_volatility,
        GREATEST(-1, LEAST(1,
            (p.prediction_values->>'q50')::NUMERIC / NULLIF(vt.strong_threshold, 0)
        )) AS quantile_component,
        CASE
            WHEN vt.historical_volatility > 0 THEN
                GREATEST(0, LEAST(1,
                    1 - (((p.prediction_values->>'q90')::NUMERIC - (p.prediction_values->>'q10')::NUMERIC) / (vt.historical_volatility * 2))
                ))
            ELSE 0.5
        END AS quantile_confidence
    FROM predictions p
    LEFT JOIN LATERAL (
        SELECT strong_threshold, weak_threshold, historical_volatility
        FROM volatility_thresholds
        WHERE data_id = p.data_id
          AND horizon_days = p.horizon_days
          AND calculation_date <= p.prediction_date
        ORDER BY calculation_date DESC
        LIMIT 1
    ) vt ON TRUE
    WHERE p.prediction_type = 'quantile'
),
classifier_signals AS (
    SELECT
        p.data_id,
        p.prediction_date,
        p.horizon_days,
        p.prediction_values->>'predicted_class' AS predicted_class,
        (p.prediction_values->>'p_strong_down')::NUMERIC(5,4) AS p_strong_down,
        (p.prediction_values->>'p_weak_down')::NUMERIC(5,4) AS p_weak_down,
        (p.prediction_values->>'p_neutral')::NUMERIC(5,4) AS p_neutral,
        (p.prediction_values->>'p_weak_up')::NUMERIC(5,4) AS p_weak_up,
        (p.prediction_values->>'p_strong_up')::NUMERIC(5,4) AS p_strong_up,
        (COALESCE((p.prediction_values->>'p_strong_up')::NUMERIC, 0) * 1.0 +
         COALESCE((p.prediction_values->>'p_weak_up')::NUMERIC, 0) * 0.5 +
         COALESCE((p.prediction_values->>'p_neutral')::NUMERIC, 0) * 0.0 +
         COALESCE((p.prediction_values->>'p_weak_down')::NUMERIC, 0) * -0.5 +
         COALESCE((p.prediction_values->>'p_strong_down')::NUMERIC, 0) * -1.0) AS classifier_component,
        COALESCE((p.prediction_values->>'margin')::NUMERIC, 0.5) AS margin,
        COALESCE((p.prediction_values->>'margin')::NUMERIC, 0.5) AS classifier_confidence
    FROM predictions p
    WHERE p.prediction_type = 'trend_class'
)
SELECT
    COALESCE(qs.data_id, cs.data_id) AS data_id,
    s.symbol,
    COALESCE(qs.prediction_date, cs.prediction_date) AS prediction_date,
    COALESCE(qs.horizon_days, cs.horizon_days) AS horizon_days,
    qs.quantile_component,
    cs.classifier_component,
    qs.q50,
    qs.q10,
    qs.q90,
    cs.predicted_class,
    GREATEST(-1, LEAST(1,
        COALESCE(qs.quantile_component, 0) * (SELECT quantile_weight FROM params) +
        COALESCE(cs.classifier_component, 0) * (SELECT classifier_weight FROM params)
    )) AS signal_score,
    CASE
        WHEN GREATEST(-1, LEAST(1,
            COALESCE(qs.quantile_component, 0) * (SELECT quantile_weight FROM params) +
            COALESCE(cs.classifier_component, 0) * (SELECT classifier_weight FROM params)
        )) > 0.3 THEN 'bullish'
        WHEN GREATEST(-1, LEAST(1,
            COALESCE(qs.quantile_component, 0) * (SELECT quantile_weight FROM params) +
            COALESCE(cs.classifier_component, 0) * (SELECT classifier_weight FROM params)
        )) < -0.3 THEN 'bearish'
        ELSE 'neutral'
    END AS signal_direction,
    qs.quantile_confidence,
    cs.classifier_confidence,
    cs.margin,
    (COALESCE(qs.quantile_confidence, 0.5) + COALESCE(cs.classifier_confidence, 0.5)) / 2 AS avg_confidence,
    qs.iqr_width,
    qs.strong_threshold,
    qs.weak_threshold,
    qs.historical_volatility
FROM quantile_signals qs
FULL OUTER JOIN classifier_signals cs
    ON qs.data_id = cs.data_id
    AND qs.prediction_date = cs.prediction_date
    AND qs.horizon_days = cs.horizon_days
LEFT JOIN stocks s ON COALESCE(qs.data_id, cs.data_id) = s.id;

-- =============================================================================
-- AI EXPERIMENTATION FRAMEWORK
-- =============================================================================

-- Experiments table - tracks experiment definitions and status
CREATE TABLE IF NOT EXISTS experiments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    experiment_type VARCHAR(50) NOT NULL,  -- strategy_params, feature_selection, etc.

    -- Configuration (JSONB for flexibility)
    config JSONB NOT NULL,  -- Type-specific config
    search_space JSONB,     -- Parameters to explore

    -- Objective & Goal (optional)
    objective_metric VARCHAR(50) DEFAULT 'sharpe_ratio',  -- What to optimize
    objective_direction VARCHAR(10) DEFAULT 'maximize',   -- maximize or minimize
    goal_target NUMERIC(12,6),           -- Optional: target value (e.g., 2.0 for Sharpe > 2.0)
    goal_type VARCHAR(20),               -- 'achieve' (absolute), 'improve' (relative), 'minimize'
    baseline_value NUMERIC(12,6),        -- For 'improve': current performance to beat
    early_stop_on_goal BOOLEAN DEFAULT FALSE,  -- Stop when goal achieved?

    -- Execution
    status VARCHAR(20) DEFAULT 'proposed',  -- proposed, approved, running, completed, failed, rejected
    priority INTEGER DEFAULT 0,

    -- Chaining
    parent_experiment_id INTEGER REFERENCES experiments(id),
    depends_on_output VARCHAR(100),  -- Which output from parent to use

    -- Results
    results JSONB,           -- Best params, metrics, etc.
    artifacts_path VARCHAR(500),  -- Path to saved models/files
    goal_achieved BOOLEAN,   -- Did we meet the goal?

    -- Metadata
    proposed_by VARCHAR(50) DEFAULT 'ai',  -- ai or user
    approved_by VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    -- Tracking
    total_trials INTEGER,
    completed_trials INTEGER DEFAULT 0,
    best_score NUMERIC(12,6),

    CONSTRAINT valid_status CHECK (status IN ('proposed', 'approved', 'running', 'completed', 'failed', 'rejected')),
    CONSTRAINT valid_goal_type CHECK (goal_type IS NULL OR goal_type IN ('achieve', 'improve', 'minimize')),
    CONSTRAINT valid_direction CHECK (objective_direction IN ('maximize', 'minimize'))
);

CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_experiments_type ON experiments(experiment_type);
CREATE INDEX IF NOT EXISTS idx_experiments_parent ON experiments(parent_experiment_id);

-- Experiment trials table - individual trial results
CREATE TABLE IF NOT EXISTS experiment_trials (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    trial_number INTEGER NOT NULL,

    -- Parameters tested
    params JSONB NOT NULL,

    -- Results
    metrics JSONB NOT NULL,  -- sharpe_ratio, total_return, max_drawdown, etc.
    score NUMERIC(12,6),     -- Primary optimization metric

    -- Metadata
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    duration_seconds NUMERIC(10,2),

    UNIQUE(experiment_id, trial_number)
);

CREATE INDEX IF NOT EXISTS idx_trials_experiment ON experiment_trials(experiment_id);
CREATE INDEX IF NOT EXISTS idx_trials_score ON experiment_trials(score DESC);

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
\echo '  - strategy_registry'
\echo '  - strategy_configs'
\echo '  - ml_datasets'
\echo '  - ml_runs'
\echo '  - ml_models'
\echo '  - predictions (hypertable, unified quantile + trend_class)'
\echo '  - prediction_outcomes (hypertable)'
\echo '  - model_performance'
\echo '  - volatility_thresholds (hypertable)'
\echo '  - experiments'
\echo '  - experiment_trials'
\echo ''
\echo 'Views Created:'
\echo '  - signal_strength_view'
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
UNION ALL
SELECT
    'strategy_registry',
    COUNT(*)
FROM strategy_registry
UNION ALL
SELECT
    'strategy_configs',
    COUNT(*)
FROM strategy_configs
ORDER BY table_name;
