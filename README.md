# g2 (working title)

Database-first ML platform for quantitative stock analysis. Ingests price data, computes technical indicators, trains quantile regression models, and generates return predictions.

**Key Features:**

- 📊 AlphaVantage integration with 5,600+ NASDAQ stocks
- 🔧 Technical indicators (RSI, MACD, Bollinger Bands, etc.) + extensible custom features
- 🤖 ML pipeline: multi-horizon quantile regression (7/30/90-day forecasts)
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
g2 seed-features                        # Seed technical indicator definitions (RSI, MACD, Bollinger Bands, etc.)
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

# Compute all indicators for those stocks
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
g2 feat-fx-import --dir feature-functions
```

This stores the function in the `feature_functions` table, making it available to the dispatcher.

### Step 3: Register Feature Definitions

Register three features: sentiment score, relevance, and article count:

```bash
# Sentiment score (weighted by relevance)
g2 feat-def-register --definition '{
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
g2 feat-def-register --definition '{
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
g2 feat-def-register --definition '{
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
g2 feat-compute --features news_sentiment_score,news_relevance_score,news_article_count \
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
g2 ml dataset-build --name sentiment_test --version v1 \
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
- **Dispatcher**: `src/g2/ingest/dispatcher.py` - loads and executes functions
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
2. Import to database: `g2 feat-fx-import`
3. Register definition: `g2 feat-def-register`
4. Compute: `g2 feat-compute`

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

**See:**
- [PROGRESS.md](PROGRESS.md) for detailed status and recent changes
- [NEXT_STEPS.md](NEXT_STEPS.md) for prioritized implementation roadmap

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
