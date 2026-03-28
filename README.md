# Gefion (working title)

Database-first ML platform for quantitative stock analysis. Ingests price data, computes technical indicators, trains ML models, and backtests trading strategies.

**Key Features:**

- 📊 AlphaVantage integration with 5,600+ NASDAQ stocks
- 🔧 Technical indicators (RSI, MACD, Bollinger Bands, etc.) + cross-sectional features (market-relative)
- 🤖 ML pipeline: quantile regression, trend classification, model ensembles, e2e testing
- 📈 Backtesting engine with execution modeling (costs, slippage, position sizing)
- 💬 Natural language interface via MCP server
- 🗃️ TimescaleDB for efficient time-series storage
- 🔌 DB-first architecture: features and functions stored in database, exported to git

## Prerequisites

Before starting, ensure you have:

- **Python 3.10+** - Check with `python --version`
- **Docker & Docker Compose** - For TimescaleDB database
- **PostgreSQL client (psql)** - For schema initialization
- **AlphaVantage API key** - Premium tier recommended for production use (75 calls/min). Get at [alphavantage.co](https://www.alphavantage.co/support/#api-key)

Optional:

- **Make** - For convenient commands (`make venv`, `make test`)
- **GPU + nvidia-container-toolkit** - For accelerated ML training (XGBoost/LightGBM)

## Quick Start (10 minutes)

### 1. Install and Configure

```bash
# Create Python environment and install gefion
make venv                               # Creates .venv + installs gefion + dependencies
source .venv/bin/activate               # Activate venv (Windows: .venv\Scripts\activate)

# Configure environment variables
cp .env.example .env
# Edit .env and set:
#   DATABASE_URL=postgresql://gefion:gefionpass@localhost:5432/gefion
#   ALPHAVANTAGE_API_KEY=your_key_here
```

### 2. Start Database

```bash
docker compose up -d postgres           # Start TimescaleDB
docker compose ps postgres              # Verify it's healthy (wait ~10 seconds)
```

### 3. Initialize Schema and Seed Data

```bash
psql -d gefion -f sql/schema.sql            # Create tables, hypertables, indexes
gefion seed-features                        # Seed technical indicator definitions (RSI, MACD, Bollinger Bands, etc.)
```

### 4. Test with Sample Data (Offline)

```bash
# Ingest sample IBM data (offline, uses bundled fixture)
gefion prices-ingest --symbol IBM --input tests/fixtures/demo_time_series_daily_adjusted.json

# Compute RSI indicator
gefion run-features --features indicator_rsi_14 --symbols IBM --local
```

✅ **Success!** You now have price data and computed features in the database.

### Next Steps

- **Live data ingestion:** See "Data Ingestion" section below
- **ML workflow:** See "Machine Learning" section below
- **Full CLI reference:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md)

## What Can You Do?

### 📊 Data Ingestion & Features

Ingest daily OHLCV data and compute technical indicators:

```bash
# Update prices for NASDAQ stocks (live API)
gefion data-update --exchange NASDAQ --limit 50 --timeframe auto

# Compute all indicators for those stocks
gefion feat-compute --exchange NASDAQ --limit 50 --local
```

**Learn more:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) - Full CLI reference

### 🤖 Machine Learning Pipeline

Train quantile regression models to predict return distributions:

```bash
# Prerequisites: Have price data + features in database (see above)

# Quick validation - run full e2e test (~2-5 minutes)
gefion ml e2e-test --limit 10

# Or step-by-step:

# 1. Build dataset
gefion ml dataset-build --name mvp --version v1 --symbols AAPL,MSFT --horizons 7,30 --export

# 2. Train model (predicts q10/q50/q90 quantiles)
gefion ml train --dataset-name mvp --dataset-version v1 --model-name model --model-version $(date +%Y%m%d)

# 3. Train ensemble (combines XGBoost + LightGBM)
gefion ml train-ensemble --dataset-name mvp --dataset-version v1 --model-name ensemble --model-version $(date +%Y%m%d)

# 4. Generate predictions
gefion ml predict --model-name model --model-version $(date +%Y%m%d) --symbols AAPL,MSFT

# 5. Evaluate performance (calibration metrics)
gefion ml eval --model-name model --model-version $(date +%Y%m%d) --start-date 2024-01-01 --end-date 2024-11-30
```

**Additional ML commands:**
- `gefion ml train-classifier` - Train 5-class trend classifier (strong_down → strong_up)
- `gefion ml predict-classifier` - Generate trend class predictions
- `gefion ml predict-ensemble` - Predictions using ensemble models

**Learn more:** [docs/ML_QUICKSTART.md](docs/ML_QUICKSTART.md) - Complete ML workflow guide

### 💬 Natural Language Interface (MCP Server)

Interact with Gefion using natural language via Model Context Protocol:

```text
You: "Update NASDAQ data for the top 100 stocks"
Assistant: [Runs Gefion data-update --exchange NASDAQ --limit 100]

You: "Build a dataset with AAPL, MSFT, GOOGL for 7 and 30 day horizons"
Assistant: [Runs Gefion ml dataset-build ...]

You: "Show me predictions for AAPL from the last week"
Assistant: [Queries database and displays results]
```

**Learn more:** [mcp-server/README.md](mcp-server/README.md) - MCP server setup and usage

## Creating Custom Features & Data Sources

Gefion's DB-first architecture makes it easy to add custom indicators, alternative data, or new data sources without modifying code.

### Custom Technical Indicators

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

Import and use:

```bash
# Import function to database
gefion feat-fx-import --dir feature-functions

# Register feature definition
gefion feat-def-register --definition '{
  "name": "daily_price_change_pct",
  "function_name": "price_change_pct",
  "params": {},
  "source_table": "stock_ohlcv",
  "source_column": "close",
  "store_table": "computed_features",
  "store_column": "value",
  "active": true
}'

# Compute for stocks
gefion feat-compute --features daily_price_change_pct --symbols AAPL,MSFT --local
```

### Ingesting New Data Sources

Add data from new API endpoints (sentiment, fundamentals, news, etc.). You have two storage options:

1. **Use `computed_features` table** (recommended for simple scalar values): Store single values per (symbol, date, feature) - good for most use cases, keeps data normalized
2. **Create dedicated table** (for complex multi-column data): Use when you need multiple related values per date, or when the data has a unique schema

## Example: AlphaVantage News Sentiment API

The AlphaVantage [News Sentiment API](https://www.alphavantage.co/documentation/#news-sentiment) provides sentiment scores for news articles. This example demonstrates the **computed_features** pattern for storing scalar values. For complex data with multiple columns, consider creating a dedicated table (see Alternative section below).

### Step 1: Create API Fetcher Function

Store in `feature-functions/news_sentiment_fetcher.json`:

```json
{
  "name": "news_sentiment_fetcher",
  "version": "1.0",
  "language": "python",
  "description": "Fetch news sentiment from AlphaVantage API with error handling and aggregation",
  "status": "active",
  "enabled": true,
  "function_body": "import requests\nimport os\nimport time\nfrom datetime import datetime\nfrom collections import defaultdict\n\ndef compute(rows, specs):\n    \"\"\"Fetch sentiment data from AlphaVantage News Sentiment API.\n    \n    Returns aggregated daily sentiment scores (mean of all articles per day).\n    Handles API errors, rate limiting, and missing data gracefully.\n    \"\"\"\n    api_key = os.environ.get('ALPHAVANTAGE_API_KEY')\n    if not api_key:\n        raise ValueError('ALPHAVANTAGE_API_KEY environment variable not set')\n    \n    symbol = rows[0]['symbol'] if rows else None\n    if not symbol:\n        return []\n    \n    # AlphaVantage rate limit: 5 calls/minute (free tier)\n    # Add 1s delay to respect rate limits when processing multiple symbols\n    time.sleep(1)\n    \n    # Call AlphaVantage News Sentiment API\n    url = f'https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol}&apikey={api_key}'\n    \n    try:\n        response = requests.get(url, timeout=30)\n        response.raise_for_status()\n        data = response.json()\n    except requests.exceptions.Timeout:\n        # API timeout - return empty, will retry later\n        return []\n    except requests.exceptions.RequestException as e:\n        # Network error - return empty\n        return []\n    \n    # Check for API error responses\n    if 'Error Message' in data:\n        # Invalid API key or other API error\n        return []\n    if 'Note' in data:\n        # Rate limit exceeded - return empty, will retry later\n        return []\n    \n    # Aggregate sentiment scores by date (multiple articles per day)\n    daily_sentiments = defaultdict(lambda: {'scores': [], 'relevance': []})\n    \n    for article in data.get('feed', []):\n        try:\n            # Extract date from time_published (format: '20241215T103000')\n            time_published = article.get('time_published', '')\n            if len(time_published) < 8:\n                continue\n            date = time_published[:8]  # YYYYMMDD\n            date_formatted = f'{date[:4]}-{date[4:6]}-{date[6:8]}'\n            \n            # Find sentiment for this ticker\n            for ticker_sentiment in article.get('ticker_sentiment', []):\n                if ticker_sentiment.get('ticker') == symbol:\n                    score = ticker_sentiment.get('ticker_sentiment_score')\n                    relevance = ticker_sentiment.get('relevance_score')\n                    \n                    if score is not None and relevance is not None:\n                        daily_sentiments[date_formatted]['scores'].append(float(score))\n                        daily_sentiments[date_formatted]['relevance'].append(float(relevance))\n        except (KeyError, ValueError, TypeError):\n            # Skip malformed articles\n            continue\n    \n    # Aggregate: weighted average by relevance\n    results = []\n    for date, data in daily_sentiments.items():\n        if data['scores'] and data['relevance']:\n            # Weight sentiment scores by relevance\n            total_relevance = sum(data['relevance'])\n            if total_relevance > 0:\n                weighted_sentiment = sum(\n                    score * relevance \n                    for score, relevance in zip(data['scores'], data['relevance'])\n                ) / total_relevance\n                \n                results.append({\n                    'date': date,\n                    'sentiment_score': round(weighted_sentiment, 4),\n                    'relevance_score': round(total_relevance / len(data['relevance']), 4),\n                    'article_count': len(data['scores'])\n                })\n    \n    return results\n"
}
```

### Step 2: Import Function to Database

```bash
gefion feat-fx-import --dir feature-functions
```

This stores the function in the `feature_functions` table, making it available to the dispatcher.

### Step 3: Register Feature Definitions

Register three features: sentiment score, relevance, and article count:

```bash
# Sentiment score (weighted by relevance)
gefion feat-def-register --definition '{
  "name": "news_sentiment_score",
  "function_name": "news_sentiment_fetcher",
  "params": {"column": "sentiment_score"},
  "source_table": "stock_ohlcv",
  "source_column": "symbol",
  "store_table": "computed_features",
  "store_column": "value",
  "active": true
}'

# Average relevance score
gefion feat-def-register --definition '{
  "name": "news_relevance_score",
  "function_name": "news_sentiment_fetcher",
  "params": {"column": "relevance_score"},
  "source_table": "stock_ohlcv",
  "source_column": "symbol",
  "store_table": "computed_features",
  "store_column": "value",
  "active": true
}'

# Article count (volume indicator)
gefion feat-def-register --definition '{
  "name": "news_article_count",
  "function_name": "news_sentiment_fetcher",
  "params": {"column": "article_count"},
  "source_table": "stock_ohlcv",
  "source_column": "symbol",
  "store_table": "computed_features",
  "store_column": "value",
  "active": true
}'
```

### Step 4: Compute for Symbols

```bash
# Fetch and store sentiment data (respects 5 calls/min rate limit)
gefion feat-compute --features news_sentiment_score,news_relevance_score,news_article_count \
  --symbols AAPL,MSFT,GOOGL --local
```

### Example Output

After running the computation, your `computed_features` table will contain:

| data_id | date       | feature_id | value    |
|---------|------------|------------|----------|
| 1       | 2024-12-13 | 42         | 0.3524   |
| 1       | 2024-12-13 | 43         | 0.7823   |
| 1       | 2024-12-13 | 44         | 5        |
| 1       | 2024-12-14 | 42         | -0.1234  |
| 1       | 2024-12-14 | 43         | 0.6421   |
| 1       | 2024-12-14 | 44         | 3        |

Where:

- `feature_id=42` → news_sentiment_score (range: -1 to +1, negative=bearish, positive=bullish)
- `feature_id=43` → news_relevance_score (range: 0 to 1, how relevant articles are)
- `feature_id=44` → news_article_count (number of articles mentioning the ticker)

### Querying Sentiment Data

```sql
-- View sentiment for AAPL over last 30 days
SELECT
    s.symbol,
    cf.date,
    MAX(CASE WHEN fd.name = 'news_sentiment_score' THEN cf.value END) as sentiment,
    MAX(CASE WHEN fd.name = 'news_relevance_score' THEN cf.value END) as relevance,
    MAX(CASE WHEN fd.name = 'news_article_count' THEN cf.value END) as article_count
FROM computed_features cf
JOIN stocks s ON s.id = cf.data_id
JOIN feature_definitions fd ON fd.id = cf.feature_id
WHERE s.symbol = 'AAPL'
  AND cf.date >= CURRENT_DATE - INTERVAL '30 days'
  AND fd.name LIKE 'news_%'
GROUP BY s.symbol, cf.date
ORDER BY cf.date DESC;

-- Find stocks with strong positive sentiment (> 0.3) and high coverage
SELECT
    s.symbol,
    cf.date,
    cf.value as sentiment_score
FROM computed_features cf
JOIN stocks s ON s.id = cf.data_id
JOIN feature_definitions fd ON fd.id = cf.feature_id
WHERE fd.name = 'news_sentiment_score'
  AND cf.value > 0.3
  AND cf.date >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY cf.value DESC
LIMIT 20;
```

### Using in ML Training

All features in `computed_features` for the specified symbols and date range are automatically included when building datasets:

```bash
# All available features will be included (news sentiment + technical indicators)
gefion ml dataset-build --name sentiment_test --version v1 \
  --symbols AAPL,MSFT,GOOGL --horizons 7,30 --export

# Features CSV will include columns for all computed features:
# - news_sentiment_score
# - news_relevance_score
# - news_article_count
# - indicator_rsi_14
# - indicator_macd
# - (all other registered and computed features)
```

**Note:** Currently, all features in `computed_features` are included. To use only specific features for training:

1. Build the full dataset with `--export` to get CSVs
2. Manually filter the `features.csv` file to include only desired columns
3. Retrain using the filtered dataset

Future versions may support feature selection during dataset build.

### Rate Limiting & Performance

- **AlphaVantage premium tier**: 75 API calls/minute (recommended for production)
- **Built-in delay**: 1.0 second minimum spacing between calls (enforced to prevent burst patterns)
- **Throughput**: ~60 symbols/minute with premium tier
- **Batch processing**: Process 500 symbols ≈ 8 minutes; full NASDAQ universe (5,600 stocks) ≈ 90 minutes

### Troubleshooting

1. **Empty results returned:**
   - Check API key is valid: `echo $ALPHAVANTAGE_API_KEY`
   - Verify symbol exists: Small-cap stocks may have no news coverage
   - Check date range: API returns last 50 articles (typically 7-14 days)

2. **Rate limit errors:**
   - Error: `{'Note': 'Thank you for using Alpha Vantage! Our standard API call frequency is...'}`
   - Solution: Built-in 1s delay handles this automatically with premium tier
   - If using free tier: Reduce `--max-workers` or expect slower processing (5 calls/min limit)

3. **Missing dates:**
   - Sentiment data is sparse (only dates with news articles)
   - ML pipeline handles missing features via median imputation
   - To check coverage: `SELECT COUNT(DISTINCT date) FROM computed_features WHERE feature_id=42`

4. **Debugging API responses:**

   ```python
   # Test fetcher manually
   import json
   with open('feature-functions/news_sentiment_fetcher.json') as f:
       func_def = json.load(f)

   exec(func_def['function_body'])
   rows = [{'symbol': 'AAPL'}]
   result = compute(rows, {})
   print(json.dumps(result, indent=2))
   ```

The dispatcher will:

1. Load the `news_sentiment_fetcher` function from database
2. Call it for each symbol with rate limiting
3. Store results in `computed_features` table
4. Features are available for ML training in dataset builds

### Where Code Lives

- **API fetcher functions**: `feature-functions/` directory → imported to `feature_functions` table
- **Feature definitions**: Registered in `feature_definitions` table
- **Dispatcher**: `src/gefion/ingest/dispatcher.py` - loads and executes functions
- **Data storage**: `computed_features` table (or custom table if needed)

### Alternative: Custom Table for Complex Data

Use when data doesn't fit the `computed_features` schema (e.g., multiple columns per row):

```python
# custom_ingest.py
import psycopg
import pandas as pd

# Read your data source (CSV, API, etc.)
df = pd.read_csv('earnings_data.csv')

# Insert into database
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    with conn.cursor() as cur:
        # Option A: Use computed_features (generic)
        for _, row in df.iterrows():
            cur.execute("""
                INSERT INTO computed_features (data_id, date, feature_id, value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (data_id, date, feature_id) DO UPDATE
                SET value = EXCLUDED.value
            """, (stock_id, row['date'], feature_id, row['earnings_surprise']))

        # Option B: Create custom table (for complex data)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS earnings_data (
                data_id INT REFERENCES stocks(id),
                date DATE,
                eps_actual DECIMAL,
                eps_estimate DECIMAL,
                surprise_pct DECIMAL,
                PRIMARY KEY (data_id, date)
            )
        """)

# Then run via CLI
python custom_ingest.py
```

### Other API Data Sources You Can Add

- **AlphaVantage News Sentiment** - Article sentiment scores (example above)
- **AlphaVantage Fundamentals** - `OVERVIEW`, `INCOME_STATEMENT`, `BALANCE_SHEET`, `EARNINGS`
- **FRED Economic Data** - GDP, unemployment, interest rates
- **Twitter/Reddit Sentiment** - Social media APIs
- **SEC EDGAR** - Insider trading (Form 4), earnings filings
- **Options Data** - `HISTORICAL_OPTIONS` endpoint
- **Analyst Ratings** - From financial data providers
- **Weather Data** - For retail/energy stocks

### Pattern is always the same

1. Create fetcher function in `feature-functions/`
2. Import to database: `gefion feat-fx-import`
3. Register definition: `gefion feat-def-register`
4. Compute: `gefion feat-compute`

### What's allowed in sandboxed functions

- ✅ pandas, numpy, scipy, sklearn, talib
- ✅ External APIs via requests
- ✅ JSON/CSV parsing
- ✅ Date/time operations
- ❌ File I/O (use database)
- ❌ eval(), exec(), arbitrary imports

**See:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for DB-first architecture details

## Architecture

```mermaid
graph TB
    subgraph "Data Sources"
        AV[AlphaVantage API]
    end

    subgraph "CLI Commands"
        DataUpdate[Gefion data-update]
        FeaturesCompute[Gefion feat-compute]
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
- **DB-First**: Custom feature functions stored in database with git backup for version control

## Documentation Index

**Getting Started:**

- This README - Installation and overview
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) - Full CLI reference
- [docs/ML_QUICKSTART.md](docs/ML_QUICKSTART.md) - End-to-end ML workflow
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - Common issues and solutions

**Advanced:**

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - System design and DB-first architecture
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) - Optimization techniques
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) - OpenTelemetry + Grafana Tempo for performance investigation
- [docs/WHITEPAPER_TECHNICAL_ANALYSIS_AND_ML.md](docs/WHITEPAPER_TECHNICAL_ANALYSIS_AND_ML.md) - White paper on technical analysis and ML
- [mcp-server/README.md](mcp-server/README.md) - Natural language interface setup
- [docs/archive/ml/](docs/archive/ml/) - ML vision and future roadmap
- [.specify/memory/progress.md](.specify/memory/progress.md) - Current status and capabilities

Tempo/Grafana docker files live in `docker/tempo/` (start with `docker compose -f docker/tempo/docker-compose.tempo.yml up -d`).

### Observability & Performance

Gefion uses OpenTelemetry + Grafana Tempo for tracing. During development, `gefion ui` **auto-detects Tempo** and enables tracing automatically.

```bash
# Start all services (postgres + tempo + grafana)
# In Claude Code: /gefion-services start
docker compose up -d postgres
docker compose -f docker/tempo/docker-compose.tempo.yml up -d

# Verify tracing works end-to-end
bash scripts/otel_smoke_test.sh

# Launch UI (auto-detects Tempo, enables OTEL)
gefion ui
# Shows: "Starting Gefion UI on http://localhost:8501 (tracing: enabled — Tempo detected)"

# Check recent traces for slow operations
gefion span-check --limit 10
```

**Performance Feedback Loop** (Claude Code skills):

| Command | What it does |
|---------|-------------|
| `/gefion-perf` | Query Tempo for slow traces, rank by duration, suggest fixes |
| `/gefion-perf 1000` | Show traces slower than 1 second |
| `/gefion-perf baseline` | Save current trace durations as a performance baseline |
| `/gefion-perf compare` | Compare current traces against saved baseline, flag regressions |
| `/gefion-perf fix` | Find slowest trace and suggest/implement a fix |
| `/loop 5m /gefion-perf` | Continuous monitoring — check traces every 5 minutes |

**Span-specific thresholds** (what counts as "slow"):

| Operation | Threshold | Rationale |
|-----------|-----------|-----------|
| `ui.*` (page loads) | 500ms | Pages should feel instant |
| `db.*` (database) | 500ms | Queries should be fast |
| `charts.*` (rendering) | 2000ms | Chart rendering has overhead |
| `cli.*` (commands) | 5000ms | CLI includes I/O |

**Automated enforcement** (happens without manual action):

- **Session start**: Hook checks if postgres + tempo are running, warns if not
- **After pytest**: Hook queries Tempo for slow spans, alerts in context
- **Pre-commit**: Blocks commits of significant files missing `from gefion.observability import`

More details: [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) and [docs/TEMPO_QUICKSTART.md](docs/TEMPO_QUICKSTART.md).

## Running Tests

```bash
# Quick tests (no database required)
make test                               # Uses fixture data

# Full test suite (requires PostgreSQL running)
make test-db                            # Includes database integration tests

# Manual pytest commands
pytest -q                               # All tests (skips DB if not available)
pytest -q tests -k "not db"             # Explicitly skip DB tests
ENABLE_DB_TESTS=1 pytest -q             # Force DB tests
```

**Test Coverage:** 127 tests (ML, CLI, strategies, backtesting, integration)

## Useful Commands

```bash
# Database
make db-up                              # Start PostgreSQL
make db-down                            # Stop PostgreSQL
make db-health                          # Check database health

# Development
make venv                               # Create/upgrade virtualenv
gefion --help                               # Show all CLI commands
gefion ml --help                            # Show ML subcommands

# Feature Management
gefion feat-fx-export --dir feature-functions    # Export functions to git
gefion feat-fx-import --dir feature-functions    # Import functions from git
gefion feat-def-export --dir feature-definitions # Export definitions to git
gefion feat-def-import --dir feature-definitions # Import definitions from git
```

## Project Status

**Current State:**

- ✅ Data pipeline complete (ingestion, features, storage)
- ✅ ML pipeline complete (quantile regression, trend classification, ensembles)
- ✅ E2E test command for quick pipeline validation
- ✅ Advanced backtesting (6 strategies, execution modeling)
- ✅ MCP server implemented (natural language interface)
- ✅ Production-ready database schema (unified predictions table)
- ✅ D3.js interactive charts (17 chart types across 4 categories)
- ✅ Ask Gefion — contextual AI chat on every UI page
- ✅ Comprehensive observability (48 instrumented modules, automated perf detection)
- ✅ Cascading data cull (`gefion data cull --before DATE`)
- ✅ Comprehensive documentation

**See:**
- [.specify/memory/progress.md](.specify/memory/progress.md) for detailed status and capabilities
- [.specify/memory/backlog.md](.specify/memory/backlog.md) for prioritized implementation backlog

## Contributing

This project follows the [Gefion Constitution](.specify/memory/constitution.md). Key requirements:

### Development Workflow

```
1. Start services          /gefion-services start (postgres + tempo + grafana)
2. Write failing tests     tests/ before src/ (TDD — enforced by hooks)
3. Implement               Minimum code to pass tests
4. Instrument              from gefion.observability import create_span (enforced by pre-commit)
5. Verify traces           /gefion-perf — check for slow spans
6. Save baseline           /gefion-perf baseline (after perf fixes)
7. Commit                  Tests + implementation together
```

### Enforcement Layers

| Hook | When | What |
|------|------|------|
| SessionStart | Claude Code opens | Checks postgres + tempo running |
| PreToolUse | Before code edit | TDD: tests before src changes |
| PreCommit | Before git commit | Observability imports required |
| PostToolUse | After pytest | Queries Tempo for slow spans |

### Key Practices

- **Database-first**: DB is source of truth, git exports are backups
- **TDD (non-negotiable)**: Tests before implementation, enforced by hooks
- **Observability (non-negotiable)**: All significant operations traced, enforced by pre-commit
- **CLI-first**: Every feature has a CLI command before UI, major commands have MCP tools
- **Trace-driven performance**: Use `/gefion-perf` to find and fix slow operations

## License

See [LICENSE](LICENSE) file for details.
