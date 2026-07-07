# Gefion User Guide

## ML overview (conceptual)

Gefion’s ML workflow (high level) is:
1. Ingest daily price data (OHLCV) into TimescaleDB
2. Compute technical indicators/derived features and store them in the feature store
3. Build training datasets (rolling windows) and labels (forward returns for 7/30/90d, plus optional “big move” classification labels)
4. Train a multi-horizon model that predicts return *distributions* (quantiles) rather than a single number
5. Store predictions in dedicated prediction tables and validate with point-in-time backtests before turning them into tradeable signals

For details, start at `docs/archive/ml/README.md` (index), then read `docs/archive/ml/HIGHLEVEL.md` (vision) and `docs/archive/ml/ML_SYSTEM_DESIGN.md` (schemas/pipelines). For backlog and future plans, see `.specify/memory/backlog.md`.

### ML via Docker (GPU if available, CPU otherwise)

Use the `ml` Docker image for training/inference with optional GPU acceleration. The image includes a CUDA-enabled PyTorch build and automatically falls back to CPU when no GPU is available.

**How it works:**
- `docker compose run --rm ml` runs the Gefion CLI inside a container
- Same commands as local Gefion, just prefixed with `docker compose run --rm ml`
- Container has ML dependencies pre-installed (XGBoost, LightGBM, PyTorch)
- `--gpus all` exposes host NVIDIA GPUs to the container (requires nvidia-container-toolkit)

**Examples:**

```bash
# Check device (CPU or GPU)
docker compose run --rm ml Gefion ml device

# Train model (CPU)
docker compose run --rm ml Gefion ml train --dataset-name mvp --dataset-version v1 --model-name test --model-version 20251217

# Train model (GPU accelerated)
docker compose run --rm --gpus all ml Gefion ml train --dataset-name mvp --dataset-version v1 --model-name test --model-version 20251217

# Generate predictions
docker compose run --rm ml Gefion ml predict --model-name test --model-version 20251217 --prediction-date 2024-12-14 --symbols AAPL,MSFT
```

**When to use Docker vs local:**
- **Docker**: Clean environment, GPU support without local CUDA install, reproducible builds
- **Local**: Faster iteration, easier debugging, direct file access

### ML Workflow (End-to-End)

#### 1. Build ML Dataset

`dataset-build` registers a dataset configuration in `ml_datasets` and writes a manifest JSON file. With `--export`, it also exports CSVs (`prices.csv`, `features.csv`, `labels.csv`) into the same output directory.

Example (symbols):
```bash
gefion ml dataset-build \
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
gefion ml dataset-build \
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
gefion ml train \
  --dataset-name mvp \
  --dataset-version v1 \
  --model-name mvp_model \
  --model-version 20251214 \
  --algorithm quantile_regression \
  --out-dir models
```

**Supported Algorithms:**

- `quantile_regression` - sklearn QuantileRegressor (default, fast, linear)
- `xgboost` - XGBoost quantile regression (requires `pip install gefion[ml_extended]'`)
- `lightgbm` - LightGBM quantile regression (requires `pip install gefion[ml_extended]'`)

The command trains 3 quantile models (q10, q50, q90) per horizon and saves artifacts to `models/mvp_model_20251214_hN/`.

#### 3. Generate Predictions

Generate predictions for symbols on a specific date:

```bash
# Predict for specific symbols
gefion ml predict \
  --model-name mvp_model \
  --model-version 20251214 \
  --prediction-date 2024-12-14 \
  --symbols IBM,MSFT,AAPL

# Predict for exchange
gefion ml predict \
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
gefion ml eval \
  --model-name mvp_model \
  --model-version 20251214 \
  --start-date 2024-01-01 \
  --end-date 2024-12-01
```

Computes calibration metrics (q10/q50/q90 coverage, pinball loss, IQR), stores in `model_performance`, prints evaluation report.

#### 5. Calibrate

Apply conformal calibration to correct quantile coverage on a holdout period:

```bash
gefion ml calibrate \
  --model-name mvp_model \
  --model-version 20251214 \
  --start-date 2024-06-01 \
  --end-date 2024-12-01
```

Computes additive shift corrections so predicted quantiles achieve nominal coverage (10%, 50%, 90%). Saves `calibration.json` alongside model artifacts. Future predictions automatically apply the shifts.

## Setup
1. Copy `.env.example` to `.env` and set:
   - `DATABASE_URL` (e.g., `postgresql://gefion:gefionpass@localhost:5432/gefion`)
   - `ALPHAVANTAGE_API_KEY`
2. Start TimescaleDB: `docker compose up -d postgres`
3. Install: `python -m venv .venv && . .venv/bin/activate && pip install -e .`

## CLI Commands
`gefion --help` for all commands. Add `--json` for machine-readable output.

### Prices
```bash
gefion universe-ingest --exchange NASDAQ --timeframe auto --refresh-existing --max-workers 4 --writer-workers 1
```
- `--timeframe auto|compact|full`: auto skips symbols up-to-date (latest date = today) and chooses compact/full otherwise.
- `--refresh-existing`: upserts existing dates.
- `--writer-workers`: DB writers (default 1 to reduce lock contention).

Single symbol from file:
```bash
gefion prices-ingest --symbol IBM --input tests/fixtures/demo_time_series_daily_adjusted.json
```

### Indicators / features (tall store)
Run indicator features (local compute by default):
```bash
# Resume from last date (only compute new dates)
gefion feat-compute --features indicator_rsi_14,indicator_macd --exchange NASDAQ

# Recompute all dates (useful after fixing a feature function bug)
gefion feat-compute --features indicator_rsi_14,indicator_macd --exchange NASDAQ --refresh-existing
```
- Writes tall rows into `computed_features` (no wide table).
- `--max-workers` / `--writer-workers`: control fetch/write concurrency.
- Progress shows mode, queue depth, fetched count.

### Listings / Offline

Use `--listings-file <csv|json>` to bypass the API for universe selection and work with a pre-defined list of stocks.

**When and why to use offline listings:**

1. **Testing**: Work with a small subset of symbols without hitting the API
   ```bash
   echo "AAPL,MSFT,GOOGL" > test_symbols.csv
   gefion data-update --listings-file test_symbols.csv --timeframe auto
   ```

2. **Reproducibility**: Lock to a specific universe for consistent backtesting
   ```bash
   # Save current NASDAQ 100 to file
   gefion universe-ingest --exchange NASDAQ --limit 100   # then export via query_database / psql
   # Use same universe months later
   gefion data-update --listings-file nasdaq100_2024.csv
   ```

3. **Custom watchlist/portfolio**: Work with your own curated list of stocks
   ```bash
   # personal_portfolio.csv: AAPL,TSLA,NVDA,AMD...
   gefion feat-compute --listings-file personal_portfolio.csv
   ```

4. **Offline development**: Develop and test without internet or API access
   ```bash
   # Use saved listings file when API unavailable
   gefion data-update --listings-file cached_listings.json --timeframe compact
   ```

### Feature definitions
- Seed indicator feature metadata: `gefion feat-fx-import --dir feature-functions` (creates `stocks`, `feature_definitions`, `computed_features`, and seeds indicator definitions).
- Register a single feature definition from JSON:
```bash
gefion feat-def-import --definition '{
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
gefion feat-trim --feature indicator_rsi_14,indicator_macd --before 2024-01-01

# Trim features AND underlying prices
gefion feat-trim --feature indicator_rsi_14 --before 2024-01-01 --trim-prices
```
Deletes rows in `computed_features` for the named features before/after the given dates. Use `--trim-prices` to also trim `stock_ohlcv` in the same window.

**Note:** Default behavior inverted from older versions - now features are trimmed independently unless you explicitly use `--trim-prices`.

Trim prices (also trims features by default):
```bash
# Trim prices AND all derived features
gefion prices-trim --before 2023-01-01 --symbols IBM,MSFT

# Trim prices only (keep computed features)
gefion prices-trim --before 2023-01-01 --symbols IBM,MSFT --no-trim-features
```
Removes price rows before/after the given dates. By default also trims all `computed_features` derived from those prices; use `--no-trim-features` to keep features.

Drop features and data (destructive):
```bash
gefion feat-drop --feature indicator_rsi_14 --drop-storage
```
Deletes rows from `computed_features` for the named features; with `--drop-storage` also drops non-`computed_features` store tables.
Data-only delete (keep definitions/schema):
```bash
gefion feat-drop --feature indicator_rsi_14 --data-only
```

### Update everything (prices + computed_features)
```bash
gefion data-update --exchange NASDAQ --timeframe auto --refresh-existing
```
- Fetches listings once, ingests prices, then ingests indicators into `computed_features`.
- Honors `--refresh-existing` to upsert.
- Processes symbols in small chunks to reduce DB pressure; keep writer workers low (default 1).

### Features management
- List: `gefion feat-def-list --json`
- Show one: `gefion feat-def-show --feature indicator_rsi_14 --json`
- Run features (indicators): `gefion feat-compute --features indicator_rsi_14,indicator_macd --exchange NASDAQ --refresh-existing`

### Fundamentals (sector, industry, company name)
Update company fundamentals from AlphaVantage OVERVIEW endpoint:
```bash
# Update stale fundamentals (skips stocks updated within 30 days)
gefion fundamentals-update

# Force update all stocks
gefion fundamentals-update --force

# Update specific number of stocks
gefion fundamentals-update --limit 50
```
- Stores `sector`, `industry`, `name` in the `stocks` table
- Tracks staleness with `updated_at` timestamp
- Required for cross-sectional sector/industry rankings

### Cross-sectional features (market-relative rankings)
Cross-sectional features compare stocks to their peers at the same point in time, as opposed to time-series features which compare a stock to its own history.

Compute rankings for a feature:
```bash
# Compute RSI rankings (market + sector rankings)
gefion cross-sectional-compute --feature indicator_rsi_14

# Include industry rankings
gefion cross-sectional-compute --feature indicator_rsi_14 --industries

# Market-only rankings (no sector/industry)
gefion cross-sectional-compute --feature indicator_rsi_14 --no-sectors

# JSON output
gefion cross-sectional-compute --feature indicator_rsi_14 --json
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

The experiments module enables autonomous experimentation across the full
pipeline: feature engineering (AI-generated code), hyperparameters, model
comparison, label engineering, feature selection, and strategy parameters.
Cycle experiments are judged by a statistical gate — trials train on
pre-holdout data only, each experiment earns a one-sided holdout p-value,
and Benjamini-Hochberg FDR decides survival (fail-closed: no p-value, no
promotion). Survivors get a 7-day probation window and can be taken to
production with one command.

**Propose an experiment:**
```bash
# Basic: explore parameter space
gefion experiment propose \
  --name "momentum_optimization" \
  --strategy momentum \
  --search-space '{"lookback_days": {"type": "int", "low": 5, "high": 30}}' \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2023-01-01 --end-date 2024-01-01 \
  --objective sharpe_ratio \
  --search-method bayesian \
  --max-trials 50

# With goal: early-stop when target achieved
gefion experiment propose \
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
gefion experiment pending
gefion experiment list --status proposed

# Approve or reject
gefion experiment approve --id 1
gefion experiment reject --id 1

# Run experiment (executes all trials)
gefion experiment run --id 1

# View results
gefion experiment results --id 1 --trials

# Get detailed status
gefion experiment status --id 1
```

**Autonomous cycles and the production flow:**
```bash
# Start a cycle (reserves the most recent weeks as holdout)
gefion experiment cycle-start --name exploration-1 --max-experiments 5

# Run it end to end: discover → propose → run → holdout-evaluate → FDR → promote
gefion experiment cycle-run 1

# Inspect cycles
gefion experiment cycle-list
gefion experiment cycle-status 1

# Take a promoted winner to production:
# dataset rebuild → retrain → predict → ml_signal backtest → probation window
gefion experiment apply --id 42

# Probation: re-measures applied winners against realized outcomes.
# Runs automatically at the end of every `gefion data-update`.
gefion experiment probation-check

# Manual demotion (reason is recorded on the experiment)
gefion experiment demote --id 42 --reason "degraded after regime change"

# Charts
gefion chart experiment-trials 42     # trial scatter (+ heatmap when 2 params vary)
gefion chart experiment-fdr 1         # cycle p-values vs FDR threshold
```

In the UI (Experiments page): lifecycle badges (🟡 on probation / 🟢 promoted /
🔴 demoted), holdout p-values in cycle details, inline charts, an Apply to
Production button on loaded results, and demote/probation-check actions.

**Chaining experiments:**
```bash
# Create child experiment that uses parent's best params
gefion experiment chain \
  --parent-id 1 \
  --name "fine_tune_thresholds" \
  --search-space '{"entry_threshold": {"type": "float", "low": 0.01, "high": 0.10}}' \
  --depends-on best_params

# List children of an experiment
gefion experiment children --parent-id 1

# Get parent info
gefion experiment parent --id 2
```

**Search methods:**
- `grid` - Exhaustive search (default)
- `random` - Random sampling
- `bayesian` - Adaptive optimization (Optuna TPE sampler)

See [specs/004-autonomous-experiments/](../specs/004-autonomous-experiments/) for the full specification and [specs/004-autonomous-experiments/quickstart.md](../specs/004-autonomous-experiments/quickstart.md) for a hands-on walkthrough.

## Tips and Behaviors
- Prices/indicators skip symbols already current (latest date = today).
- Price ingest is weekend-aware: running on Sat/Sun treats the previous weekday as “current”.
- API calls retry on transient errors/timeouts; local compute avoids rate limits.
- Batch inserts are used to reduce lock contention; if you see `max_locks_per_transaction`, lower `--writer-workers` or process smaller batches.
- Performance knobs:
  - Timescale tuning: `gefion db-tune --chunk-days 30 --compress-after-days 60` sets chunk interval and compression policies.
  - Concurrency: keep writer workers low (1–2). Heavy commands process symbols in chunks (~50) to avoid overwhelming the DB. `feat-compute` always starts with 1 fetch/1 writer, then ramps fetchers up batch-by-batch on success, and backs off on errors (even when `--max-workers` is set; it’s a ceiling).
  - Use `--max-workers` and `--limit` to reduce load while testing. Larger batch sizes are better than many writers.
  - If performance drops after large ingests, run `VACUUM ANALYZE stock_ohlcv computed_features`.
- Indicators: if local prices are missing for a symbol, feature runs will attempt to fetch daily adjusted prices from Alpha Vantage, store them, and then compute locally.

## Web UI

Launch the Streamlit UI for interactive exploration:

```bash
gefion ui              # default port 8501
gefion ui --port 8502  # custom port
```

### AI Actions

The **AI Actions** page (second item in the sidebar) is the primary interaction point. It supports:

- **Natural language prompts**: Type a question like "Which stocks had the biggest moves?" — routed to `claude -p` with Gefion MCP tools.
- **CLI commands**: Type `gefion health` or any Gefion command — executes directly and streams output.
- **MCP tool shortcuts**: Type a tool name like `data_update` — mapped to the corresponding CLI command.

**Conversation history** persists across page refreshes and server restarts. Previous exchanges are displayed as a chat thread. Click "Clear History" to start fresh. History is stored in `~/.gefion/ai_history.jsonl` (capped at 100 exchanges).

**Error indicator**: If errors occur during the session, an expandable "Errors (N)" badge appears at the top of the page showing all session errors with timestamps. Errors are also logged to `~/.gefion/ui_errors.jsonl` for diagnosis from Claude Code.

### Other Views

- **Dashboard**: System overview and quick stats
- **Data Management**: Price data ingestion and updates
- **Features**: Feature definitions, functions, coverage, and computation
- **ML Pipeline**: Dataset building, training, prediction, evaluation
- **Backtesting**: Strategy backtests and comparisons
- **Experiments**: Propose, approve, and run experiments
- **Charts**: Price, prediction, and feature charts

### Tips

- The UI is best launched from a regular terminal (not from within Claude Code) — AI prompts via `claude -p` work correctly this way.
- If launched from Claude Code, CLI commands still work but AI prompts may fail due to session nesting.
- Errors from the UI session can be read anytime via `cat ~/.gefion/ui_errors.jsonl`.

## Verification
- Run tests: `make test` (DB tests skipped) or `ENABLE_DB_TESTS=1 make test-db` with the DB running.

## Regimes (spec 005)

Define, compute, and inspect market/sector/asset regimes for conditional evaluation. See
[REGIMES.md](REGIMES.md) for concepts.

- `gefion regime define --name <slug> --scope market|sector|industry|asset --expression <ast.json> --bucketing <buckets.json> [--min-dwell N]` — define and store a regime.
- `gefion regime list [--scope S] [--status active|archived]` — list definitions.
- `gefion regime show <name>` — show a definition (AST, bucketing, persistence, metadata).
- `gefion regime compute <name> [--dataset V] [--window N]` — compute causal labels from referenced features.
- `gefion regime labels <name>` — summarize computed labels (bucket coverage).
- `gefion regime archive <name>` — archive a definition.
- `gefion regime export <dir>` / `gefion regime import <dir>` — JSON backup/restore of definitions.
- `gefion regime interaction --signal <feat> --by <feat> [--horizon-days N]` — continuous-interaction test (does a signal's edge scale with a conditioning variable?).
- `gefion experiment run --id N --by-regime <name>` — conditional holdout verdicts: a p-value per regime bucket, all entered into one flat Benjamini-Hochberg family (fail-closed on low-power buckets).
- `gefion chart regime <name> --symbol <SYM> [--start-date D] [--end-date D]` — chart a symbol's price with regime-episode bands overlaid.

All commands accept `--json` and `--db-url`.

### Agentic regime discovery (spec 006)

The agent proposes and tests candidate regimes under structural guardrails: nested
segregation (discovery never sees the outer holdout), a pre-registered bounded search
space, one flat FDR family that counts every test including the losers, and auditable
ledgers. See [REGIMES.md](REGIMES.md) § Agentic discovery for the threat model.

- `gefion regime discover start --name <slug> --atoms <atoms.json> [--depth K] [--budget N] [--tier interaction|grammar|expressive]... [--signal-source features] [--grading-scheme walk_forward] [--universe-filter <chain>|passthrough] [--fresh-holdout START:END] [--signal <feat>]... [--horizon-days N] [--holdout-weeks N] [--seed N] [--dataset V]` — pre-register and run a bounded discovery. Expect mostly rejections; that is correct behavior.
- `gefion regime discover list [--status S]` — list runs (status, family size, dataset).
- `gefion regime discover show <run>` — pre-registration, segregation boundaries, family size, status.

Honest refusals at start: expressive tier without a declared `--fresh-holdout` reserve;
an unfiltered universe without explicit `passthrough`; a run whose segregation cannot be
proven is recorded with status `invalid` and produces no verdicts.
