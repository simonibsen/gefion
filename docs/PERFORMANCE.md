# Performance Optimizations

## Overview

gefion handles 5,600+ stocks with daily ingestion and indicator computation. Key optimizations ensure efficient resource usage and fast processing.

## Data Ingestion

### Bulk Symbol Filtering

**Problem**: Checking if each symbol needs an update requires N database queries.

**Solution**: Single query to filter all up-to-date symbols upfront.

```python
def filter_symbols_needing_update(conn, symbols, expected_date):
    """Return symbols needing updates (single query for all symbols)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.symbol
            FROM stocks s
            LEFT JOIN stock_ohlcv p ON s.id = p.data_id
            WHERE s.symbol = ANY(%s)
            AND (p.date IS NULL OR p.date < %s)
        """, (symbols, expected_date))
        return [row[0] for row in cur.fetchall()]
```

**Impact**:
- Before: 5,600 queries
- After: 1 query
- Skip rate: ~91% (5,100 symbols already up-to-date)

### Resource-Aware Worker Scaling

**Problem**: Static worker counts waste resources when idle, overwhelm system when busy.

**Solution**: Dynamically scale workers based on available resources.

```python
class ResourceAwareAdaptiveLimiter:
    def calculate_optimal_workers(self):
        cpu_workers = int(psutil.cpu_percent() < 70) * 2
        mem_available = psutil.virtual_memory().available
        mem_workers = mem_available // (100 * 1024 * 1024)  # 100MB per worker
        db_workers = available_db_connections - 5  # Leave buffer

        return min(cpu_workers, mem_workers, db_workers, self.max_workers)
```

**Impact**:
- Automatically scales from 2 to 16 workers based on load
- Prevents memory exhaustion and connection pool depletion
- Maintains system responsiveness under varying load

### Rate Limiting (AlphaVantage API)

**Problem**: Burst requests trigger "Burst pattern detected" errors.

**Solution**: Enforce minimum 1.0 second spacing between requests.

```python
class RateLimiter:
    def __init__(self, calls_per_minute=75):
        self.min_spacing = (60.0 / calls_per_minute) * 1.25  # 1.0 sec
        self.last_request = 0.0

    def acquire(self):
        time_since_last = now - self.last_request
        if time_since_last < self.min_spacing:
            sleep(self.min_spacing - time_since_last)
        self.last_request = now
```

**Impact**:
- Prevents API throttling errors
- Maintains ~60 calls/minute effective rate
- Works with any number of parallel workers

### Parallel Fetch + Write Pipeline

**Problem**: Serial fetch-then-write is slow.

**Solution**: Pipeline with bounded queue.

```python
# Parallel fetch workers → Queue → Parallel write workers
fetch_pool = ThreadPoolExecutor(max_workers=fetch_workers)
write_pool = ThreadPoolExecutor(max_workers=write_workers)
work_queue = Queue(maxsize=200)  # Bounded to prevent memory exhaustion
```

**Impact**:
- Fetch and write happen concurrently
- Queue depth adapts to workload
- Write workers keep database busy while fetch workers wait for API

## Feature Computation

### Local vs API Mode

**Local Mode** (recommended):
- Compute indicators from price data in database
- No API calls needed
- Faster (no network latency)
- No rate limits

**API Mode** (legacy):
- Fetch pre-computed indicators from AlphaVantage
- Subject to rate limits
- Useful for complex indicators not implemented locally

### Batch Inserts

**Problem**: Individual inserts are slow.

**Solution**: Batch upserts with 200-row chunks.

```python
INSERT INTO computed_features (data_id, date, feature_id, value)
VALUES (%s, %s, %s, %s), (%s, %s, %s, %s), ...  -- 200 rows
ON CONFLICT (data_id, date, feature_id) DO UPDATE
SET value = EXCLUDED.value
```

**Impact**:
- 10-50x faster than individual inserts
- Reduces round trips to database
- Maintains ACID properties

### Parallel Feature Processing

**Problem**: Computing multiple indicators sequentially is slow.

**Solution**: Process features in parallel per symbol.

```python
with ThreadPoolExecutor(max_workers=8) as pool:
    futures = {pool.submit(compute_feature, sym, feat): feat
               for feat in active_features}
    for future in as_completed(futures):
        result = future.result()
```

**Impact**:
- 8x speedup on multi-core systems
- CPU-bound work scales with cores
- Database writes still serialized to avoid contention

## Database Optimizations

### TimescaleDB Hypertables

**Problem**: Full table scans on millions of rows are slow.

**Solution**: Partition by time (chunk by month).

```sql
-- Automatic chunking by month
SELECT create_hypertable('stock_ohlcv', 'date');
SELECT create_hypertable('computed_features', 'date');
```

**Impact**:
- Range queries only scan relevant chunks
- Date filters (e.g., `WHERE date > '2024-01-01'`) are fast
- Efficient compression for old data

### Composite Indexes

**Problem**: Common queries scan entire tables.

**Solution**: Strategic composite indexes.

```sql
-- Feature function lookups
CREATE INDEX idx_feature_functions_enabled_status_name
ON feature_functions (enabled, status, name);

-- Feature value queries
CREATE INDEX idx_computed_features_data_feature_date
ON computed_features (data_id, feature_id, date DESC);
```

**Impact**:
- Function dispatch: 100x faster
- Feature queries: Index-only scans (no table access)

### Connection Pooling

**Problem**: Opening/closing connections per query is slow.

**Solution**: Reuse connections with autocommit.

```python
with psycopg.connect(db_url) as conn:
    conn.autocommit = True  # No transaction overhead
    # Reuse connection for multiple operations
```

**Impact**:
- Reduces connection overhead
- Autocommit avoids explicit BEGIN/COMMIT
- Suitable for read-heavy workloads

## Progress Tracking

### Rich UI with Live Updates

**Problem**: No visibility into long-running operations.

**Solution**: Progress bar with detailed stats.

```python
from rich.live import Live
from rich.table import Table

with Live(progress_table, refresh_per_second=4):
    # Update table in place during computation
    table.add_row(f"{symbol}", f"{inserted} rows", ...)
```

**Impact**:
- User visibility into progress
- Early detection of errors
- Helpful for debugging performance issues

## Benchmarks

### Full Universe Update (5,600 stocks)

**Before optimizations**:
- 5,600 API calls to check if up-to-date
- Sequential processing
- ~2 hours

**After optimizations**:
- 1 query to filter out up-to-date symbols
- 500 symbols need updates (91% skip rate)
- Parallel fetch/write with adaptive workers
- ~5 minutes

**Speedup**: 24x

### Feature Computation (20 stocks, multiple indicators)

**Before optimizations**:
- Sequential feature processing
- Individual row inserts
- ~30 seconds

**After optimizations**:
- Parallel feature processing (8 workers)
- Batch inserts (200 rows)
- ~3 seconds

**Speedup**: 10x

## Future Optimizations

See [archive/historical/](archive/historical/) for detailed optimization history and additional ideas:

- **Vectorized indicators**: NumPy/Numba for faster computation
- **Materialized views**: Pre-aggregate common queries
- **Read replicas**: Separate read/write workloads
- **Caching**: Redis for frequently-accessed data
- **Async I/O**: Non-blocking database operations

## Monitoring

Key metrics to track:

```python
# Ingestion metrics
price_fetch_workers: 8       # Current parallel fetch workers
price_writer_workers: 1      # Current parallel write workers
queue_depth: 12              # Items waiting to be written
fetch_completed: 450         # API calls completed
rate: 1.08/s                 # Effective API call rate

# Feature metrics
feature_fetch_workers: 8     # Parallel feature workers
feature_writer_workers: 2    # Parallel database writers
inserted_total: 59,915       # Rows inserted
avg_write_latency: 136ms     # Average write operation time
```

**Red flags**:
- Queue depth > 100: Fetch outpacing write (increase write workers)
- Rate > 1.5/s: Risk of API throttling (reduce fetch workers)
- Avg write latency > 500ms: Database contention (reduce batch size)

## Related Documentation

- **Architecture**: [ARCHITECTURE.md](ARCHITECTURE.md) - System design
- **Troubleshooting**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
- **Historical**: [archive/historical/](archive/historical/) - Detailed optimization history
