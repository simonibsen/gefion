-- Migration: Add volatility_thresholds table
-- Date: 2024-12-29
-- Description: Store per-stock adaptive thresholds based on historical volatility

-- Volatility thresholds table
-- Stores per-stock, per-horizon thresholds that replace static percentages
CREATE TABLE IF NOT EXISTS volatility_thresholds (
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    horizon_days INTEGER NOT NULL,
    calculation_date DATE NOT NULL,

    -- Volatility measures
    historical_volatility NUMERIC(10,6),  -- Annualized std dev of returns
    bb_width NUMERIC(10,6),               -- Bollinger Band width (normalized)

    -- Adaptive thresholds (volatility-adjusted)
    weak_threshold NUMERIC(10,6),         -- Replaces static 2%/5%/10%
    strong_threshold NUMERIC(10,6),       -- Replaces static 5%/10%/20%

    -- Cross-sectional context
    volatility_percentile NUMERIC(5,4),   -- Where this stock ranks in market (0-1)

    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (data_id, horizon_days, calculation_date)
);

-- Convert to hypertable for efficient time-series queries
SELECT create_hypertable('volatility_thresholds', 'calculation_date', if_not_exists => TRUE);

-- Index for querying by symbol and date
CREATE INDEX IF NOT EXISTS volatility_thresholds_symbol_date_idx
    ON volatility_thresholds(data_id, calculation_date, horizon_days);

-- Index for querying latest thresholds
CREATE INDEX IF NOT EXISTS volatility_thresholds_latest_idx
    ON volatility_thresholds(data_id, horizon_days, calculation_date DESC);

\echo 'Created volatility_thresholds table'
