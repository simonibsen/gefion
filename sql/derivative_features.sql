-- Recommended Derivative Feature Definitions
--
-- This file defines 15 derivative features organized by category:
--   - RSI: 3 features (rsi_14: slope_5, slope_10, concavity_5)
--   - MACD: 3 features (macd & signal: slope_5, concavity_5, signal_slope_5)
--   - Price: 3 features (close: slope_5, slope_10, concavity_5) [requires indicator_close]
--   - ADX: 2 features (adx_14: slope_5, concavity_5)
--   - Stochastic: 2 features (stoch_k: slope_5, concavity_5)
--   - Bollinger: 2 features (bb_middle: slope_5, concavity_5)
--
-- Prerequisites:
--   All source indicators must exist in feature_definitions and be computed.
--   For price derivatives, you may need to create 'indicator_close' as a passthrough.
--
-- Usage:
--   psql -d g2 -f sql/derivative_features.sql

-- RSI Derivatives (3)
INSERT INTO feature_definitions (
    name, function_name, params,
    source_table, source_column,
    store_table, store_column, store_type,
    active
) VALUES
    (
        'derivative_rsi_14_slope_5',
        'derivative',
        '{"source_feature": "indicator_rsi_14", "type": "slope", "window": 5, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_rsi_14_slope_10',
        'derivative',
        '{"source_feature": "indicator_rsi_14", "type": "slope", "window": 10, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_rsi_14_concavity_5',
        'derivative',
        '{"source_feature": "indicator_rsi_14", "type": "concavity", "window": 5, "method": "poly2"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    )
ON CONFLICT (name) DO NOTHING;

-- MACD Derivatives (3)
INSERT INTO feature_definitions (
    name, function_name, params,
    source_table, source_column,
    store_table, store_column, store_type,
    active
) VALUES
    (
        'derivative_macd_slope_5',
        'derivative',
        '{"source_feature": "indicator_macd", "type": "slope", "window": 5, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_macd_concavity_5',
        'derivative',
        '{"source_feature": "indicator_macd", "type": "concavity", "window": 5, "method": "poly2"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_macd_signal_slope_5',
        'derivative',
        '{"source_feature": "indicator_macd_signal", "type": "slope", "window": 5, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    )
ON CONFLICT (name) DO NOTHING;

-- Price Derivatives (3)
-- NOTE: These require 'indicator_close' to exist as a passthrough feature.
-- If not already defined, create it first:
--   INSERT INTO feature_definitions (name, function_name, params, source_table, source_column, store_table, store_column, store_type, active)
--   VALUES ('indicator_close', 'passthrough', '{}'::jsonb, 'stock_prices', 'close', 'computed_features', 'value', 'double precision', true);
INSERT INTO feature_definitions (
    name, function_name, params,
    source_table, source_column,
    store_table, store_column, store_type,
    active
) VALUES
    (
        'derivative_close_slope_5',
        'derivative',
        '{"source_feature": "indicator_close", "type": "slope", "window": 5, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_close_slope_10',
        'derivative',
        '{"source_feature": "indicator_close", "type": "slope", "window": 10, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_close_concavity_5',
        'derivative',
        '{"source_feature": "indicator_close", "type": "concavity", "window": 5, "method": "poly2"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    )
ON CONFLICT (name) DO NOTHING;

-- ADX Derivatives (2)
INSERT INTO feature_definitions (
    name, function_name, params,
    source_table, source_column,
    store_table, store_column, store_type,
    active
) VALUES
    (
        'derivative_adx_14_slope_5',
        'derivative',
        '{"source_feature": "indicator_adx_14", "type": "slope", "window": 5, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_adx_14_concavity_5',
        'derivative',
        '{"source_feature": "indicator_adx_14", "type": "concavity", "window": 5, "method": "poly2"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    )
ON CONFLICT (name) DO NOTHING;

-- Stochastic Derivatives (2)
INSERT INTO feature_definitions (
    name, function_name, params,
    source_table, source_column,
    store_table, store_column, store_type,
    active
) VALUES
    (
        'derivative_stoch_k_slope_5',
        'derivative',
        '{"source_feature": "indicator_stoch_k", "type": "slope", "window": 5, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_stoch_k_concavity_5',
        'derivative',
        '{"source_feature": "indicator_stoch_k", "type": "concavity", "window": 5, "method": "poly2"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    )
ON CONFLICT (name) DO NOTHING;

-- Bollinger Band Derivatives (2)
INSERT INTO feature_definitions (
    name, function_name, params,
    source_table, source_column,
    store_table, store_column, store_type,
    active
) VALUES
    (
        'derivative_bb_middle_slope_5',
        'derivative',
        '{"source_feature": "indicator_bb_middle", "type": "slope", "window": 5, "method": "linreg"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    ),
    (
        'derivative_bb_middle_concavity_5',
        'derivative',
        '{"source_feature": "indicator_bb_middle", "type": "concavity", "window": 5, "method": "poly2"}'::jsonb,
        'computed_features',
        'value',
        'computed_features',
        'value',
        'double precision',
        true
    )
ON CONFLICT (name) DO NOTHING;

-- Summary
SELECT
    COUNT(*) as total_derivatives_defined,
    COUNT(*) FILTER (WHERE active = true) as active_derivatives
FROM feature_definitions
WHERE function_name = 'derivative';
