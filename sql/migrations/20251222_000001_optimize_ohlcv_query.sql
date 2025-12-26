-- Migration 006: Optimize OHLCV data fetch for feature computation
--
-- Description: Adds optimized index for the most common query pattern in feature computation.
-- The existing index uses DESC order, but feature computation needs ASC order.

-- Create index matching the query pattern: WHERE data_id = ? ORDER BY date ASC
-- This eliminates the need for PostgreSQL to reverse-scan or sort
CREATE INDEX IF NOT EXISTS idx_stock_ohlcv_data_id_date_asc
    ON stock_ohlcv (data_id, date ASC);

-- Note: We keep the existing DESC index as it may be used by other queries
-- PostgreSQL will choose the best index based on the query
