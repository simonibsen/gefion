-- Migration: Add plural source_tables and source_columns to feature_definitions
-- Date: 2025-12-21
-- Purpose: Support features requiring multiple source columns (e.g., ADX needs high/low/close)
--
-- This migration adds JSONB columns to store arrays of table/column names while
-- maintaining backward compatibility with singular source_table/source_column columns.

BEGIN;

-- Add plural columns for source tables and columns
ALTER TABLE feature_definitions
    ADD COLUMN IF NOT EXISTS source_tables JSONB,
    ADD COLUMN IF NOT EXISTS source_columns JSONB;

-- Create index for querying by source tables (useful for finding all features from a table)
CREATE INDEX IF NOT EXISTS idx_feature_definitions_source_tables
    ON feature_definitions USING GIN (source_tables);

-- Create index for querying by source columns (useful for finding all features using a column)
CREATE INDEX IF NOT EXISTS idx_feature_definitions_source_columns
    ON feature_definitions USING GIN (source_columns);

COMMIT;

-- Usage examples:
--
-- Legacy format (still supported):
-- INSERT INTO feature_definitions (name, function_name, params, source_table, source_column, ...)
-- VALUES ('indicator_rsi_14', 'compute_features', '{"indicator": "rsi", "period": 14}', 'stock_ohlcv', 'close', ...);
--
-- New plural format (single column):
-- INSERT INTO feature_definitions (name, function_name, params, source_tables, source_columns, ...)
-- VALUES ('indicator_rsi_14', 'compute_features', '{"indicator": "rsi", "period": 14}', '["stock_ohlcv"]', '["close"]', ...);
--
-- New plural format (multiple columns):
-- INSERT INTO feature_definitions (name, function_name, params, source_tables, source_columns, ...)
-- VALUES ('indicator_adx_14', 'compute_features', '{"indicator": "adx", "period": 14}', '["stock_ohlcv"]', '["high", "low", "close"]', ...);
