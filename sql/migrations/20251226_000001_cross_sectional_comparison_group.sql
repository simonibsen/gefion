-- Migration: Add comparison_group column to cross_sectional_features
--
-- Enables storing features relative to different comparison groups:
--   - 'market' (default): compared to all stocks
--   - 'sector:Technology': compared to tech sector
--   - 'industry:Software': compared to software industry
--
-- The same feature can have different ranks/percentiles depending on
-- the comparison group, allowing for market-relative vs sector-relative analysis.

-- =============================================================================
-- ADD COMPARISON_GROUP COLUMN
-- =============================================================================

-- Add the column with default value
ALTER TABLE cross_sectional_features
ADD COLUMN IF NOT EXISTS comparison_group TEXT NOT NULL DEFAULT 'market';

-- =============================================================================
-- UPDATE PRIMARY KEY
-- =============================================================================

-- Drop the existing primary key constraint
-- Note: TimescaleDB hypertables have a unique constraint, not a traditional PK
ALTER TABLE cross_sectional_features
DROP CONSTRAINT IF EXISTS cross_sectional_features_pkey;

-- Create new primary key including comparison_group
ALTER TABLE cross_sectional_features
ADD CONSTRAINT cross_sectional_features_pkey
PRIMARY KEY (data_id, date, feature_name, comparison_group);

-- =============================================================================
-- UPDATE INDEXES
-- =============================================================================

-- Drop old index if exists and recreate with comparison_group
DROP INDEX IF EXISTS cross_sectional_features_feature_date_rank_idx;
CREATE INDEX cross_sectional_features_feature_date_group_rank_idx
    ON cross_sectional_features(feature_name, date, comparison_group, rank);

-- Index for querying by comparison group
CREATE INDEX IF NOT EXISTS cross_sectional_features_comparison_group_idx
    ON cross_sectional_features(comparison_group, date);

\echo ''
\echo '========================================='
\echo 'Migration 007: comparison_group Complete'
\echo '========================================='
\echo ''
\echo 'Changes:'
\echo '  - Added comparison_group column (default: market)'
\echo '  - Updated primary key to include comparison_group'
\echo '  - Added index for comparison_group queries'
\echo ''
