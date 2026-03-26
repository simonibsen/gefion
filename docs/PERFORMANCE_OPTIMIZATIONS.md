# Database Performance Optimizations

**Date:** 2025-12-22
**Branch:** perfDataUpdate

## Summary

Implemented three key database performance optimizations to improve query performance and reduce storage overhead.

## Optimizations Implemented

### 1. Added Index on feature_definitions (HIGH IMPACT)

**Problem:**
- 371 sequential scans with 0 index scans on feature_definitions table
- Queries filter by `active = TRUE` and `function_name` during feature computation

**Solution:**
- Created partial B-tree index: `idx_feature_definitions_active_function`
- Index columns: `(active, function_name)`
- Partial index with `WHERE active = TRUE` clause for efficiency

**Impact:**
- Converts sequential scans to index scans for feature definition lookups
- Reduces query time for feature computation initialization
- Minimal index size due to partial index (only indexes active features)

**Files Modified:**
- `sql/migrations/004_performance_optimizations.sql` (new migration)
- `sql/schema.sql` (added index to base schema)

### 2. Enabled TimescaleDB Compression (MEDIUM IMPACT)

**Problem:**
- Historical data consuming unnecessary storage space
- No compression enabled on hypertables

**Solution:**
- Enabled compression on both hypertables:
  - `stock_ohlcv`: segment by `data_id`, order by `date DESC`
  - `computed_features`: segment by `data_id, feature_id`, order by `date DESC`
- Set up automatic compression policies (compress chunks older than 30 days)
- Compressed 317 existing chunks immediately

**Impact:**
- Significant storage savings (typically 70-90% compression ratio for time-series data)
- Faster queries on recent data (less I/O for compressed historical data)
- Automatic compression via background jobs

**Compression Status:**
- stock_ohlcv: 317 of 319 chunks compressed (99.4%)
- computed_features: 0 of 0 chunks (no data yet)

**Files Created:**
- `sql/enable_compression.sql` (compression setup script)

### 3. Updated Query Planner Statistics (LOW IMPACT)

**Problem:**
- Potentially stale table statistics for query planner

**Solution:**
- Ran `VACUUM ANALYZE` on all tables:
  - stocks
  - stock_ohlcv
  - feature_definitions
  - computed_features
  - feature_functions

**Impact:**
- Ensures PostgreSQL query planner has accurate row counts and distribution statistics
- Enables optimal query plan selection
- Reclaims dead tuple space

## Performance Metrics

### Before Optimizations
- feature_definitions: 371 sequential scans, 0 index scans
- stock_ohlcv: 319 uncompressed chunks
- Database size: ~89 MB

### After Optimizations
- feature_definitions: New index will convert sequential scans to index scans
- stock_ohlcv: 317 chunks compressed (99.4%)
- Database size: 89 MB (minimal data, compression savings will grow with data volume)

## Index Usage Analysis

Current index scan statistics (from pg_stat_user_tables):
- **stocks**: 99,832 index scans vs 27 sequential scans (excellent ratio)
- **feature_definitions**: 0 index scans, 371 sequential scans (improved by new index)
- **stock_ohlcv**: Good index coverage with composite indexes
- **computed_features**: Properly indexed, awaiting data

## Future Optimization Opportunities

1. **Parallel Symbol Processing** (code-level)
   - Current: Sequential processing in data_update (one symbol at a time)
   - Opportunity: Parallelize feature computation across symbols
   - Location: `src/gefion/cli.py:3321`

2. **Connection Pool Tuning**
   - Monitor connection pool usage under load
   - Adjust pool size based on worker configuration

3. **Materialized Views**
   - Consider for frequently accessed aggregations
   - Example: Latest prices per stock, feature statistics

4. **Additional Partial Indexes**
   - Monitor query patterns as data volume grows
   - Add indexes for emerging slow queries

## Maintenance Notes

### Compression
- Automatic compression runs via TimescaleDB background workers
- Chunks older than 30 days are automatically compressed
- Compressed chunks are read-only (no updates/inserts)
- Use `decompress_chunk()` if you need to modify historical data

### VACUUM
- Run `VACUUM ANALYZE` periodically (weekly recommended)
- Automatic vacuum should handle routine maintenance
- Monitor bloat with pg_stat_user_tables

## Testing Recommendations

1. **Benchmark feature computation** with and without the new index:
   ```bash
   g2 data-update --exchange NASDAQ --limit 10
   ```

2. **Monitor compression ratio** as data grows:
   ```sql
   SELECT
     hypertable_name,
     pg_size_pretty(before_compression_total_bytes) as uncompressed,
     pg_size_pretty(after_compression_total_bytes) as compressed,
     ROUND(100.0 * after_compression_total_bytes / before_compression_total_bytes, 1) as compression_pct
   FROM timescaledb_information.compression_settings;
   ```

3. **Check index usage** after running data updates:
   ```sql
   SELECT schemaname, tablename, indexname, idx_scan
   FROM pg_stat_user_indexes
   WHERE schemaname = 'public'
   ORDER BY idx_scan DESC;
   ```

## Applying to Other Servers

### New Databases
```bash
g2 db-init
g2 db-tune --compress-after-days 30
```

### Existing Databases
```bash
# Run migrations (includes performance optimization index)
g2 db-migrate

# Enable compression
g2 db-tune --compress-after-days 30

# Update statistics
psql -d g2 -c "VACUUM ANALYZE;"
```

See [Database Migrations Guide](DATABASE_MIGRATIONS.md) for details.

## References

- Migration: `sql/migrations/004_performance_optimizations.sql`
- Migration system: [DATABASE_MIGRATIONS.md](DATABASE_MIGRATIONS.md)
- Compression setup: `sql/enable_compression.sql`
- Schema: `sql/schema.sql` (lines 144-149)
- TimescaleDB compression docs: https://docs.timescale.com/use-timescale/latest/compression/
