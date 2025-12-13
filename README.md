# g2 (working title)

New Python/Postgres project inspired by `folly` (technical analysis + calc_store pattern) and `gefjon` (modern ingestion/ML pipeline). The goal is to grow this incrementally with strict TDD and a running dev journal so work can be paused/resumed easily.

## Architecture

```mermaid
graph TB
    subgraph "Data Sources"
        AV[AlphaVantage API]
    end

    subgraph "CLI Commands"
        DataUpdate[g2 data-update]
        FeaturesCompute[g2 features-compute]
        FeaturesRegister[g2 features-register]
    end

    subgraph "Application Layer"
        Ingestion[Ingestion Pipeline]
        Dispatcher[Feature Dispatcher]
        Registry[Compute Function Registry]
    end

    subgraph "Compute Functions"
        IndicatorFn[compute_indicators]
        DerivativeFn[compute_derivatives]
        CustomFn[custom functions...]
    end

    subgraph "Database - TimescaleDB"
        direction TB
        Stocks[(stocks)]
        Prices[(stock_ohlcv<br/>hypertable)]
        FeatureDefs[(feature_definitions<br/>metadata)]
        ComputedFeatures[(computed_features<br/>hypertable)]

        Stocks -->|1:N| Prices
        Stocks -->|1:N| ComputedFeatures
        FeatureDefs -->|1:N| ComputedFeatures
        Prices -->|source| ComputedFeatures
        ComputedFeatures -->|source| ComputedFeatures
    end

    %% Data ingestion flow
    AV -->|fetch prices| DataUpdate
    DataUpdate -->|batch insert| Ingestion
    Ingestion -->|insert| Stocks
    Ingestion -->|insert| Prices

    %% Feature registration flow
    FeaturesRegister -->|define| FeatureDefs

    %% Feature computation flow
    FeaturesCompute -->|dispatch| Dispatcher
    Dispatcher -->|read metadata| FeatureDefs
    Dispatcher -->|route by function_name| Registry
    Registry -->|indicator| IndicatorFn
    Registry -->|derivative| DerivativeFn
    Registry -->|custom| CustomFn

    IndicatorFn -->|fetch| Prices
    DerivativeFn -->|fetch| ComputedFeatures

    IndicatorFn -->|insert| ComputedFeatures
    DerivativeFn -->|insert| ComputedFeatures
    CustomFn -->|insert| ComputedFeatures

    style Dispatcher fill:#e1f5ff
    style Registry fill:#e1f5ff
    style FeatureDefs fill:#fff4e1
    style ComputedFeatures fill:#e8f5e9
```

### Key Concepts

- **Metadata-Driven**: Features are defined as data in `feature_definitions`, not code
- **Registry Pattern**: Compute functions register by name (e.g., "indicator", "derivative")
- **Generic Dispatcher**: Routes computation based on `function_name` in feature definitions
- **Hypertables**: TimescaleDB optimizes time-series queries on `stock_ohlcv` and `computed_features`
- **Pure Functions**: Compute functions are side-effect-free, dispatcher handles DB I/O
- **DB-First Functions**: Custom feature functions stored in database with git backup for version control

## Feature Functions (DB-First Architecture)

### Overview

g2 supports **database-stored feature functions** that can be written in multiple languages (Python, SQL, etc.) and executed in a sandboxed environment. The database is the source of truth, with periodic exports to git for version control and code review.

### Architecture Layers

1. **Core Compute Engines** (in code): `indicator`, `derivative`
   - Built-in functions that need full system access
   - Registered at runtime via `register_compute_function()`
   - Cannot be overridden by DB functions

2. **Custom Feature Functions** (in database): User-defined logic
   - Stored in `feature_functions` table as code text
   - Loaded and executed in sandboxed environment
   - Can override code registry or add new functions
   - Support multiple languages: `python`, `python_expr`, `sql` (extensible)

### DB-First Workflow

```bash
# 1. Create/modify feature functions in the database
#    (via g2 CLI, web UI, or direct SQL)

# 2. Export to git for version control
g2 features-export --dir feature-functions

# 3. Review and commit changes
git add feature-functions/
git commit -m "Update price_change_pct feature function"

# 4. Deploy to other environments
g2 features-import --dir feature-functions
```

### Example: Creating a Custom Feature Function

Create a JSON file in `feature-functions/`:

```json
{
  "name": "price_change_pct",
  "version": "1.0",
  "language": "python",
  "description": "Calculate percentage price change",
  "status": "active",
  "enabled": true,
  "function_body": "import pandas as pd\n\ndef compute(rows, specs):\n    df = pd.DataFrame(rows)\n    df['price_change_pct'] = df['close'].pct_change() * 100\n    return df.to_dict('records')\n"
}
```

Import to database:

```bash
g2 features-import --dir feature-functions
```

### Security & Sandboxing

DB-stored functions execute in a restricted environment:

- **Allowed**: pandas, numpy, scipy, sklearn, talib, math, statistics, datetime
- **Blocked**: file I/O, eval(), exec(), compile(), arbitrary imports
- **Safe**: Each function runs in isolated namespace

### Priority Order

When loading functions, the dispatcher checks:

1. **Function cache** (performance optimization)
2. **Database** (`feature_functions` table) — **highest priority**
3. **Code registry** (hardcoded via `register_compute_function()`)

DB functions override code registry functions with the same name.

### Best Practices

- **Core engines stay in code**: Don't try to move `indicator` or `derivative` to DB
- **Custom logic goes to DB**: User-defined features, experiments, prototypes
- **Version control**: Export regularly, commit to git
- **Idempotent imports**: Safe to re-import; uses `ON CONFLICT DO UPDATE`
- **Clear descriptions**: Document what each function does
- **Test before deploy**: Verify functions in dev before production import

## Current focus

- Capture initial domain notes (data model, sources/computed separation, calc_store-style descriptors)
- Set up a minimal Python package + pytest harness
- Keep AlphaVantage credentials in `.env` (reused from `../gefjon/.env`, never printed or committed)

## Running tests
```bash
python -m pytest -q
```

### Make targets
- `make venv` — create/upgrade the venv and install dev deps
- `make test` — run pytest in the venv (DB tests skipped)
- `make test-db` — run pytest with `ENABLE_DB_TESTS=1` (requires running Postgres)
- `make db-up` / `make db-down` — start/stop TimescaleDB via docker compose
- `make db-health` — pg_isready check against the running container

## CLI
- Install (editable): `pip install -e .`
- Run: `g2 ingest-prices --symbol IBM --input tests/fixtures/demo_time_series_daily_adjusted.json`
  - Uses `DATABASE_URL` from env; creates tables if missing.
- Universe ingest (AlphaVantage LISTING_STATUS + prices):
  - `g2 ingest-universe --exchange NASDAQ --limit 5 --max-workers 4 --timeframe auto --update-existing`
  - Respects `ALPHAVANTAGE_API_KEY` and `calls_per_minute` (default 75 for premium). `timeframe auto` chooses full if no data or newest point is older than ~100 days; otherwise compact. `--update-existing` upserts existing dates.
  - CLI loads `.env` automatically for `ALPHAVANTAGE_API_KEY` and `DATABASE_URL`.
- Indicators ingest:
  - `g2 ingest-indicators --exchange NASDAQ --local --indicators rsi,macd,bbands,adx,stoch,psar --refresh`
  - Local mode (default) computes indicators from existing `stock_ohlcv` and resumes from the last indicator date; use `--api` to fetch from AlphaVantage instead. Writer/fetch workers are auto-sized; progress shows mode and queue/fetch stats.

## Local Postgres (TimescaleDB)
- Copy `.env.example` to `.env` and adjust credentials as needed.
- Start database: `docker compose up -d postgres`
- A TimescaleDB extension is enabled via `docker/initdb.d/timescaledb.sql`.
- **Initialize schema**: `psql -d g2 -f sql/schema.sql`
  - Creates all tables, hypertables, and indexes
  - Safe to run multiple times (idempotent)

## Contributing workflow (TDD-first)
- Write a failing test that describes the behavior
- Implement the smallest change to make it pass
- Refactor with tests green
- Update docs/dev-journal.md with what changed and why
