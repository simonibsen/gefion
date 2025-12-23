-- Enable TimescaleDB compression for hypertables
--
-- This enables compression and sets up automatic compression policies
-- for chunks older than 30 days to save storage space and improve query performance.

-- Enable compression on stock_ohlcv hypertable
-- Segment by data_id (stock) and order by date for optimal compression
ALTER TABLE stock_ohlcv SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'data_id',
    timescaledb.compress_orderby = 'date DESC'
);

-- Enable compression on computed_features hypertable
-- Segment by data_id and feature_id, order by date
ALTER TABLE computed_features SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'data_id,feature_id',
    timescaledb.compress_orderby = 'date DESC'
);

-- Add compression policy: compress chunks older than 30 days
-- This runs automatically in the background
SELECT add_compression_policy('stock_ohlcv', INTERVAL '30 days');
SELECT add_compression_policy('computed_features', INTERVAL '30 days');

-- Manually compress existing old chunks (older than 30 days)
-- This is a one-time operation; future chunks will be compressed automatically
SELECT compress_chunk(i, if_not_compressed => true)
FROM show_chunks('stock_ohlcv', older_than => INTERVAL '30 days') i;

SELECT compress_chunk(i, if_not_compressed => true)
FROM show_chunks('computed_features', older_than => INTERVAL '30 days') i;

\echo ''
\echo 'Compression enabled successfully!'
\echo 'Policies will automatically compress chunks older than 30 days.'
\echo ''
