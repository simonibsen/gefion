-- Migration: Add fundamental data columns to stocks table
--
-- Adds sector, industry, name, and updated_at columns to support:
--   - Sector-relative cross-sectional features
--   - Industry-relative cross-sectional features
--   - Company name for display purposes
--   - Staleness tracking for infrequently-updated data

-- =============================================================================
-- ADD FUNDAMENTAL DATA COLUMNS
-- =============================================================================

-- Company name (e.g., "Apple Inc.")
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS name TEXT;

-- Sector classification (e.g., "Technology", "Finance")
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sector TEXT;

-- Industry classification (e.g., "Software", "Semiconductors")
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS industry TEXT;

-- Timestamp for tracking data freshness
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;

-- =============================================================================
-- ADD INDEXES
-- =============================================================================

-- Index for sector queries (for cross-sectional features)
CREATE INDEX IF NOT EXISTS stocks_sector_idx ON stocks(sector);

-- Index for industry queries
CREATE INDEX IF NOT EXISTS stocks_industry_idx ON stocks(industry);

\echo ''
\echo '============================================='
\echo 'Migration: stocks_fundamentals Complete'
\echo '============================================='
\echo ''
\echo 'Changes:'
\echo '  - Added name column (company name)'
\echo '  - Added sector column with index'
\echo '  - Added industry column with index'
\echo '  - Added updated_at column (staleness tracking)'
\echo ''
