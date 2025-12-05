# Troubleshooting Guide

## TimescaleDB Unique Index Error

### Error Message
```
{"status": "error", "message": "Ingest failed: cannot create a unique index without the column \"date\" (used in partitioning)"}
```

### Cause
This error occurs when the `stock_ohlcv` table was created before TimescaleDB was properly enabled, or if the table schema conflicts with TimescaleDB's hypertable requirements.

TimescaleDB requires that all UNIQUE constraints on hypertables must include the partitioning column (in this case, `date`).

### Quick Fix

Run the migration script to automatically fix the table:

```bash
python scripts/fix_hypertable.py
```

This script will:
1. Check if the table exists
2. Drop the old table if it's not a proper hypertable
3. Recreate it with the correct TimescaleDB configuration
4. Verify the fix

### Manual Fix

If you prefer to fix it manually:

```bash
# Connect to your database
psql -U g2 -d g2

# Drop the problematic table
DROP TABLE IF EXISTS stock_ohlcv CASCADE;

# Exit psql
\q

# Run g2 command again - it will recreate the table properly
g2 universe-ingest --exchange nasdaq --json
```

### Prevention

The issue has been fixed in the codebase. The `create_stock_ohlcv_table()` function now:
- Automatically detects if a table exists but isn't a hypertable
- Drops and recreates it properly
- Ensures TimescaleDB compatibility

### After Running the Fix

You can verify the fix worked by checking:

```bash
psql -U g2 -d g2 -c "SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name = 'stock_ohlcv';"
```

You should see one row indicating the table is now a hypertable.

---

## Performance Issues

If you're experiencing slow performance, check:

1. **Connection Pooling**: Make sure you're using the connection pool:
   ```python
   from g2.db import pool
   pool.init_pool(db_url, min_size=2, max_size=10)
   ```

2. **Worker Count**: Let auto-sizing work, or manually adjust:
   ```bash
   g2 universe-ingest --fetch-workers 4 --writer-workers 2
   ```

3. **Database Indexes**: Ensure composite indexes exist:
   ```sql
   \d stock_ohlcv
   -- Should show: stock_ohlcv_data_id_date_idx
   ```

---

## Test Failures

If tests are failing:

```bash
# For DB tests, ensure PostgreSQL is running
docker compose ps postgres

# Run tests with DB enabled
ENABLE_DB_TESTS=1 pytest tests/ -v

# Run only non-DB tests
pytest tests/ -k "not db" -v
```

---

## Need More Help?

Check the documentation:
- [OPTIMIZATION_COMPLETE.md](OPTIMIZATION_COMPLETE.md) - Performance optimizations
- [PERFORMANCE_QUICK_REF.md](PERFORMANCE_QUICK_REF.md) - Quick reference
- [docs/PERFORMANCE_README.md](docs/PERFORMANCE_README.md) - Usage guide
