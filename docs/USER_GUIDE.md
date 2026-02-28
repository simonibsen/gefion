# G2 User Guide

## ML overview (conceptual)

g2’s ML workflow (high level) is:
1. Ingest daily price data (OHLCV) into TimescaleDB
2. Compute technical indicators/derived features and store them in the feature store
3. Build training datasets (rolling windows) and labels (forward returns for 7/30/90d, plus optional “big move” classification labels)
4. Train a multi-horizon model that predicts return *distributions* (quantiles) rather than a single number
5. Store predictions in dedicated prediction tables and validate with point-in-time backtests before turning them into tradeable signals

For details, start at `docs/archive/ml/README.md` (index), then read `docs/archive/ml/HIGHLEVEL.md` (vision) and `docs/archive/ml/ML_SYSTEM_DESIGN.md` (schemas/pipelines). For backlog and future plans, see `.specify/memory/backlog.md`.

### ML via Docker (GPU if available, CPU otherwise)

Use the `ml` Docker image for training/inference with optional GPU acceleration. The image includes a CUDA-enabled PyTorch build and automatically falls back to CPU when no GPU is available.

**How it works:**
- `docker compose run --rm ml` runs the g2 CLI inside a container
- Same commands as local g2, just prefixed with `docker compose run --rm ml`
- Container has ML dependencies pre-installed (XGBoost, LightGBM, PyTorch)
- `--gpus all` exposes host NVIDIA GPUs to the container (requires nvidia-container-toolkit)

**Examples:**

```bash
# Check device (CPU or GPU)
docker compose run --rm ml g2 ml device

# Train model (CPU)
docker compose run --rm ml g2 ml train --dataset-name mvp --dataset-version v1 --model-name test --model-version 20251217

# Train model (GPU accelerated)
docker compose run --rm --gpus all ml g2 ml train --dataset-name mvp --dataset-version v1 --model-name test --model-version 20251217

# Generate predictions
docker compose run --rm ml g2 ml predict --model-name test --model-version 20251217 --prediction-date 2024-12-14 --symbols AAPL,MSFT
```

**When to use Docker vs local:**
- **Docker**: Clean environment, GPU support without local CUDA install, reproducible builds
- **Local**: Faster iteration, easier debugging, direct file access

### ML Workflow (End-to-End)

#### 1. Build ML Dataset

`dataset-build` registers a dataset configuration in `ml_datasets` and writes a manifest JSON file. With `--export`, it also exports CSVs (`prices.csv`, `features.csv`, `labels.csv`) into the same output directory.

Example (symbols):
```bash
g2 ml dataset-build \
  --name mvp --version v1 \
  --symbols IBM,MSFT \
  --horizons 7,30,90 \
  --weak-thresholds 0.02,0.05,0.10 \
  --strong-thresholds 0.05,0.10,0.20 \
  --out-dir datasets/mvp \
  --export
```

Example (exchange + optional limit):
```bash
g2 ml dataset-build \
  --name nasdaq_mvp --version v1 \
  --exchange NASDAQ --limit 100 \
  --horizons 7,30,90 \
  --weak-thresholds 0.02,0.05,0.10 \
  --strong-thresholds 0.05,0.10,0.20 \
  --out-dir datasets/nasdaq_mvp \
  --export
```

#### 2. Train Models

Train quantile regression models for each horizon:

```bash
g2 ml train \
  --dataset-name mvp \
  --dataset-version v1 \
  --model-name mvp_model \
  --model-version 20251214 \
  --algorithm quantile_regression \
  --out-dir models
```

**Supported Algorithms:**

- `quantile_regression` - sklearn QuantileRegressor (default, fast, linear)
- `xgboost` - XGBoost quantile regression (requires `pip install 'g2[ml_extended]'`)
- `lightgbm` - LightGBM quantile regression (requires `pip install 'g2[ml_extended]'`)

The command trains 3 quantile models (q10, q50, q90) per horizon and saves artifacts to `models/mvp_model_20251214_hN/`.

#### 3. Generate Predictions

Generate predictions for symbols on a specific date:

```bash
# Predict for specific symbols
g2 ml predict \
  --model-name mvp_model \
  --model-version 20251214 \
  --prediction-date 2024-12-14 \
  --symbols IBM,MSFT,AAPL

# Predict for exchange
g2 ml predict \
  --model-name mvp_model \
  --model-version 20251214 \
  --prediction-date 2024-12-14 \
  --exchange NASDAQ \
  --limit 500
```

Fetches features from `computed_features`, generates q10/q50/q90 predictions, stores in `quantile_predictions`.

#### 4. Evaluate Performance

Evaluate model calibration on historical predictions:

```bash
g2 ml eval \
  --model-name mvp_model \
  --model-version 20251214 \
  --start-date 2024-01-01 \
  --end-date 2024-12-01
```

Computes calibration metrics (q10/q50/q90 coverage, pinball loss, IQR), stores in `model_performance`, prints evaluation report.

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
Run indicator features (local compute by default):
```bash
# Resume from last date (only compute new dates)
g2 run-features --features indicator_rsi_14,indicator_macd --exchange NASDAQ --local

# Recompute all dates (useful after fixing a feature function bug)
g2 run-features --features indicator_rsi_14,indicator_macd --exchange NASDAQ --local --refresh-existing
```
- Writes tall rows into `computed_features` (no wide table). Add `--api` to fetch from Alpha Vantage instead of local compute.
- `--max-workers` / `--writer-workers`: control fetch/write concurrency (local mode safe to increase).
- Progress shows mode, queue depth, fetched count.

### Listings / Offline

Use `--listings-file <csv|json>` to bypass the API for universe selection and work with a pre-defined list of stocks.

**When and why to use offline listings:**

1. **Testing**: Work with a small subset of symbols without hitting the API
   ```bash
   echo "AAPL,MSFT,GOOGL" > test_symbols.csv
   g2 data-update --listings-file test_symbols.csv --timeframe auto
   ```

2. **Reproducibility**: Lock to a specific universe for consistent backtesting
   ```bash
   # Save current NASDAQ 100 to file
   g2 universe-list --exchange NASDAQ --limit 100 > nasdaq100_2024.csv
   # Use same universe months later
   g2 data-update --listings-file nasdaq100_2024.csv
   ```

3. **Custom watchlist/portfolio**: Work with your own curated list of stocks
   ```bash
   # personal_portfolio.csv: AAPL,TSLA,NVDA,AMD...
   g2 run-features --listings-file personal_portfolio.csv --local
   ```

4. **Offline development**: Develop and test without internet or API access
   ```bash
   # Use saved listings file when API unavailable
   g2 data-update --listings-file cached_listings.json --timeframe compact
   ```

### Feature definitions
- Seed indicator feature metadata: `g2 seed-features` (creates `stocks`, `feature_definitions`, `computed_features`, and seeds indicator definitions).
- Register a single feature definition from JSON:
```bash
g2 register-feature --definition '{
  "name": "my_feature",
  "function_name": "my_fx",
  "params": {"window": 30},
  "source_table": "stock_ohlcv",
  "source_column": "close",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'
```
Required keys: `name`, `function_name`, `store_table`, `store_column`. Optional: `params`, `source_table`, `source_column`, `store_type`, `active`.
- Ingestion commands look up `feature_definitions` to map columns -> feature IDs before writing to `computed_features`.
- **Registering a feature automatically creates the target table/column if it doesn't exist:**
  - For `computed_features` table: Ensures the hypertable exists (standard schema)
  - For custom tables: Creates table with columns `(data_id, date, <store_column>, source)` and indexes
  - This means you can register features without manually creating database schema first
- Trim feature data (left/right):
```bash
# Trim features only (doesn't touch prices by default)
g2 trim-features --feature indicator_rsi_14,indicator_macd --before 2024-01-01

# Trim features AND underlying prices
g2 trim-features --feature indicator_rsi_14 --before 2024-01-01 --trim-prices
```
Deletes rows in `computed_features` for the named features before/after the given dates. Use `--trim-prices` to also trim `stock_ohlcv` in the same window.

**Note:** Default behavior inverted from older versions - now features are trimmed independently unless you explicitly use `--trim-prices`.

Trim prices (also trims features by default):
```bash
# Trim prices AND all derived features
g2 trim-prices --before 2023-01-01 --symbols IBM,MSFT

# Trim prices only (keep computed features)
g2 trim-prices --before 2023-01-01 --symbols IBM,MSFT --no-trim-features
```
Removes price rows before/after the given dates. By default also trims all `computed_features` derived from those prices; use `--no-trim-features` to keep features.

Drop features and data (destructive):
```bash
g2 features-drop --feature indicator_rsi_14 --drop-storage
```
Deletes rows from `computed_features` for the named features; with `--drop-storage` also drops non-`computed_features` store tables.
Data-only delete (keep definitions/schema):
```bash
g2 features-drop --feature indicator_rsi_14 --data-only
```

### Update everything (prices + computed_features)
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

### Fundamentals (sector, industry, company name)
Update company fundamentals from AlphaVantage OVERVIEW endpoint:
```bash
# Update stale fundamentals (skips stocks updated within 30 days)
g2 fundamentals-update

# Force update all stocks
g2 fundamentals-update --force

# Update specific number of stocks
g2 fundamentals-update --limit 50
```
- Stores `sector`, `industry`, `name` in the `stocks` table
- Tracks staleness with `updated_at` timestamp
- Required for cross-sectional sector/industry rankings

### Cross-sectional features (market-relative rankings)
Cross-sectional features compare stocks to their peers at the same point in time, as opposed to time-series features which compare a stock to its own history.

Compute rankings for a feature:
```bash
# Compute RSI rankings (market + sector rankings)
g2 cross-sectional-compute --feature indicator_rsi_14

# Include industry rankings
g2 cross-sectional-compute --feature indicator_rsi_14 --industries

# Market-only rankings (no sector/industry)
g2 cross-sectional-compute --feature indicator_rsi_14 --no-sectors

# JSON output
g2 cross-sectional-compute --feature indicator_rsi_14 --json
```

**Comparison groups:**
- `market` - Rank vs all stocks in the universe
- `sector:X` - Rank vs stocks in the same sector (e.g., `sector:TECHNOLOGY`)
- `industry:X` - Rank vs stocks in the same industry

**Output stored in `cross_sectional_features` table:**
| Column | Description |
|--------|-------------|
| `data_id` | Foreign key to stocks.id |
| `date` | Date the ranking was computed |
| `feature_name` | Underlying feature (e.g., `indicator_rsi_14`) |
| `comparison_group` | Peer group (`market`, `sector:X`, `industry:X`) |
| `value` | Original feature value |
| `rank` | Position among peers (1 = highest) |
| `percentile` | Position as 0-1 (1.0 = top, 0.0 = bottom) |

**Example interpretation:**
- AAPL with RSI 72, rank 3, percentile 0.85 in `sector:TECHNOLOGY`
- Means: AAPL's RSI is 3rd highest among tech stocks, in the top 15%

### AI Experimentation Framework

The experiments module enables autonomous strategy parameter optimization. AI proposes experiments, users approve, and the system runs trials using grid, random, or Bayesian search.

**Propose an experiment:**
```bash
# Basic: explore parameter space
g2 experiment propose \
  --name "momentum_optimization" \
  --strategy momentum \
  --search-space '{"lookback_days": {"type": "int", "low": 5, "high": 30}}' \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2023-01-01 --end-date 2024-01-01 \
  --objective sharpe_ratio \
  --search-method bayesian \
  --max-trials 50

# With goal: early-stop when target achieved
g2 experiment propose \
  --name "target_sharpe_2" \
  --strategy momentum \
  --search-space '{"lookback_days": {"type": "int", "low": 5, "high": 30}}' \
  --symbols AAPL,MSFT \
  --start-date 2023-01-01 --end-date 2024-01-01 \
  --goal-type achieve --goal-target 2.0 \
  --early-stop
```

**Search space format:**
```json
{
  "lookback_days": {"type": "int", "low": 5, "high": 30},
  "entry_threshold": {"type": "float", "low": 0.01, "high": 0.10},
  "exit_type": {"type": "categorical", "choices": ["trailing", "fixed"]}
}
```

**Manage experiments:**
```bash
# List pending approvals
g2 experiment list --status proposed

# Approve for execution
g2 experiment approve --id 1

# Run experiment (executes all trials)
g2 experiment run --id 1

# View results
g2 experiment results --id 1 --show-trials

# Get detailed status
g2 experiment status --id 1
```

**Chaining experiments:**
```bash
# Create child experiment that uses parent's best params
g2 experiment chain \
  --parent-id 1 \
  --name "fine_tune_thresholds" \
  --search-space '{"entry_threshold": {"type": "float", "low": 0.01, "high": 0.10}}' \
  --depends-on best_params

# List children of an experiment
g2 experiment children --parent-id 1

# Get parent info
g2 experiment parent --id 2
```

**Search methods:**
- `grid` - Exhaustive search (default)
- `random` - Random sampling
- `bayesian` - Adaptive optimization (Optuna TPE sampler)

See [.specify/specs/experiments-framework.md](../.specify/specs/experiments-framework.md) for detailed documentation.

## Tips and Behaviors
- Prices/indicators skip symbols already current (latest date = today).
- Price ingest is weekend-aware: running on Sat/Sun treats the previous weekday as “current”.
- API calls retry on transient errors/timeouts; local compute avoids rate limits.
- Batch inserts are used to reduce lock contention; if you see `max_locks_per_transaction`, lower `--writer-workers` or process smaller batches.
- Performance knobs:
  - Timescale tuning: `g2 db-tune --chunk-days 30 --compress-after-days 60` sets chunk interval and compression policies.
  - Concurrency: keep writer workers low (1–2). Heavy commands process symbols in chunks (~50) to avoid overwhelming the DB. `features-run` always starts with 1 fetch/1 writer, then ramps fetchers up batch-by-batch on success, and backs off on errors (even when `--max-workers` is set; it’s a ceiling).
  - Use `--max-workers` and `--limit` to reduce load while testing. Larger batch sizes are better than many writers.
  - If performance drops after large ingests, run `VACUUM ANALYZE stock_ohlcv computed_features`.
- Indicators: if local prices are missing for a symbol, feature runs will attempt to fetch daily adjusted prices from Alpha Vantage, store them, and then compute locally.

## Verification
- Run tests: `make test` (DB tests skipped) or `ENABLE_DB_TESTS=1 make test-db` with the DB running.
