# g2 (working title)

Database-first ML platform for quantitative stock analysis. Ingests price data, computes technical indicators, trains quantile regression models, and generates return predictions.

**Key Features:**

- 📊 AlphaVantage integration with 5,600+ NASDAQ stocks
- 🔧 17 technical indicators (RSI, MACD, Bollinger Bands, etc.) + extensible custom features
- 🤖 ML pipeline: multi-horizon quantile regression (7/30/90-day forecasts)
- 💬 Natural language interface via MCP server
- 🗃️ TimescaleDB for efficient time-series storage
- 🔌 DB-first architecture: features and functions stored in database, exported to git

## Prerequisites

Before starting, ensure you have:

- **Python 3.10+** - Check with `python --version`
- **Docker & Docker Compose** - For TimescaleDB database
- **PostgreSQL client (psql)** - For schema initialization
- **AlphaVantage API key** - Get free at [alphavantage.co](https://www.alphavantage.co/support/#api-key)

Optional:

- **Make** - For convenient commands (`make venv`, `make test`)
- **GPU + nvidia-container-toolkit** - For accelerated ML training (XGBoost/LightGBM)

## Quick Start (10 minutes)

### 1. Install and Configure

```bash
# Create Python environment and install g2
make venv                               # Creates .venv + installs g2 + dependencies
source .venv/bin/activate               # Activate venv (Windows: .venv\Scripts\activate)

# Configure environment variables
cp .env.example .env
# Edit .env and set:
#   DATABASE_URL=postgresql://g2:g2pass@localhost:5432/g2
#   ALPHAVANTAGE_API_KEY=your_key_here
```

### 2. Start Database

```bash
docker compose up -d postgres           # Start TimescaleDB
docker compose ps postgres              # Verify it's healthy (wait ~10 seconds)
```

### 3. Initialize Schema and Seed Data

```bash
psql -d g2 -f sql/schema.sql            # Create tables, hypertables, indexes
g2 seed-features                        # Seed 17 technical indicator definitions
```

### 4. Test with Sample Data (Offline)

```bash
# Ingest sample IBM data (offline, uses bundled fixture)
g2 prices-ingest --symbol IBM --input tests/fixtures/demo_time_series_daily_adjusted.json

# Compute RSI indicator
g2 run-features --features indicator_rsi_14 --symbols IBM --local
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
g2 data-update --exchange NASDAQ --limit 50 --timeframe auto

# Compute all 17 indicators for those stocks
g2 feat-compute --exchange NASDAQ --limit 50 --local
```

**Learn more:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) - Full CLI reference

### 🤖 Machine Learning Pipeline

Train quantile regression models to predict return distributions:

```bash
# Prerequisites: Have price data + features in database (see above)

# 1. Build dataset
g2 ml dataset-build --name mvp --version v1 --symbols AAPL,MSFT --horizons 7,30 --export

# 2. Train model (predicts q10/q50/q90 quantiles)
g2 ml train --dataset-name mvp --dataset-version v1 --model-name model --model-version $(date +%Y%m%d)

# 3. Generate predictions
g2 ml predict --model-name model --model-version $(date +%Y-%m-%d) --prediction-date $(date +%Y-%m-%d) --symbols AAPL,MSFT

# 4. Evaluate performance (calibration metrics)
g2 ml eval --model-name model --model-version $(date +%Y%m%d) --start-date 2024-01-01 --end-date 2024-11-30
```

**Learn more:** [docs/ML_QUICKSTART.md](docs/ML_QUICKSTART.md) - Complete ML workflow guide

### 💬 Natural Language Interface (MCP Server)

Interact with g2 using natural language via Model Context Protocol:

```text
You: "Update NASDAQ data for the top 100 stocks"
Assistant: [Runs g2 data-update --exchange NASDAQ --limit 100]

You: "Build a dataset with AAPL, MSFT, GOOGL for 7 and 30 day horizons"
Assistant: [Runs g2 ml dataset-build ...]

You: "Show me predictions for AAPL from the last week"
Assistant: [Queries database and displays results]
```

**Learn more:** [mcp-server/README.md](mcp-server/README.md) - MCP server setup and usage

## Creating Custom Features & Data Sources

g2's DB-first architecture makes it easy to add custom indicators, alternative data, or new data sources without modifying code.

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
g2 feat-fx-import --dir feature-functions

# Register feature definition
g2 feat-def-register --definition '{
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
g2 feat-compute --features daily_price_change_pct --symbols AAPL,MSFT --local
```

### Ingesting New Data Sources

Add data from new API endpoints (sentiment, fundamentals, news, etc.):

**Example: AlphaVantage News Sentiment API**

The AlphaVantage [News Sentiment API](https://www.alphavantage.co/documentation/#news-sentiment) provides sentiment scores for news articles. Here's how to integrate it:

**Step 1: Create API Fetcher Function**

Store in `feature-functions/news_sentiment_fetcher.json`:

```json
{
  "name": "news_sentiment_fetcher",
  "version": "1.0",
  "language": "python",
  "description": "Fetch news sentiment from AlphaVantage API",
  "status": "active",
  "enabled": true,
  "function_body": "import requests\nimport os\nfrom datetime import datetime\n\ndef compute(rows, specs):\n    \"\"\"Fetch sentiment data from AlphaVantage News Sentiment API.\"\"\"\n    api_key = os.environ.get('ALPHAVANTAGE_API_KEY')\n    symbol = rows[0]['symbol']  # Get symbol from price data\n    \n    # Call AlphaVantage News Sentiment API\n    url = f'https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol}&apikey={api_key}'\n    response = requests.get(url)\n    data = response.json()\n    \n    # Parse sentiment data and return time-series rows\n    results = []\n    for article in data.get('feed', []):\n        for ticker_sentiment in article.get('ticker_sentiment', []):\n            if ticker_sentiment['ticker'] == symbol:\n                results.append({\n                    'date': article['time_published'][:10],  # Extract date\n                    'sentiment_score': float(ticker_sentiment['ticker_sentiment_score']),\n                    'relevance_score': float(ticker_sentiment['relevance_score'])\n                })\n    \n    return results\n"
}
```

**Step 2: Import Function to Database**

```bash
g2 feat-fx-import --dir feature-functions
```

This stores the function in the `feature_functions` table, making it available to the dispatcher.

**Step 3: Register Feature Definition**

Register it so g2 knows how to run it:

```bash
g2 feat-def-register --definition '{
  "name": "news_sentiment_score",
  "function_name": "news_sentiment_fetcher",
  "params": {},
  "source_table": "stock_ohlcv",
  "source_column": "symbol",
  "store_table": "computed_features",
  "store_column": "value",
  "active": true
}'
```

**Step 4: Compute for Symbols**

```bash
# Fetch and store sentiment data
g2 feat-compute --features news_sentiment_score --symbols AAPL,MSFT,GOOGL --local
```

The dispatcher will:
1. Load the `news_sentiment_fetcher` function from database
2. Call it for each symbol
3. Store results in `computed_features` table
4. Available for ML training as a feature

**Where Code Lives:**

- **API fetcher functions**: `feature-functions/` directory → imported to `feature_functions` table
- **Feature definitions**: Registered in `feature_definitions` table
- **Dispatcher**: `src/g2/ingest/dispatcher.py` - loads and executes functions
- **Data storage**: `computed_features` table (or custom table if needed)

**Alternative: Custom Table for Complex Data**

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

**Other API Data Sources You Can Add:**

- **AlphaVantage News Sentiment** - Article sentiment scores (example above)
- **AlphaVantage Fundamentals** - `OVERVIEW`, `INCOME_STATEMENT`, `BALANCE_SHEET`, `EARNINGS`
- **FRED Economic Data** - GDP, unemployment, interest rates
- **Twitter/Reddit Sentiment** - Social media APIs
- **SEC EDGAR** - Insider trading (Form 4), earnings filings
- **Options Data** - `HISTORICAL_OPTIONS` endpoint
- **Analyst Ratings** - From financial data providers
- **Weather Data** - For retail/energy stocks

**Pattern is always the same:**
1. Create fetcher function in `feature-functions/`
2. Import to database: `g2 feat-fx-import`
3. Register definition: `g2 feat-def-register`
4. Compute: `g2 feat-compute`

**What's allowed in sandboxed functions:**

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
        DataUpdate[g2 data-update]
        FeaturesCompute[g2 feat-compute]
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
- [mcp-server/README.md](mcp-server/README.md) - Natural language interface setup
- [docs/archive/ml/](docs/archive/ml/) - ML vision and future roadmap
- [PROGRESS.md](PROGRESS.md) - Current status and recent changes

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

**Test Coverage:** 27 ML tests, full CLI integration tests

## Useful Commands

```bash
# Database
make db-up                              # Start PostgreSQL
make db-down                            # Stop PostgreSQL
make db-health                          # Check database health

# Development
make venv                               # Create/upgrade virtualenv
g2 --help                               # Show all CLI commands
g2 ml --help                            # Show ML subcommands

# Feature Management
g2 feat-fx-export --dir feature-functions    # Export functions to git
g2 feat-fx-import --dir feature-functions    # Import functions from git
g2 feat-def-export --dir feature-definitions # Export definitions to git
g2 feat-def-import --dir feature-definitions # Import definitions from git
```

## Project Status

**Current State:**

- ✅ Data pipeline complete (ingestion, features, storage)
- ✅ ML Phase 1 complete (training, prediction, evaluation)
- ✅ MCP server implemented (natural language interface)
- ✅ Production-ready database schema
- ✅ Comprehensive documentation

**See:** [PROGRESS.md](PROGRESS.md) for detailed status and recent changes

## Contributing

This project follows strict TDD (test-driven development):

1. Write a failing test that describes the behavior
2. Implement the smallest change to make it pass
3. Refactor with tests green
4. Update documentation

**Key Practices:**

- Database is source of truth (DB-first architecture)
- All feature functions stored in database, exported to git
- Functions execute in sandboxed environment
- Comprehensive test coverage required

## License

See [LICENSE](LICENSE) file for details.
