-- Populate standard technical indicator feature definitions
-- Run with: psql -U g2 -d g2 -f scripts/populate_indicator_features.sql

-- Ensure all indicators have the "column" field in params for dispatcher compatibility

-- Insert all feature definitions with ON CONFLICT to handle existing rows
INSERT INTO feature_definitions
(name, function_name, params, source_table, source_column, store_table, store_column, active)
VALUES
-- RSI (Relative Strength Index)
('indicator_rsi_14', 'indicator',
 '{"type": "rsi", "window": 14, "column": "rsi_14"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),

-- SMA (Simple Moving Average)
('indicator_sma_20', 'indicator',
 '{"type": "sma20", "window": 20, "column": "sma_20"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),
('indicator_sma_50', 'indicator',
 '{"type": "sma50", "window": 50, "column": "sma_50"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),
('indicator_sma_200', 'indicator',
 '{"type": "sma200", "window": 200, "column": "sma_200"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),

-- EMA (Exponential Moving Average)
('indicator_ema_12', 'indicator',
 '{"type": "ema12", "span": 12, "column": "ema_12"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),
('indicator_ema_26', 'indicator',
 '{"type": "ema26", "span": 26, "column": "ema_26"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),

-- MACD (Moving Average Convergence Divergence)
('indicator_macd', 'indicator',
 '{"type": "macd", "column": "macd"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),
('indicator_macd_signal', 'indicator',
 '{"type": "macd", "column": "macd_signal"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),
('indicator_macd_hist', 'indicator',
 '{"type": "macd", "column": "macd_hist"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),

-- Bollinger Bands
('indicator_bb_upper', 'indicator',
 '{"type": "bbands", "window": 20, "column": "bb_upper"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),
('indicator_bb_middle', 'indicator',
 '{"type": "bbands", "window": 20, "column": "bb_middle"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),
('indicator_bb_lower', 'indicator',
 '{"type": "bbands", "window": 20, "column": "bb_lower"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),

-- ADX (Average Directional Index)
('indicator_adx_14', 'indicator',
 '{"type": "adx", "window": 14, "column": "adx_14"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),

-- Stochastic Oscillator
('indicator_stoch_k', 'indicator',
 '{"type": "stoch", "window": 14, "column": "stoch_k"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),
('indicator_stoch_d', 'indicator',
 '{"type": "stoch", "window": 14, "column": "stoch_d"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true),

-- Parabolic SAR
('indicator_psar', 'indicator',
 '{"type": "psar", "step": 0.02, "max_step": 0.2, "column": "psar"}'::jsonb,
 'stock_prices', 'close', 'computed_features', 'value', true)

ON CONFLICT (name) DO UPDATE SET
  params = EXCLUDED.params,
  function_name = EXCLUDED.function_name,
  source_table = EXCLUDED.source_table,
  source_column = EXCLUDED.source_column,
  store_table = EXCLUDED.store_table,
  store_column = EXCLUDED.store_column,
  active = EXCLUDED.active;
