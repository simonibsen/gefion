# G2 User Guide

## Setup
1. Copy `.env.example` to `.env` and set:
   - `DATABASE_URL` (e.g., `postgresql://g2:g2pass@localhost:5432/g2`)
   - `ALPHAVANTAGE_API_KEY`
2. Start TimescaleDB: `docker compose up -d postgres`
3. Install: `python -m venv .venv && . .venv/bin/activate && pip install -e .`

## CLI Commands
`g2 --help` for all commands. Add `--json` for machine-readable output.

### Prices
```bash
g2 universe-ingest --exchange NASDAQ --timeframe auto --refresh-existing --max-workers 4 --writer-workers 1
```
- `--timeframe auto|compact|full`: auto skips symbols up-to-date (latest date = today) and chooses compact/full otherwise.
- `--refresh-existing`: upserts existing dates.
- `--writer-workers`: DB writers (default 1 to reduce lock contention).

Single symbol from file:
```bash
g2 prices-ingest --symbol IBM --input tests/fixtures/demo_time_series_daily_adjusted.json
```

### Indicators / features (tall store)
Run indicator features (local compute by default, resumes from last date):
```bash
g2 run-features --features indicator_rsi_14,indicator_macd --exchange NASDAQ --local --refresh-existing
```
- Writes tall rows into `computed_features` (no wide table). Add `--api` to fetch from Alpha Vantage instead of local compute.
- `--max-workers` / `--writer-workers`: control fetch/write concurrency (local mode safe to increase).
- Progress shows mode, queue depth, fetched count.

### Listings / Offline
- Use `--listings-file <csv|json>` to bypass network for universe selection.

### Feature definitions
- Seed indicator feature metadata: `g2 seed-features` (creates `stocks`, `feature_definitions`, `computed_features`, and seeds indicator definitions).
- Register a single feature definition from JSON:
```bash
g2 register-feature --definition '{
  "name": "my_feature",
  "function_name": "my_fx",
  "params": {"window": 30},
  "source_table": "stock_prices",
  "source_column": "close",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'
```
Required keys: `name`, `function_name`, `store_table`, `store_column`. Optional: `params`, `source_table`, `source_column`, `store_type`, `active`.
- Ingestion commands look up `feature_definitions` to map columns -> feature IDs before writing to `computed_features`.
- Registering a feature also ensures the target store table/column exists. Non-`computed_features` targets are created with columns `(data_id, date, <store_column>, source)`.
- Trim feature data (left/right):
```bash
g2 trim-features --feature indicator_rsi_14,indicator_macd --before 2024-01-01
g2 trim-features --feature indicator_rsi_14 --after 2024-12-31 --no-trim-prices
```
Deletes rows in `computed_features` for the named features before/after the given dates. By default also trims `stock_prices` in the same window; use `--no-trim-prices` to skip price trimming.

Trim prices only:
```bash
g2 trim-prices --before 2023-01-01 --symbols IBM,MSFT
```
Removes price rows before/after the given dates, optionally limited to symbols.

Drop features and data (destructive):
```bash
g2 features-drop --feature indicator_rsi_14 --drop-storage
```
Deletes rows from `computed_features` for the named features; with `--drop-storage` also drops non-`computed_features` store tables.
Data-only delete (keep definitions/schema):
```bash
g2 features-drop --feature indicator_rsi_14 --data-only
```

### Update everything (prices + indicators)
```bash
g2 data-update --exchange NASDAQ --timeframe auto --refresh-existing --local
```
- Fetches listings once, ingests prices, then ingests indicators into `computed_features`.
- Honors `--local/--api` for indicators and `--refresh-existing` to upsert.
- Processes symbols in small chunks to reduce DB pressure; keep writer workers low (default 1).

### Features management
- List: `g2 features-list --json`
- Show one: `g2 features-show --feature indicator_rsi_14 --json`
- Run features (indicators): `g2 features-run --features indicator_rsi_14,indicator_macd --exchange NASDAQ --local --refresh-existing`

## Tips and Behaviors
- Prices/indicators skip symbols already current (latest date = today).
- Price ingest is weekend-aware: running on Sat/Sun treats the previous weekday as “current”.
- API calls retry on transient errors/timeouts; local compute avoids rate limits.
- Batch inserts are used to reduce lock contention; if you see `max_locks_per_transaction`, lower `--writer-workers` or process smaller batches.
- Performance knobs:
  - Timescale tuning: `g2 db-tune --chunk-days 30 --compress-after-days 60` sets chunk interval and compression policies.
  - Concurrency: keep writer workers low (1–2). Heavy commands process symbols in chunks (~50) to avoid overwhelming the DB. `features-run` always starts with 1 fetch/1 writer, then ramps fetchers up batch-by-batch on success, and backs off on errors (even when `--max-workers` is set; it’s a ceiling).
  - Use `--max-workers` and `--limit` to reduce load while testing. Larger batch sizes are better than many writers.
  - If performance drops after large ingests, run `VACUUM ANALYZE stock_prices computed_features`.
- Indicators: if local prices are missing for a symbol, feature runs will attempt to fetch daily adjusted prices from Alpha Vantage, store them, and then compute locally.

## Verification
- Run tests: `make test` (DB tests skipped) or `ENABLE_DB_TESTS=1 make test-db` with the DB running.
