-- Migration 004: Performance Optimizations
--
-- Adds indexes and enables compression for improved query performance
-- and storage efficiency.

-- Index for feature_definitions queries
-- Optimizes: SELECT ... FROM feature_definitions WHERE active = TRUE AND function_name IN (...)
-- This index significantly speeds up feature definition lookups during data updates
CREATE INDEX IF NOT EXISTS idx_feature_definitions_active_function
    ON feature_definitions(active, function_name)
    WHERE active = TRUE;

-- Partial index is more efficient since we only query active=TRUE features
-- The WHERE clause makes this a partial index, reducing index size and maintenance cost

-- Note: TimescaleDB compression and VACUUM ANALYZE are runtime operations
-- and should be run separately via CLI commands or maintenance scripts
