# g2: ML-Driven Financial Trend Signal Analysis

> **Current Status (December 2025)**: Phase 1 complete, Phase 2 infrastructure complete.
> Data pipeline is production-ready with ~5,600 NASDAQ stocks tracked daily.
> Ready to begin ML implementation (feature engineering, label generation, model training).
> See [PROGRESS.md](PROGRESS.md) for detailed current state assessment.

## Project Vision

gefion modernizes the rule-based technical analysis approach from the legacy Folly project by applying PyTorch-based machine learning to predict trend signal strength. Rather than manually crafting rules to identify momentum patterns, Gefion will learn these patterns from historical data and provide probabilistic predictions for multiple time horizons.

## Evolution from Folly

### What Folly Did (Rule-Based Approach)
- **Data Collection**: Downloaded OHLCV data from Yahoo Finance
- **Indicator Calculation**: Computed technical indicators (RSI, PSAR, ADX, Moving Averages) using Perl libraries
- **Pattern Matching**: Used a sophisticated query language to describe "curve shapes" through combinations of:
  - Value thresholds (e.g., RSI > 30)
  - Relative comparisons (e.g., MA_30 < 90% of MA_50)
  - Slope analysis (momentum detection over windows)
  - Multi-timeframe signals
- **Storage**: MySQL database with time-series tables and calculated indicators
- **Limitation**: Manual rule creation without data-driven optimization or backtesting

### What Gefion Will Do (ML Approach)
- **Enhanced Data Sources**: Leverage AlphaVantage API for pre-calculated indicators, eliminating custom computation
- **Modern Storage**: Use performant time-series database (PostgreSQL with TimescaleDB)
- **PyTorch Models**: Train neural networks to predict trend signal strength instead of hand-coding rules
- **Multi-Horizon Predictions**: Start with one multi-output model for 7/30/90-day horizons (fallback: separate models only if multi-output underperforms)
- **Self-Validating**: Use rolling windows to measure model accuracy against actual future returns
- **TDD Approach**: Build with test-driven development for reliability and maintainability
- **Modern Development Practices**: Follow contemporary software engineering best practices including containerization, runtime flexibility, single source of truth, CI/CD automation, and cloud-native architecture

## Core Problem Statement

gefion provides **two complementary prediction systems** for trend following:

### System 1: Return Distribution Prediction (Quantile Regression)

**Business Question**: What is the expected return distribution for a stock over the next 7/30/90 days?

**Input**: Historical stock data with technical indicators (OHLCV, RSI, PSAR, ADX, Moving Averages, sector data, etc.)

**Output**: Multi-horizon return distribution predictions
- **7-day horizon**: Return quantiles (10th, 50th, 90th percentile)
- **30-day horizon**: Return quantiles (10th, 50th, 90th percentile)
- **90-day horizon**: Return quantiles (10th, 50th, 90th percentile)

**Use Cases**:
- Risk assessment (downside: q10, upside: q90)
- Position sizing (wider distribution = smaller position)
- Understanding uncertainty

**Validation**: Compare predicted distributions to actual returns using quantile loss and calibration metrics.

### System 2: Trend Classification (NEW - Pattern Recognition)

**Business Question**: Will this stock make a strong directional move (e.g., ±10%) in the next 7/30/90 days?

**Input**: Same feature set as quantile regression

**Output**: Trend probability classifications
- **Binary**: P(move ≥ threshold) for configurable thresholds
- **Multi-class**: Strong up / Weak up / Neutral / Weak down / Strong down

**Use Cases**:
- Screening: "Show me stocks with high probability of making strong directional moves"
- Entry timing: "Is now a good time to enter this trend?"
- Pattern extraction: "What patterns preceded successful trends?"
- Customizable thresholds: Define what constitutes a "strong move" (e.g., ±5%, ±10%, ±15%)
- Multiple horizons: Screen for trends at different time scales (7-day, 30-day, 90-day)

**Validation**: Classification metrics (accuracy, precision, recall, F1), confusion matrices, and forward testing on held-out data.

### Why Both Systems?

**Workflow Example**:
1. **Screen with classifier**: Find stocks likely to trend (System 2)
2. **Assess risk/reward with quantiles**: Check if risk/reward is favorable (System 1)
3. **Size position based on distribution**: Wider uncertainty = smaller size (System 1)
4. **Track pattern effectiveness**: Did the pattern work? (System 2 validation)

Both systems share infrastructure (data, features, training) but serve different purposes in a complete trend-following workflow.

## Modeling Approach: One Model or Three?

**Answer: Start with ONE multi-output model, fallback to separate models if needed.**

Your question *"What's the likelihood stock A moves X points in 7/30/90 days?"* can be answered by:

**Single Multi-Output Quantile Model** (Recommended):
```
One PyTorch model with shared encoder + three output heads
↓
Predicts all horizons simultaneously: 7d, 30d, 90d
↓
Each horizon outputs quantiles: [q10, q50, q90]
↓
Total outputs: 9 values per prediction
```

**Advantages:**
- **Efficiency**: Train once, deploy once, maintain once
- **Consistency**: Can't predict contradictory signals (e.g., strong 7d up, strong 30d down)
- **Better learning**: 7-day patterns often inform 30-day patterns; shared layers capture this
- **Practical**: Easier to version, monitor, and debug one model

**When to use separate models:**
- If multi-output validation loss is >10% worse than separate models
- If different horizons need fundamentally different features
- If you want to iterate on horizons independently

**Implementation path:**
1. **Phase 1**: Build multi-output model (weeks 5-7)
2. **Phase 2**: If underperforming, try separate models (week 8)
3. **Phase 3**: Use whichever performs better in backtests (week 9)

The multi-output approach directly answers your use case: "Given current conditions, what's the distribution of outcomes across multiple timeframes?"

## Architecture Design

### 1. Data Layer

**Data Sources:**
- **AlphaVantage API** (https://www.alphavantage.co/documentation)
  - TIME_SERIES_DAILY: OHLCV data
  - Technical indicators: RSI, ADX, BBANDS, SMA, EMA, MACD, Stochastic, etc.
  - Fundamental data (optional future enhancement)
  - **Rate Limits**:
    - Free tier: 25 requests/day, 5 requests/minute
    - Premium: 75-1200 requests/minute depending on tier
  - **Critical**: Must implement batching and caching strategies (see Data Ingestion Strategy below)

**Storage (Current Implementation):**

- **PostgreSQL + TimescaleDB**
  - ACID compliance with excellent time-series extensions
  - Automatic hypertable partitioning with 30-day chunks
  - Connection pooling for production-grade concurrent access
  - BRIN indexes for time-based queries
  - Composite B-tree indexes for data_id + date queries

**Schema Design (Current Implementation):**

gefion uses a normalized, registry-based design for efficient storage and flexible feature engineering:

```sql
-- Stock dimension table (normalized design using integer IDs)
CREATE TABLE stocks (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR UNIQUE NOT NULL,
    status VARCHAR DEFAULT 'Active'
);

-- OHLCV time-series data (TimescaleDB hypertable with 30-day chunks)
CREATE TABLE stock_ohlcv (
    id BIGSERIAL,
    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    open NUMERIC(18,6),
    high NUMERIC(18,6),
    low NUMERIC(18,6),
    close NUMERIC(18,6),
    adjusted_close NUMERIC(18,6),
    dividend_amount NUMERIC(18,6),
    split_coefficient NUMERIC(18,6),
    volume BIGINT,
    source TEXT,
    PRIMARY KEY (id, date),
    UNIQUE (data_id, date)
);

-- Convert to TimescaleDB hypertable with 30-day chunks
SELECT create_hypertable('stock_ohlcv', 'date', if_not_exists => TRUE);
SELECT set_chunk_time_interval('stock_ohlcv', INTERVAL '30 days');

-- Indexes for efficient time-series queries
CREATE INDEX stock_ohlcv_brin ON stock_ohlcv USING BRIN(date);
CREATE INDEX stock_ohlcv_data_id_date_idx ON stock_ohlcv(data_id, date DESC);

-- Feature registry (defines available features and their computation)
CREATE TABLE feature_definitions (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    function_name TEXT NOT NULL,
    params JSONB,
    source_table TEXT,
    source_column TEXT,
    store_table TEXT DEFAULT 'computed_features',
    store_column TEXT,
    store_type TEXT DEFAULT 'double precision',
    active BOOLEAN DEFAULT TRUE,
    version TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Computed features (tall format for flexibility - stores all indicator values)
CREATE TABLE computed_features (
    data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    feature_id INTEGER NOT NULL REFERENCES feature_definitions(id),
    value DOUBLE PRECISION,
    source TEXT,
    PRIMARY KEY (data_id, date, feature_id)
);

-- Function registry (maps function names to Python implementations)
CREATE TABLE feature_functions (
    name TEXT PRIMARY KEY,
    module_path TEXT NOT NULL,
    description TEXT,
    incremental_capable BOOLEAN DEFAULT FALSE,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Model predictions (for backtesting and monitoring)
-- Decision: store predictions in dedicated tables (not as computed_features).
-- See docs/archive/ml/ML_SYSTEM_DESIGN.md for canonical schemas:
--   quantile_predictions, prediction_outcomes, model_performance

```

**Future Schema Enhancements (Planned):**

The following tables represent planned enhancements for cross-sectional features and metadata:

```sql
-- Market-level indicators (VIX, breadth, sentiment)
CREATE TABLE market_indicators (
    date DATE PRIMARY KEY,
    vix DECIMAL,
    spy_return DECIMAL,
    advance_decline DECIMAL,
    put_call_ratio DECIMAL
);

-- Sector ETF data for relative strength and sector rotation signals
CREATE TABLE sector_etfs (
    etf_symbol VARCHAR,
    date DATE,
    open DECIMAL,
    high DECIMAL,
    low DECIMAL,
    close DECIMAL,
    volume BIGINT,
    PRIMARY KEY (etf_symbol, date)
);

-- Stock metadata and sector assignments
CREATE TABLE stock_metadata (
    symbol VARCHAR PRIMARY KEY REFERENCES stocks(symbol),
    sector VARCHAR,
    industry VARCHAR,
    market_cap_category VARCHAR,  -- large/mid/small cap
    is_sp500 BOOLEAN,
    country VARCHAR
);
```

These enhancements will enable:

- **Cross-sectional features**: Sector relative strength, market breadth indicators
- **Fundamental data**: Earnings, analyst ratings, company metadata
- **Alternative data**: News sentiment, social media signals, options flow
- **Feature versioning**: Track feature definitions for model reproducibility

### 2. Data Pipeline

**Data Ingestion Strategy: Batching and Rate Limit Management**

Given API rate limits, intelligent batching and caching are critical for cost-effective data retrieval.

**Key Principle**: *Always batch API requests when possible to minimize API calls and respect rate limits.*

#### AlphaVantage API Batching Strategies

**1. Batch Daily Updates (Most Efficient)**

Instead of requesting each stock individually, batch updates intelligently:

```python
class BatchedIngestionStrategy:
    """
    Intelligent batching strategy for data ingestion.
    Goal: Minimize API calls while keeping data fresh.
    """

    def ingest_universe_daily(self, symbols: list[str]) -> None:
        """
        Daily update strategy for 3,500 stocks.

        AlphaVantage free tier: 25 requests/day → NOT viable for daily updates
        AlphaVantage premium ($50/mo): 75 req/min → 4,500 req/hour

        Strategy:
        - Use TIME_SERIES_DAILY_ADJUSTED with timeframe=compact (100 days)
        - Only need 1 call per symbol per day for price data
        - Batch technical indicators: 1 call gets multiple timeframes
        """

        # Group symbols into batches respecting rate limits
        batch_size = 75  # requests per minute (premium tier)

        for batch in self._chunk_symbols(symbols, batch_size):
            # Fetch OHLCV data (1 request per symbol)
            ohlcv_data = self._fetch_batch_ohlcv(batch)

            # Fetch technical indicators in batch
            # AlphaVantage allows multiple indicators per call
            indicators = self._fetch_batch_indicators(batch)

            # Store in database
            self._store_batch(ohlcv_data, indicators)

            # Rate limit: wait 60 seconds before next batch
            if batch != batches[-1]:
                time.sleep(60)

    def _fetch_batch_indicators(self, symbols: list[str]) -> dict:
        """
        Fetch multiple indicators per symbol efficiently.

        AlphaVantage returns all indicator history in one call,
        so we only need 1 call per (symbol, indicator) pair.
        """
        # Instead of calling RSI, MACD, ADX separately (3 calls),
        # fetch once and cache, then compute others locally if needed
        pass
```

**2. Historical Backfill (One-Time)**

For initial data load (10 years of history):

```python
class HistoricalBackfill:
    """
    One-time backfill strategy for 3,500 stocks × 10 years.

    Requirements:
    - ~3,500 API calls (1 per symbol for full history)
    - AlphaVantage TIME_SERIES_DAILY_ADJUSTED with timeframe=full
    - Each call returns up to 20 years of data

    Premium tier (75 req/min):
    - 3,500 calls ÷ 75 req/min = 47 minutes total
    - Run once during initial setup
    """

    def backfill_all_symbols(self, symbols: list[str]) -> None:
        """
        Fetch complete history for all symbols.
        Respects rate limits and caches results.
        """
        for i, symbol in enumerate(symbols):
            # Check if already cached
            if self._is_cached(symbol):
                continue

            # Fetch full history (timeframe=full)
            data = self.api.get_daily_adjusted(
                symbol=symbol,
                outputsize='full'  # Returns 20+ years
            )

            # Store in database
            self._store_historical(symbol, data)

            # Rate limiting
            if (i + 1) % 75 == 0:  # Every 75 requests
                print(f"Processed {i+1}/{len(symbols)} symbols. Waiting 60s...")
                time.sleep(60)

        print(f"Backfill complete: {len(symbols)} symbols")
```

**3. Incremental Updates (Daily Production)**

After initial backfill, only fetch new data:

```python
class IncrementalUpdate:
    """
    Daily update strategy: only fetch data newer than last_update.

    For 3,500 stocks:
    - Compact mode (100 days) sufficient for daily updates
    - 3,500 calls total
    - With premium tier: ~47 minutes/day
    """

    def update_since_last_run(self, symbols: list[str]) -> None:
        """
        Fetch only new data since last successful update.
        """
        last_update = self.db.get_last_update_timestamp()

        # AlphaVantage compact mode returns last 100 days
        # Filter in-memory to only insert new rows
        for batch in self._chunk_symbols(symbols, 75):
            data = self._fetch_batch_compact(batch)

            # Filter: only insert rows newer than last_update
            new_rows = [
                row for row in data
                if row['date'] > last_update
            ]

            if new_rows:
                self.db.insert_batch(new_rows)

            time.sleep(60)  # Rate limit
```

**4. Smart Caching Strategy**

Minimize redundant API calls:

```python
class CachingLayer:
    """
    Multi-level caching to reduce API calls.

    Levels:
    1. Database cache (primary storage)
    2. File system cache (raw API responses)
    3. In-memory cache (current session)
    """

    def get_or_fetch(self, symbol: str, date: str) -> dict:
        """
        Check multiple cache levels before hitting API.
        """
        # Level 1: Check database
        db_result = self.db.query(symbol, date)
        if db_result:
            return db_result

        # Level 2: Check file cache (JSON responses)
        file_path = f"data/raw/{symbol}_{date}.json"
        if os.path.exists(file_path):
            with open(file_path) as f:
                return json.load(f)

        # Level 3: Fetch from API (last resort)
        api_result = self.api.fetch(symbol, date)

        # Cache for future use
        self._cache_to_file(file_path, api_result)
        self.db.insert(api_result)

        return api_result
```

**5. Cross-Sectional Batching**

For cross-sectional features, batch by sector:

```python
class SectorBatchStrategy:
    """
    Batch sector-level metrics to reduce calls.

    Example: Instead of fetching 500 tech stocks individually,
    fetch XLK (tech sector ETF) once.
    """

    def update_sector_etfs(self) -> None:
        """
        Fetch 11 sector ETFs instead of thousands of stocks.

        Sectors: XLK, XLV, XLF, XLE, XLY, XLP, XLI, XLB, XLRE, XLC, XLU
        Cost: 11 API calls vs 3,500
        """
        sector_etfs = ['XLK', 'XLV', 'XLF', 'XLE', 'XLY',
                       'XLP', 'XLI', 'XLB', 'XLRE', 'XLC', 'XLU']

        for etf in sector_etfs:
            data = self.api.get_daily_adjusted(etf, outputsize='compact')
            self.db.insert_sector_etf(etf, data)

        # No rate limit needed (< 1 minute of calls)
```

#### Rate Limit Monitoring

```python
class RateLimitTracker:
    """
    Track API usage to avoid hitting limits.
    """

    def __init__(self):
        self.calls_this_minute = 0
        self.calls_today = 0
        self.last_call_time = None

    def before_request(self) -> None:
        """
        Check rate limits before making request.
        """
        now = time.time()

        # Reset minute counter
        if self.last_call_time and (now - self.last_call_time) > 60:
            self.calls_this_minute = 0

        # Wait if at limit
        if self.calls_this_minute >= 75:  # Premium tier
            wait_time = 60 - (now - self.last_call_time)
            if wait_time > 0:
                time.sleep(wait_time)
            self.calls_this_minute = 0

        self.calls_this_minute += 1
        self.calls_today += 1
        self.last_call_time = now
```

#### Cost Analysis: Batching vs Individual Calls

| Approach | API Calls/Day | Time Required | Premium Cost/Month |
|----------|---------------|---------------|--------------------|
| **Individual (naive)** | 3,500 stocks × 5 indicators = 17,500 | 4 hours | $50 |
| **Batched (smart)** | 3,500 stocks × 1 call = 3,500 | 47 minutes | $50 |
| **Cached + Batched** | Only changed stocks (~100/day) | 1-2 minutes | $50 |
| **Sector ETFs only** | 11 ETFs × 1 call = 11 | <1 minute | $50 |

**Recommendation**: Use batched approach with aggressive caching. After initial backfill, daily updates should take <5 minutes.

#### Implementation Checklist

- [ ] Implement `BatchedIngestionStrategy` with rate limiting
- [ ] Add `CachingLayer` with file and DB caching
- [ ] Create `RateLimitTracker` to prevent API throttling
- [ ] Use `outputsize=full` for initial backfill (one-time)
- [ ] Use `outputsize=compact` for daily updates
- [ ] Store raw API responses in `data/raw/` for debugging
- [ ] Log all API calls with timestamps for monitoring
- [ ] Implement exponential backoff for API errors
- [ ] Add retry logic for transient failures
- [ ] Monitor API quota usage in dashboard

**Data Quality & Validation:**

Data from external APIs can be incomplete, incorrect, or inconsistent. Robust validation is critical:

**Validation Rules:**
```python
class DataValidator:
    @staticmethod
    def validate_ohlcv(row):
        """Validate OHLCV data integrity"""
        checks = {
            'positive_prices': row['open'] > 0 and row['high'] > 0 and row['low'] > 0 and row['close'] > 0,
            'high_is_highest': row['high'] >= max(row['open'], row['close']),
            'low_is_lowest': row['low'] <= min(row['open'], row['close']),
            'valid_volume': row['volume'] >= 0,
            'reasonable_range': (row['high'] / row['low']) < 1.5,  # No 50%+ intraday moves
        }
        return all(checks.values()), checks

    @staticmethod
    def validate_technical_indicator(value, name, expected_range):
        """Validate technical indicators are in expected ranges"""
        ranges = {
            'rsi': (0, 100),
            'adx': (0, 100),
            'stoch_k': (0, 100),
            'stoch_d': (0, 100),
        }
        if name in ranges:
            min_val, max_val = ranges[name]
            return min_val <= value <= max_val
        return True  # No range check for other indicators

# On ingestion
for row in api_response:
    is_valid, checks = DataValidator.validate_ohlcv(row)
    if not is_valid:
        logger.warning(f"Invalid OHLCV data for {symbol} on {date}: {checks}")
        # Store in quarantine table for review
        db.insert_quarantine(row, checks)
        continue
    db.insert_stock_prices(row)
```

**Handling Missing Data:**
- **Missing dates**: Detect gaps in time series, log warnings
- **Forward fill**: Use previous valid value for up to 5 trading days
- **Drop if stale**: If >5 days missing, exclude from training for that period
- **Indicator gaps**: If technical indicator missing but OHLCV present, compute locally or skip

**Handling Delisted Stocks:**
```python
# Track stock lifecycle
CREATE TABLE stock_metadata (
    symbol VARCHAR PRIMARY KEY,
    name VARCHAR,
    sector VARCHAR,
    first_seen DATE,
    last_seen DATE,
    status VARCHAR,  -- 'active', 'delisted', 'merged', 'symbol_change'
    notes TEXT
);

# On ingestion failure
if api_response.status == 404:
    db.update_stock_status(symbol, 'delisted', last_seen=today)
    logger.info(f"{symbol} marked as delisted")

# During training
active_stocks = db.get_stocks_where(status='active')
```

**Data Anomaly Detection:**
- **Circuit breaker**: Flag stocks with >20% single-day moves for review
- **Volume spikes**: Flag volume >10× average for review (potential stock splits, special events)
- **Correlation checks**: If AAPL drops 50% but NASDAQ flat, likely data error

**Extensible Feature Architecture:**

The system uses a **Feature Registry Pattern** to support adding new data sources without changing model code:

```python
# Feature providers are pluggable modules
class FeatureProvider(Protocol):
    def get_features(self, symbol: str, as_of: date) -> Dict[str, float]
    def get_feature_names(self) -> list[str]
    def requires_update(self, symbol: str, as_of: date) -> bool
```

**Phase 1 Providers (Core):**
- `OHLCVProvider`: Basic price/volume data
- `TechnicalIndicatorProvider`: RSI, ADX, SMA, EMA, MACD, etc.

**Future Providers (Extensible):**
- `MarketContextProvider`: VIX, SPY, market breadth, put/call ratio
- `IndustryTrendProvider`: Sector performance, relative strength
- `EarningsProvider`: Earnings dates, surprises, sentiment scores
- `SentimentProvider`: News/social sentiment, article counts
- `AlternativeDataProvider`: Options flow, insider trades, etc.

**Key Benefits:**
- Add new features by creating a provider class and enabling in config
- Model automatically resizes input layer based on enabled providers
- Feature versioning tracks which providers were used for each model
- No code changes to model architecture when adding features

**Configuration:**
```yaml
# config/features.yaml
feature_providers:
  - name: ohlcv
    enabled: true
  - name: technical_indicators
    enabled: true
  - name: market_context  # VIX, etc.
    enabled: false  # Enable when data available
  - name: earnings
    enabled: false  # Enable when data available
```

**Components:**
1. **Ingestion Service** (`src/data/ingestion/`)
   - Modular API clients (AlphaVantage, custom sources)
   - Rate limiting and retry logic
   - Incremental updates (fetch only new data)
   - Data validation and cleaning
   - Per-provider ingestion modules

**AlphaVantage Rate Limit Management:**

AlphaVantage API tiers have strict rate limits that impact data ingestion strategy:

| Tier | Cost | Rate Limit | Daily Limit | Notes |
|------|------|------------|-------------|-------|
| Free | $0 | 5 calls/min | 500 calls/day | Suitable for development only |
| Premium | $50/month | 75 calls/min | 15,000 calls/day | Recommended for production |
| Enterprise | Custom | Custom | Custom | For institutional use |

**Ingestion Strategy for 3,500 Stocks:**

```python
# Each stock requires multiple API calls:
# - TIME_SERIES_DAILY (1 call)
# - RSI (1 call)
# - ADX (1 call)
# - SMA (1 call for each period, or batch)
# - EMA, MACD, BBANDS, STOCH (4 more calls)
# Total: ~8-10 calls per stock per full refresh

# Full ingestion cost:
# 3,500 stocks × 8 calls = 28,000 API calls

# With Premium tier (75 calls/min):
# 28,000 calls / 75 calls/min = 373 minutes ≈ 6.2 hours

# Solution: Incremental updates (only fetch new dates)
# Daily updates: 3,500 stocks × 1 call (just latest date) = 3,500 calls
# Time: 3,500 / 75 = 47 minutes
```

**Rate Limiter Implementation:**

```python
class RateLimitedAPIClient:
    def __init__(self, api_key: str, tier: str = 'premium'):
        self.api_key = api_key
        self.limits = {
            'free': {'calls_per_min': 5, 'calls_per_day': 500},
            'premium': {'calls_per_min': 75, 'calls_per_day': 15000},
        }
        self.tier = tier
        self.call_history = []  # Track API calls

    def fetch(self, endpoint: str, params: dict):
        """Fetch with automatic rate limiting"""
        self._enforce_rate_limit()

        response = requests.get(
            f"https://www.alphavantage.co/query",
            params={**params, 'apikey': self.api_key}
        )

        self.call_history.append(datetime.now())
        return response.json()

    def _enforce_rate_limit(self):
        """Sleep if necessary to respect rate limits"""
        now = datetime.now()

        # Check minute limit
        recent_calls = [t for t in self.call_history if (now - t).seconds < 60]
        if len(recent_calls) >= self.limits[self.tier]['calls_per_min']:
            sleep_time = 60 - (now - recent_calls[0]).seconds
            logger.info(f"Rate limit approaching, sleeping {sleep_time}s")
            time.sleep(sleep_time)

        # Check daily limit
        today_calls = [t for t in self.call_history if t.date() == now.date()]
        if len(today_calls) >= self.limits[self.tier]['calls_per_day']:
            raise RateLimitError("Daily API limit reached")

# Batch ingestion with progress tracking
class BatchIngestionManager:
    def ingest_universe(self, symbols: list[str], full_refresh: bool = False):
        """Ingest data for all symbols with rate limiting"""
        api_client = RateLimitedAPIClient(api_key=config.ALPHAVANTAGE_KEY, tier='premium')

        total_calls = len(symbols) * (8 if full_refresh else 1)
        estimated_time = total_calls / self.limits['calls_per_min']

        logger.info(f"Starting ingestion: {len(symbols)} symbols, {total_calls} API calls, ~{estimated_time:.0f} min")

        for i, symbol in enumerate(symbols):
            try:
                if full_refresh:
                    self._fetch_all_data(symbol, api_client)
                else:
                    self._fetch_incremental(symbol, api_client)

                if (i + 1) % 100 == 0:
                    logger.info(f"Progress: {i+1}/{len(symbols)} symbols complete")

            except RateLimitError:
                logger.error(f"Rate limit reached at symbol {i+1}. Resume tomorrow.")
                return False

        logger.info("Ingestion complete")
        return True
```

**Cost-Benefit Analysis:**

| Scenario | Tier | Monthly Cost | Full Refresh Time | Daily Update Time |
|----------|------|--------------|-------------------|-------------------|
| Development (10 stocks) | Free | $0 | 2 min | <1 min |
| Proof of Concept (100 stocks) | Free | $0 | 20 min | 2 min |
| Production (3,500 stocks) | Premium | $50 | 6.2 hours | 47 min |

**Recommendation**:
- **Development/PoC**: Use free tier for initial 10-100 stocks
- **Production**: Upgrade to Premium tier ($50/month) once scaling beyond 500 stocks
- **Optimization**: Cache technical indicators locally to reduce daily API calls to just OHLCV updates

2. **Feature Engineering** (`src/features/`)
   - **Feature Registry**: Central registry of all feature providers
   - **Feature Providers**: Pluggable modules for different data sources
   - **Feature Versioning**: Track which features each model uses
   - Normalize/standardize indicators
   - Calculate derived features (slopes, relative values from Folly insights)
   - Handle missing data and outliers
   - **Cross-Sectional Features**: Compute relative metrics across stocks

**Cross-Sectional Feature Computation:**

These features capture how a stock compares to its peers, sector, and the overall market:

```python
class CrossSectionalFeatureProvider(FeatureProvider):
    """Compute features that compare stocks to each other"""

    def __init__(self, db, sector_mapping: dict[str, str]):
        self.db = db
        self.sector_mapping = sector_mapping  # symbol → sector
        self._cache = {}

    def get_features(self, symbol: str, as_of: date) -> Dict[str, float]:
        """
        Compute cross-sectional features for a stock on a given date.

        IMPORTANT: This requires data for ALL stocks in the universe,
        not just the target stock. Cross-sectional features are computed
        by comparing the stock to its peers.
        """
        # Get stock's own data
        stock_data = self.db.get_stock_data(symbol, as_of, lookback=60)

        # Get sector peers
        sector = self.sector_mapping[symbol]
        peer_symbols = [s for s, sec in self.sector_mapping.items() if sec == sector and s != symbol]
        peer_data = {s: self.db.get_stock_data(s, as_of, lookback=60) for s in peer_symbols}

        # Get market benchmark (SPY)
        spy_data = self.db.get_stock_data('SPY', as_of, lookback=60)

        # Get sector ETF (e.g., XLK for tech)
        sector_etf = self._get_sector_etf(sector)
        sector_data = self.db.get_stock_data(sector_etf, as_of, lookback=60)

        features = {}

        # 1. Relative Returns (vs Market)
        stock_return_7d = self._calculate_return(stock_data, days=7)
        spy_return_7d = self._calculate_return(spy_data, days=7)
        features['relative_return_spy_7d'] = stock_return_7d - spy_return_7d
        features['relative_return_spy_30d'] = (
            self._calculate_return(stock_data, days=30) -
            self._calculate_return(spy_data, days=30)
        )

        # 2. Relative Returns (vs Sector)
        sector_return_7d = self._calculate_return(sector_data, days=7)
        features['relative_return_sector_7d'] = stock_return_7d - sector_return_7d
        features['relative_return_sector_30d'] = (
            self._calculate_return(stock_data, days=30) -
            self._calculate_return(sector_data, days=30)
        )

        # 3. Sector Percentile Ranking
        # Where does this stock rank within its sector?
        peer_returns_7d = [self._calculate_return(data, days=7) for data in peer_data.values()]
        features['sector_rank_7d'] = self._percentile_rank(stock_return_7d, peer_returns_7d)

        peer_returns_30d = [self._calculate_return(data, days=30) for data in peer_data.values()]
        features['sector_rank_30d'] = self._percentile_rank(stock_return_30d, peer_returns_30d)

        # 4. Beta to Market
        features['beta_spy_60d'] = self._calculate_beta(stock_data, spy_data, window=60)
        features['beta_sector_60d'] = self._calculate_beta(stock_data, sector_data, window=60)

        # 5. Correlation to Peers
        peer_correlations = [
            self._calculate_correlation(stock_data, peer, window=60)
            for peer in peer_data.values()
        ]
        features['avg_peer_correlation_60d'] = np.mean(peer_correlations)

        # 6. Relative Volume
        stock_volume_avg = stock_data['volume'].tail(20).mean()
        peer_volume_avg = np.mean([data['volume'].tail(20).mean() for data in peer_data.values()])
        features['relative_volume_vs_sector'] = stock_volume_avg / peer_volume_avg if peer_volume_avg > 0 else 1.0

        # 7. Relative RSI
        stock_rsi = stock_data['rsi_14'].iloc[-1]
        peer_rsi_median = np.median([data['rsi_14'].iloc[-1] for data in peer_data.values()])
        features['relative_rsi_vs_sector'] = stock_rsi - peer_rsi_median

        # 8. Divergence from Sector
        # Is the stock moving independently from its sector?
        stock_momentum = self._calculate_return(stock_data, days=5)
        sector_momentum = self._calculate_return(sector_data, days=5)
        features['divergence_from_sector_5d'] = abs(stock_momentum - sector_momentum)

        return features

    def _percentile_rank(self, value: float, peer_values: list[float]) -> float:
        """Return percentile rank (0-1) of value among peers"""
        return sum(1 for p in peer_values if value > p) / len(peer_values) if peer_values else 0.5

    def _calculate_beta(self, stock_data: pd.DataFrame, market_data: pd.DataFrame, window: int) -> float:
        """Calculate beta (sensitivity to market moves)"""
        stock_returns = stock_data['close'].pct_change().tail(window)
        market_returns = market_data['close'].pct_change().tail(window)
        covariance = np.cov(stock_returns, market_returns)[0, 1]
        market_variance = np.var(market_returns)
        return covariance / market_variance if market_variance > 0 else 1.0

    def _calculate_correlation(self, stock_data: pd.DataFrame, peer_data: pd.DataFrame, window: int) -> float:
        """Calculate correlation coefficient between stock and peer"""
        stock_returns = stock_data['close'].pct_change().tail(window)
        peer_returns = peer_data['close'].pct_change().tail(window)
        return np.corrcoef(stock_returns, peer_returns)[0, 1]

    def get_feature_names(self) -> list[str]:
        return [
            'relative_return_spy_7d',
            'relative_return_spy_30d',
            'relative_return_sector_7d',
            'relative_return_sector_30d',
            'sector_rank_7d',
            'sector_rank_30d',
            'beta_spy_60d',
            'beta_sector_60d',
            'avg_peer_correlation_60d',
            'relative_volume_vs_sector',
            'relative_rsi_vs_sector',
            'divergence_from_sector_5d',
        ]
```

**Efficient Implementation: Pre-Compute Sector Meta-Metrics**

Instead of computing peer comparisons on-the-fly for every stock (which would be O(N²) complexity), **pre-compute sector-level aggregates** daily:

```python
class SectorMetricsCalculator:
    """Pre-compute daily sector-level statistics for efficient cross-sectional features"""

    def compute_daily_sector_metrics(self, date: date, universe: list[str]):
        """
        Compute and store sector aggregates once per day.
        This is O(N) instead of O(N²) for individual peer comparisons.
        """
        # Get all stock data for this date
        all_data = {symbol: self.db.get_stock_data(symbol, date, lookback=60)
                    for symbol in universe}

        # Group by sector
        sectors = defaultdict(list)
        for symbol, data in all_data.items():
            sector = self.sector_mapping[symbol]
            sectors[sector].append((symbol, data))

        # Compute sector aggregates
        for sector, stocks in sectors.items():
            sector_metrics = {
                'date': date,
                'sector': sector,
                # Returns
                'return_7d_median': np.median([self._calc_return(d, 7) for _, d in stocks]),
                'return_7d_mean': np.mean([self._calc_return(d, 7) for _, d in stocks]),
                'return_30d_median': np.median([self._calc_return(d, 30) for _, d in stocks]),
                'return_30d_mean': np.mean([self._calc_return(d, 30) for _, d in stocks]),

                # Technical indicators
                'rsi_median': np.median([d['rsi_14'].iloc[-1] for _, d in stocks]),
                'rsi_mean': np.mean([d['rsi_14'].iloc[-1] for _, d in stocks]),
                'adx_median': np.median([d['adx_14'].iloc[-1] for _, d in stocks]),

                # Volume
                'volume_total': sum([d['volume'].tail(20).mean() for _, d in stocks]),
                'volume_median': np.median([d['volume'].tail(20).mean() for _, d in stocks]),

                # Dispersion (how much variation within sector?)
                'return_7d_std': np.std([self._calc_return(d, 7) for _, d in stocks]),
                'return_30d_std': np.std([self._calc_return(d, 30) for _, d in stocks]),

                # Breadth (% of stocks positive)
                'pct_positive_7d': sum(1 for _, d in stocks if self._calc_return(d, 7) > 0) / len(stocks),
                'pct_positive_30d': sum(1 for _, d in stocks if self._calc_return(d, 30) > 0) / len(stocks),
            }

            # Store in database
            self.db.insert_sector_metrics(sector_metrics)

# Database schema for pre-computed metrics
CREATE TABLE sector_daily_metrics (
    sector VARCHAR,
    date DATE,
    return_7d_median DECIMAL,
    return_7d_mean DECIMAL,
    return_30d_median DECIMAL,
    return_30d_mean DECIMAL,
    rsi_median DECIMAL,
    rsi_mean DECIMAL,
    adx_median DECIMAL,
    volume_total BIGINT,
    volume_median BIGINT,
    return_7d_std DECIMAL,
    return_30d_std DECIMAL,
    pct_positive_7d DECIMAL,
    pct_positive_30d DECIMAL,
    PRIMARY KEY (sector, date)
);
```

**Then, cross-sectional features become simple lookups:**

```python
class CrossSectionalFeatureProvider(FeatureProvider):
    """Compute features using pre-computed sector meta-metrics"""

    def get_features(self, symbol: str, as_of: date) -> Dict[str, float]:
        # Get stock's own data
        stock_data = self.db.get_stock_data(symbol, as_of, lookback=60)
        sector = self.sector_mapping[symbol]

        # FAST: Lookup pre-computed sector metrics (single query)
        sector_metrics = self.db.get_sector_metrics(sector, as_of)
        spy_data = self.db.get_stock_data('SPY', as_of, lookback=60)
        sector_etf_data = self.db.get_stock_data(self.sector_etf_map[sector], as_of, lookback=60)

        # Compute relative features
        stock_return_7d = self._calc_return(stock_data, 7)
        stock_return_30d = self._calc_return(stock_data, 30)

        features = {
            # Relative to market
            'relative_return_spy_7d': stock_return_7d - self._calc_return(spy_data, 7),
            'relative_return_spy_30d': stock_return_30d - self._calc_return(spy_data, 30),

            # Relative to sector ETF
            'relative_return_sector_7d': stock_return_7d - self._calc_return(sector_etf_data, 7),
            'relative_return_sector_30d': stock_return_30d - self._calc_return(sector_etf_data, 30),

            # Relative to sector PEERS (using pre-computed metrics)
            'relative_return_peers_7d': stock_return_7d - sector_metrics['return_7d_median'],
            'relative_return_peers_30d': stock_return_30d - sector_metrics['return_30d_median'],

            # Percentile rank (approximate using z-score)
            'sector_rank_7d': self._percentile_from_zscore(
                stock_return_7d,
                sector_metrics['return_7d_median'],
                sector_metrics['return_7d_std']
            ),

            # Relative technical indicators
            'relative_rsi_vs_sector': stock_data['rsi_14'].iloc[-1] - sector_metrics['rsi_median'],
            'relative_adx_vs_sector': stock_data['adx_14'].iloc[-1] - sector_metrics['adx_median'],

            # Relative volume
            'relative_volume_vs_sector': (
                stock_data['volume'].tail(20).mean() / sector_metrics['volume_median']
            ),

            # Sector context
            'sector_dispersion_7d': sector_metrics['return_7d_std'],  # High = stock-picking environment
            'sector_breadth_7d': sector_metrics['pct_positive_7d'],  # High = broad rally
        }

        return features

    def _percentile_from_zscore(self, value: float, median: float, std: float) -> float:
        """Approximate percentile rank from z-score (assuming normal distribution)"""
        if std == 0:
            return 0.5
        z_score = (value - median) / std
        # Convert z-score to percentile using cumulative distribution function
        from scipy.stats import norm
        return norm.cdf(z_score)
```

**Performance Comparison:**

| Approach | Complexity | Time for 3,500 stocks | Notes |
|----------|------------|----------------------|-------|
| **Naive** (compare each stock to all peers) | O(N²) | ~6 minutes | Too slow |
| **Batch** (load all, compute in memory) | O(N) | ~30 seconds | Memory intensive |
| **Meta-metrics** (pre-compute sectors) | O(N) | ~2 seconds | **Recommended** |

**Key Benefits:**
1. **Performance**: 180× faster than naive approach
2. **Consistency**: All stocks use same sector stats (no timing issues)
3. **Interpretability**: Sector metrics useful for analysis
4. **Storage**: Sector metrics table is tiny (~11 sectors × 2,520 days = 28K rows)
5. **Reusability**: Sector metrics useful for reporting, not just features

**Daily Pipeline:**
```
1. Ingest OHLCV for all stocks + SPY + sector ETFs (47 min with AlphaVantage Premium)
2. Compute sector meta-metrics (2 min)
3. Compute cross-sectional features per stock (2 min)
4. Generate predictions (4 sec)
Total: ~51 minutes/day
```

3. **Dataset Builder** (`src/dataset/`)
   - Dynamically collect features from enabled providers
   - Create train/validation/test splits
   - Rolling window generation
   - Label generation (actual returns over horizon)
   - PyTorch Dataset and DataLoader implementations
   - **Batch cross-sectional feature computation** (not per-stock)

### 3. Model Layer

**Critical Design Decision: Cross-Sectional vs Isolated Modeling**

**Current Challenge**: Treating each stock in isolation misses crucial information:
- **Relative Strength**: Is AAPL outperforming QQQ (NASDAQ ETF)?
- **Sector Effects**: Is the entire tech sector moving together?
- **Market Regime**: Are all stocks correlated (risk-on) or dispersed (stock-picking environment)?
- **Peer Comparison**: Is NVDA moving differently than AMD despite being competitors?

**Solution: Hybrid Architecture with Cross-Sectional Features**

The model should learn both **individual stock patterns** AND **relative positioning** across the universe.

**Recommended Architecture: Multi-Output Quantile Regression with Cross-Sectional Context**

```
Input Layer (Per Stock):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Stock-Specific Features (60 days × ~50 features)
  - OHLCV history
  - Technical indicators (RSI, ADX, etc.)
  - Volume patterns
        ↓
Cross-Sectional Features (60 days × ~20 features)
  - Relative return vs SPY
  - Relative return vs sector ETF
  - Rank within sector (percentile)
  - Beta to market
  - Correlation to sector peers
  - Volume relative to sector average
  - RSI relative to sector median
        ↓
Market Context Features (60 days × ~15 features)
  - VIX level
  - Market breadth (advance/decline)
  - Sector rotation signals
  - Put/call ratio
  - Market momentum (SPY trend)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        ↓
[Shared Feature Encoder - LSTM/TCN/Transformer]
  (Learns patterns across all feature types)
        ↓
[Shared Dense Layers]
        ↓
    ┌───┴───┬───────┐
    ↓       ↓       ↓
  7-day   30-day  90-day
  heads   heads   heads
    ↓       ↓       ↓
 [q10,   [q10,   [q10,
  q50,    q50,    q50,
  q90]    q90]    q90]
```

**Why This Approach?**
- **Relative Strength Matters**: A stock up 5% when the sector is up 8% is actually underperforming
- **Regime Awareness**: Same RSI=70 means different things in bull vs bear markets
- **Sector Correlation**: Tech stocks often move together; model should know this
- **Market Context**: Stock behavior differs in high-VIX vs low-VIX environments
- **Peer Comparison**: NVDA's move is more meaningful compared to AMD/INTC than in isolation

**Concrete Example: Why Cross-Sectional Features Matter**

Consider two scenarios for AAPL on the same day:

**Scenario A: Isolated Features Only**
```python
AAPL features:
  - 7d return: +5.0%
  - RSI: 68
  - Volume: 80M shares
  - ADX: 35

Model prediction (isolated): Strong bullish (expecting +8% in 30 days)
```

**Scenario B: With Cross-Sectional Context**
```python
AAPL features:
  - 7d return: +5.0%
  - RSI: 68
  - Volume: 80M shares
  - ADX: 35

Cross-sectional context:
  - SPY 7d return: +7.0%           → AAPL underperforming market by -2%
  - XLK (tech ETF) 7d return: +8.5% → AAPL underperforming sector by -3.5%
  - Sector rank: 15th percentile    → Bottom 15% of tech stocks
  - Relative RSI: -12 (sector median RSI = 80) → Less overbought than peers
  - Beta to SPY: 1.2                → Should have moved +8.4% given SPY move
  - VIX: 22 (elevated)              → Risk-off environment

Model prediction (with context): Weak/Neutral (expecting +2% in 30 days)
```

**Interpretation**:
- Without context: AAPL up 5% looks strong
- With context: AAPL is lagging badly during a tech rally, suggesting weakness
- The model should predict **lower** returns when a stock underperforms during sector strength
- This is exactly what the Folly system tried to capture with relative comparisons!

**Why Multi-Output?**
- Shared encoder learns cross-timeframe patterns (7-day trends often predict 30-day)
- More efficient than separate models
- Ensures consistency (e.g., can't predict strong 7-day up and strong 30-day down)
- Single model to train/deploy/monitor

**Why Quantile Regression?**
- Directly answers: "What's the likelihood of X% move?"
- Provides uncertainty bounds (not just point estimate)
- Robust to outliers (unlike MSE)
- Natural interpretation for signal strength

**Architecture Options (Encoder):**

**Option A: LSTM/GRU (Recommended Start)**
- Proven for time-series
- Captures sequential dependencies
- Interpretable hidden states
- Lower computational cost

**Option B: Temporal Convolutional Network (TCN)**
- Parallelizable (faster training)
- Good for pattern detection
- Less prone to vanishing gradients
- Fixed receptive field

**Option C: Transformer**
- Best for long-range dependencies
- Attention weights aid interpretability
- Higher computational cost
- May overfit on smaller datasets

**Training Strategy:**

**Loss Function**: Quantile Loss (Pinball Loss)
```python
def quantile_loss(predicted_quantile, actual_return, quantile):
    error = actual_return - predicted_quantile
    return torch.mean(torch.max(quantile * error, (quantile - 1) * error))

# Total loss combines all quantiles and horizons
loss = (
    quantile_loss(q10_7d, actual_7d, 0.10) +
    quantile_loss(q50_7d, actual_7d, 0.50) +
    quantile_loss(q90_7d, actual_7d, 0.90) +
    quantile_loss(q10_30d, actual_30d, 0.10) +
    # ... etc for all horizons
)
```

**Optimization**:
- Adam optimizer with learning rate scheduling (CosineAnnealing or ReduceLROnPlateau)
- Gradient clipping for stability

**Regularization**:
- Dropout in encoder and dense layers
- L2 weight decay
- Early stopping on validation loss

**Validation**:
- Walk-forward (expanding window) validation
- No future data leakage
- Separate validation set for each time period

**Evaluation Metrics**:
- **Quantile Coverage**: % of actual returns falling within [q10, q90] should be ~80%
- **Quantile Calibration**: q10 should actually be 10th percentile, etc.
- **Directional Accuracy**: Does sign of q50 match actual return sign?
- **Sharpe Ratio**: If trading on q50 predictions
- **Mean Quantile Loss**: Lower is better
- **Return Correlation**: Between q50 and actual returns

**Critical: Quantile Calibration Rigor**

The entire system depends on quantile predictions being accurate. If q10 isn't actually the 10th percentile, all downstream use cases (risk assessment, position sizing, stop-loss placement) become unreliable.

**Calibration Testing**:
```python
def test_quantile_calibration(predictions, actuals, quantile=0.10, tolerance=0.02):
    """
    Verify that quantile predictions are properly calibrated.

    For q10: Approximately 10% of actual returns should fall below predicted q10.
    Tolerance: ±2% (so 8-12% is acceptable for q10)
    """
    below_quantile = (actuals < predictions).mean()
    expected = quantile

    assert abs(below_quantile - expected) < tolerance, \
        f"Quantile {quantile} miscalibrated: {below_quantile:.1%} below (expected {expected:.1%})"

    return below_quantile

# Run calibration tests on validation set
calibration_results = {
    'q10_7d': test_quantile_calibration(val_preds['q10_7d'], val_actuals_7d, 0.10),
    'q50_7d': test_quantile_calibration(val_preds['q50_7d'], val_actuals_7d, 0.50),
    'q90_7d': test_quantile_calibration(val_preds['q90_7d'], val_actuals_7d, 0.90),
    # ... repeat for 30d and 90d
}
```

**Calibration Monitoring**:
- Track calibration metrics monthly on out-of-sample data
- If calibration drifts >3% from expected, trigger retraining
- Store calibration history in database for trend analysis

**Regime Change Detection**:

Models trained on 2015-2020 data may fail during:
- High volatility periods (2020 COVID crash, 2022 rate hikes)
- Sector rotations (tech boom/bust cycles)
- Market structure changes (algorithmic trading evolution)

**Mitigation Strategies**:
1. **Regular Retraining**: Monthly initially, weekly during volatile periods
2. **Rolling Training Window**: Use most recent 3-5 years (not full 10 years) to emphasize recent patterns
3. **Regime Indicators**: Include VIX, volatility metrics as features to help model adapt
4. **Performance Monitoring**: Track prediction error trends week-over-week
5. **Ensemble Models**: Train models on different time periods and average predictions

**Alternative: Separate Models Per Horizon**

If multi-output underperforms, train three separate models:
- `model_7d`: Optimized for short-term (may emphasize momentum features)
- `model_30d`: Optimized for medium-term (may emphasize trend features)
- `model_90d`: Optimized for long-term (may emphasize fundamental features)

**Decision criteria**: If validation loss for multi-output model is >10% worse than average of separate models, use separate models. Otherwise, prefer multi-output for efficiency.

**Model Versioning & Deployment Strategy:**

Managing multiple model versions in production is critical for safe deployments and A/B testing.

**Version Naming Convention:**
- **Format**: `v{major}.{minor}.{patch}`
- **Major**: Breaking changes to model architecture or features (v1 → v2)
- **Minor**: Feature additions or significant retraining (v1.0 → v1.1)
- **Patch**: Bug fixes or hyperparameter tweaks (v1.1.0 → v1.1.1)

**Model Artifact Storage:**
```
models/
├── v1.0.0/
│   ├── model.pth                    # PyTorch weights
│   ├── config.yaml                  # Hyperparameters
│   ├── features.json                # Feature snapshot (which providers enabled)
│   ├── metrics.json                 # Training/validation metrics
│   └── metadata.json                # Training date, dataset size, etc.
├── v1.1.0/
│   ├── model.pth
│   ├── config.yaml
│   ├── features.json                # Now includes VIX features
│   ├── metrics.json
│   └── metadata.json
└── production/
    └── current -> ../v1.0.0         # Symlink to active model
```

**Deployment Workflow:**

```python
# 1. Train new model version
$ Gefion train --version v1.1.0

# 2. Validate new model on holdout set
$ Gefion validate --version v1.1.0 --compare-to v1.0.0
# Output:
# v1.1.0 metrics:
#   - Quantile loss: 0.0234 (v1.0.0: 0.0245) ✓ 4.5% improvement
#   - Calibration q10: 10.2% (v1.0.0: 10.1%) ✓
#   - Directional accuracy: 57.3% (v1.0.0: 56.1%) ✓

# 3. A/B test in shadow mode (run both models, compare outputs)
$ Gefion shadow-mode --new-version v1.1.0 --baseline v1.0.0 --duration 7days
# Generate predictions with both models daily, log discrepancies

# 4. Promote to production if metrics improve
$ Gefion promote --version v1.1.0
# Updates symlink: production/current -> v1.1.0
```

**Backwards Compatibility:**

When features change between versions, ensure old models can still run:

```python
class ModelLoader:
    @staticmethod
    def load_model(version: str):
        """Load model with its specific feature configuration"""
        model_dir = f"models/{version}"

        # Load feature snapshot
        with open(f"{model_dir}/features.json") as f:
            feature_config = json.load(f)

        # Initialize feature registry with this version's config
        registry = FeatureRegistry.from_snapshot(feature_config)

        # Load model (input size matches feature count)
        model = QuantileModel(input_size=registry.feature_count())
        model.load_state_dict(torch.load(f"{model_dir}/model.pth"))

        return model, registry

# Usage: Can run v1.0.0 and v1.1.0 side-by-side
model_v1, registry_v1 = ModelLoader.load_model("v1.0.0")  # 50 features
model_v2, registry_v2 = ModelLoader.load_model("v1.1.0")  # 110 features (added VIX)
```

**Model Comparison in Production:**

```python
class ModelComparator:
    def compare_versions(self, symbol: str, date: date) -> dict:
        """Run multiple model versions on same input, compare outputs"""
        versions = ["v1.0.0", "v1.1.0"]
        results = {}

        for version in versions:
            model, registry = ModelLoader.load_model(version)
            features = registry.get_features(symbol, date)
            predictions = model.predict(features)
            results[version] = predictions

        # Calculate discrepancy
        q50_diff = abs(results["v1.1.0"]["30d"]["q50"] - results["v1.0.0"]["30d"]["q50"])

        return {
            "predictions": results,
            "q50_discrepancy_30d": q50_diff,
            "alert": q50_diff > 3.0  # Alert if >3% difference
        }
```

**Rollback Strategy:**

```python
# If v1.1.0 shows degraded performance in production
$ Gefion rollback --to v1.0.0
# Immediately updates symlink back to previous version
# Log incident for post-mortem
```

### 4. Inference Layer

**Real-time Signal Generation:**
1. Fetch latest data for symbol(s)
2. Preprocess features into model input format
3. Load trained quantile regression model
4. Generate predictions: 9 values per stock
   - 7-day: [q10, q50, q90]
   - 30-day: [q10, q50, q90]
   - 90-day: [q10, q50, q90]
5. Store predictions with timestamp
6. Return structured prediction object

**Signal Strength Calculation:**

Given a target return X% for horizon H days:

```python
def calculate_signal_strength(quantiles, target_return):
    """
    Convert quantile predictions to signal strength [-1, 1]

    Args:
        quantiles: dict with keys 'q10', 'q50', 'q90' (in %)
        target_return: float (in %)

    Returns:
        signal_strength: float in [-1, 1]
        confidence: float in [0, 1]
    """
    q10, q50, q90 = quantiles['q10'], quantiles['q50'], quantiles['q90']

    # Interpolate position within distribution
    if target_return < q10:
        # Very unlikely (below 10th percentile)
        signal_strength = -1.0
        confidence = (q10 - target_return) / (q10 - q50) if q10 != q50 else 0.5
    elif target_return < q50:
        # Below median
        signal_strength = -0.5 + 0.5 * (target_return - q10) / (q50 - q10)
        confidence = 0.7
    elif target_return < q90:
        # Above median
        signal_strength = 0.5 * (target_return - q50) / (q90 - q50)
        confidence = 0.7
    else:
        # Highly likely (above 90th percentile)
        signal_strength = 1.0
        confidence = (target_return - q90) / (q50 - q90) if q90 != q50 else 0.5

    return signal_strength, confidence

# Example usage:
# "What's the likelihood AAPL moves +$5 in 30 days?"
# Current price: $150 → Target: +3.33%
# Model predicts: 30-day quantiles = {q10: -2%, q50: 1.5%, q90: 6%}
strength, conf = calculate_signal_strength(
    {'q10': -2.0, 'q50': 1.5, 'q90': 6.0},
    target_return=3.33
)
# Result: strength ≈ 0.37 (moderately positive), confidence ≈ 0.7
# Interpretation: "Target move is likely, falls in 60th-70th percentile"
```

**Query Interface:**

```python
# Use case 1: What are the return expectations?
result = predictor.predict('AAPL')
print(result)
# Output:
# {
#   'symbol': 'AAPL',
#   'as_of': '2025-01-15',
#   '7d':  {'q10': -1.2%, 'q50': 0.8%,  'q90': 3.5%},
#   '30d': {'q10': -3.0%, 'q50': 2.5%,  'q90': 8.0%},
#   '90d': {'q10': -8.0%, 'q50': 7.0%,  'q90': 20.0%}
# }

# Use case 2: What's likelihood of specific move?
signal = predictor.evaluate_move(
    symbol='AAPL',
    target_price=155,  # Current: $150
    horizon_days=30
)
# Output: SignalStrength(strength=0.37, confidence=0.7, percentile=65)

# Use case 3: Screen for opportunities
opportunities = predictor.screen(
    symbols=['AAPL', 'MSFT', 'GOOGL'],
    min_median_return=2.0,  # q50 > 2%
    horizon_days=30
)
```

**Backtesting Engine:**
- Walk-forward simulation with rolling retraining
- Compare predicted quantiles to actual returns
- Calculate calibration metrics (are quantiles accurate?)
- Evaluate trading strategies based on signals
- Generate performance reports and visualizations

**Production Monitoring & Alerting:**

Once deployed, continuous monitoring ensures the model remains accurate and detects degradation early.

**Key Metrics to Monitor:**

1. **Prediction Metrics (Daily)**:
   - Quantile calibration drift (weekly rolling window)
   - Directional accuracy (% where sign(q50) matches sign(actual_return))
   - Mean absolute error for q50 predictions
   - Prediction volume (are all stocks being predicted?)

2. **System Health (Real-time)**:
   - Inference latency (should be <100ms per stock)
   - Data ingestion success rate (% of stocks updated daily)
   - Feature extraction failures
   - Database query performance

3. **Data Quality (Daily)**:
   - Missing data rate (% of expected data points)
   - Anomaly detection triggers (circuit breakers)
   - API rate limit usage (% of AlphaVantage quota)

**Monitoring Infrastructure:**

```python
# Store metrics in time-series table
CREATE TABLE monitoring_metrics (
    metric_name VARCHAR,
    timestamp TIMESTAMP,
    value DECIMAL,
    metadata JSON,
    PRIMARY KEY (metric_name, timestamp)
);

class MetricsCollector:
    def record_prediction_batch(self, predictions, actuals=None):
        """Record metrics after daily prediction run"""
        metrics = {
            'prediction_count': len(predictions),
            'avg_inference_time_ms': self.calculate_avg_inference_time(),
            'feature_extraction_failures': self.count_failures(),
        }

        # If actuals available (backtesting or delayed validation)
        if actuals:
            metrics.update({
                'calibration_q10_7d': self.calculate_calibration(predictions, actuals, 'q10', '7d'),
                'calibration_q50_7d': self.calculate_calibration(predictions, actuals, 'q50', '7d'),
                'calibration_q90_7d': self.calculate_calibration(predictions, actuals, 'q90', '7d'),
                'directional_accuracy_7d': self.calculate_directional_accuracy(predictions, actuals, '7d'),
            })

        for metric_name, value in metrics.items():
            db.insert_metric(metric_name, datetime.now(), value)

    def calculate_calibration(self, predictions, actuals, quantile, horizon):
        """Calculate what % of actuals fall below predicted quantile"""
        preds = [p[horizon][quantile] for p in predictions.values()]
        acts = [actuals[sym][horizon] for sym in predictions.keys()]
        below = sum(a < p for a, p in zip(acts, preds)) / len(acts)
        return below
```

**Alerting Rules:**

```python
class AlertManager:
    ALERT_THRESHOLDS = {
        # Calibration drift
        'calibration_q10_drift': {'warning': 0.03, 'critical': 0.05},  # ±3% warning, ±5% critical
        'calibration_q50_drift': {'warning': 0.05, 'critical': 0.08},
        'calibration_q90_drift': {'warning': 0.03, 'critical': 0.05},

        # Performance degradation
        'directional_accuracy_drop': {'warning': 0.52, 'critical': 0.50},  # Below 52% warning, 50% critical

        # System health
        'prediction_failures': {'warning': 0.05, 'critical': 0.10},  # >5% failures
        'inference_latency_ms': {'warning': 200, 'critical': 500},
        'data_ingestion_failures': {'warning': 0.10, 'critical': 0.20},
    }

    def check_alerts(self):
        """Run daily after predictions complete"""
        # Get latest metrics
        metrics = db.get_latest_metrics()

        # Check calibration (compare to expected values)
        expected = {'q10': 0.10, 'q50': 0.50, 'q90': 0.90}
        for quantile, exp_val in expected.items():
            actual_val = metrics[f'calibration_{quantile}_7d']
            drift = abs(actual_val - exp_val)

            if drift > self.ALERT_THRESHOLDS[f'calibration_{quantile}_drift']['critical']:
                self.send_alert('CRITICAL', f'{quantile} calibration drift: {drift:.1%}')
            elif drift > self.ALERT_THRESHOLDS[f'calibration_{quantile}_drift']['warning']:
                self.send_alert('WARNING', f'{quantile} calibration drift: {drift:.1%}')

        # Check directional accuracy
        if metrics['directional_accuracy_7d'] < self.ALERT_THRESHOLDS['directional_accuracy_drop']['critical']:
            self.send_alert('CRITICAL', f'Directional accuracy dropped to {metrics["directional_accuracy_7d"]:.1%}')

        # Check system health
        if metrics['prediction_failures'] > self.ALERT_THRESHOLDS['prediction_failures']['warning']:
            self.send_alert('WARNING', f'Prediction failure rate: {metrics["prediction_failures"]:.1%}')

    def send_alert(self, level: str, message: str):
        """Send alert via email/Slack/etc"""
        logger.log(level, message)
        # TODO: Integrate with notification system (email, Slack, PagerDuty)
```

**Dashboard Metrics (Weekly Review):**

```python
# Generate weekly performance report
$ Gefion report --period last-7-days

# Output:
# ===== Gefion Weekly Report (2025-01-08 to 2025-01-14) =====
#
# Prediction Performance:
#   - 7-day directional accuracy: 56.3% (target: >55%)  ✓
#   - 30-day directional accuracy: 57.1% (target: >55%)  ✓
#   - 90-day directional accuracy: 54.8% (target: >55%)  ⚠
#
# Calibration:
#   - q10 (7d):  9.8% below predicted (expected: 10%)   ✓
#   - q50 (7d): 51.2% below predicted (expected: 50%)   ✓
#   - q90 (7d): 89.3% below predicted (expected: 90%)   ✓
#
# System Health:
#   - Predictions generated: 3,487 / 3,500 stocks (99.6%)  ✓
#   - Avg inference time: 0.8ms per stock  ✓
#   - Data ingestion success: 98.2%  ✓
#
# Alerts:
#   - 2 warnings (data quality issues for TSLA, NVDA on 2025-01-10)
#   - 0 critical alerts
#
# Recommendation: 90-day accuracy slightly below target. Consider retraining.
```

**Automated Retraining Triggers & Warm-Start Support:**

```python
class RetrainingScheduler:
    def should_retrain(self):
        """Determine if model should be retrained and which strategy to use"""
        # Get performance metrics from last 30 days
        metrics = db.get_metrics(period='30d')

        triggers = {
            'scheduled': self.is_monthly_retrain_due(),
            'calibration_drift': max(metrics['calibration_drifts']) > 0.05,
            'accuracy_drop': metrics['directional_accuracy_7d'] < 0.52,
            'new_features': self.has_new_features_enabled(),
            'regime_change': self.detect_regime_change(),
        }

        if not any(triggers.values()):
            return False, None, 'SKIP'

        # Determine retraining strategy
        if triggers['regime_change'] or triggers['new_features']:
            strategy = 'FULL_RETRAIN'  # Start from scratch (35 min)
        else:
            strategy = 'WARM_START'     # Fine-tune from existing weights (3-5 min)

        logger.info(f"Retraining triggered: {triggers}, strategy: {strategy}")
        return True, triggers, strategy

    def is_monthly_retrain_due(self):
        """Check if 30 days since last training"""
        last_train = db.get_last_training_date()
        return (datetime.now() - last_train).days >= 30

    def detect_regime_change(self):
        """Detect major market regime changes requiring full retrain"""
        # Major volatility spike
        vix = db.get_latest_vix()
        vix_30d_avg = db.get_vix_average(days=30)
        if vix > 40 or (vix / vix_30d_avg) > 2.0:
            logger.warning(f"Regime change detected: VIX spike {vix}")
            return True

        # Market crash (SPY down >10% in 5 days)
        spy_return_5d = db.get_return('SPY', days=5)
        if spy_return_5d < -0.10:
            logger.warning(f"Regime change detected: Market crash {spy_return_5d:.1%}")
            return True

        return False
```

**Warm-Start Retraining Implementation:**

For routine monthly updates, use warm-start to fine-tune the existing model instead of training from scratch. This is 7-10× faster and preserves learned patterns while adapting to new data.

```python
class ModelTrainer:
    def train(self, strategy: str = 'FULL_RETRAIN', parent_version: str = None):
        """
        Train model with specified strategy

        Args:
            strategy: 'FULL_RETRAIN' (from scratch) or 'WARM_START' (fine-tune)
            parent_version: Model version to start from (required for WARM_START)

        Returns:
            new_version: Version string of newly trained model
        """
        if strategy == 'FULL_RETRAIN':
            # Initialize model from scratch with random weights
            model = QuantileModel(input_size=self.feature_count)
            learning_rate = 0.001
            epochs = 50
            logger.info("Training from scratch (50 epochs, LR=0.001)")

        elif strategy == 'WARM_START':
            # Load existing model weights as starting point
            if not parent_version:
                parent_version = db.get_current_model_version()

            logger.info(f"Loading weights from {parent_version} for warm-start")
            model = QuantileModel.load(f'models/{parent_version}/model.pth')

            # Use lower learning rate and fewer epochs for fine-tuning
            learning_rate = 0.0001  # 10× lower than initial training
            epochs = 5              # 10× fewer epochs
            logger.info("Fine-tuning existing model (5 epochs, LR=0.0001)")

        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        # Prepare dataset (same for both strategies)
        dataset = self.prepare_dataset()
        train_loader, val_loader = self.create_dataloaders(dataset)

        # Training loop
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode='min', patience=5, factor=0.5
        )

        best_val_loss = float('inf')
        for epoch in range(epochs):
            # Training
            train_loss = self._train_epoch(model, train_loader, optimizer)

            # Validation
            val_loss = self._validate_epoch(model, val_loader)
            scheduler.step(val_loss)

            # Early stopping for warm-start (if loss increases)
            if strategy == 'WARM_START' and val_loss > best_val_loss * 1.1:
                logger.warning(f"Warm-start validation loss increased, stopping early at epoch {epoch}")
                break

            best_val_loss = min(best_val_loss, val_loss)
            logger.info(
                f"Epoch {epoch+1}/{epochs}: "
                f"train_loss={train_loss:.4f}, val_loss={val_loss:.4f}, "
                f"lr={optimizer.param_groups[0]['lr']:.6f}"
            )

        # Calculate calibration metrics on validation set
        calibration_metrics = self._calculate_calibration(model, val_loader)

        # Save new version
        new_version = self._increment_version(parent_version if strategy == 'WARM_START' else None)
        model_path = f'models/{new_version}/model.pth'
        os.makedirs(f'models/{new_version}', exist_ok=True)
        model.save(model_path)

        # Save training metadata
        self._save_training_metadata(
            version=new_version,
            strategy=strategy,
            parent_version=parent_version,
            epochs=epochs,
            learning_rate=learning_rate,
            final_train_loss=train_loss,
            final_val_loss=val_loss,
            calibration_metrics=calibration_metrics
        )

        logger.info(f"Model {new_version} trained successfully using {strategy}")
        return new_version

    def _increment_version(self, parent_version: str = None) -> str:
        """Generate new version string"""
        if parent_version is None:
            # Full retrain: increment major version
            latest = db.get_latest_version()
            if latest is None:
                return "v1.0.0"
            major, minor, patch = latest.lstrip('v').split('.')
            return f"v{int(major)+1}.0.0"
        else:
            # Warm-start: increment minor version
            major, minor, patch = parent_version.lstrip('v').split('.')
            return f"v{major}.{int(minor)+1}.0"

# Usage example:
scheduler = RetrainingScheduler()
should_retrain, triggers, strategy = scheduler.should_retrain()

if should_retrain:
    trainer = ModelTrainer()
    new_version = trainer.train(
        strategy=strategy,
        parent_version=db.get_current_model_version() if strategy == 'WARM_START' else None
    )

    # Validate before promoting
    validator = ModelValidator()
    current_version = db.get_current_model_version()

    if validator.compare_models(new_version, current_version):
        logger.info(f"Promoting {new_version} to production")
        db.promote_model(new_version)
    else:
        logger.warning(f"{new_version} did not outperform {current_version}, keeping current")
```

**Training Performance Comparison:**

| Strategy | Epochs | Learning Rate | Time (3,500 stocks) | Cost (AWS GPU) | When to Use |
|----------|--------|---------------|---------------------|----------------|-------------|
| **FULL_RETRAIN** | 50 | 0.001 | 35 min | $0.31 | Initial training, regime changes, new features, quarterly refresh |
| **WARM_START** | 5 | 0.0001 | 3-5 min | $0.03 | Monthly updates, drift correction, small data additions |

**Recommended Retraining Schedule:**

```
Daily:      Ingest new data (no retraining)
            └─ Monitor: calibration, accuracy, drift

Week 1-4:   No retraining

Month 1:    WARM_START retrain (3-5 min)
            └─ Triggered by: 30-day schedule

Month 2:    WARM_START retrain (3-5 min)

Month 3:    WARM_START retrain (3-5 min)

Quarter 1:  FULL_RETRAIN (35 min)
            └─ Periodic reset to avoid drift accumulation

Month 4-6:  WARM_START monthly

Quarter 2:  FULL_RETRAIN (35 min)

On Alert:   WARM_START (drift) or FULL_RETRAIN (regime change)
            └─ Triggered by: calibration drift >5%, accuracy drop, VIX spike
```

**Annual Compute Cost (3,500 stocks):**
- Monthly warm-starts: 12 × $0.03 = $0.36/year
- Quarterly full retrains: 4 × $0.31 = $1.24/year
- **Total: ~$1.60/year** (negligible compared to $600/year data costs)

**Database Schema for Training History:**

```sql
CREATE TABLE model_training_history (
    version VARCHAR PRIMARY KEY,
    trained_at TIMESTAMP,
    strategy VARCHAR,           -- 'FULL_RETRAIN', 'WARM_START'
    parent_version VARCHAR,     -- NULL for FULL_RETRAIN, parent model for WARM_START
    epochs INTEGER,
    learning_rate DECIMAL,
    data_start_date DATE,
    data_end_date DATE,
    training_samples INTEGER,
    training_time_seconds INTEGER,
    final_train_loss DECIMAL,
    final_val_loss DECIMAL,
    calibration_q10_error DECIMAL,
    calibration_q50_error DECIMAL,
    calibration_q90_error DECIMAL,
    directional_accuracy DECIMAL,
    triggered_by VARCHAR,       -- 'scheduled', 'drift_alert', 'regime_change', 'manual'
    notes TEXT
);

-- Example data:
INSERT INTO model_training_history VALUES (
    'v1.0.0', '2024-01-01 10:00:00', 'FULL_RETRAIN', NULL,
    50, 0.001, '2014-01-01', '2024-01-01', 8600000, 2100,
    0.0245, 0.0267, 0.012, 0.023, 0.015, 0.563, 'initial', 'Initial model training'
);

INSERT INTO model_training_history VALUES (
    'v1.1.0', '2024-02-01 10:00:00', 'WARM_START', 'v1.0.0',
    5, 0.0001, '2014-02-01', '2024-02-01', 8605000, 280,
    0.0238, 0.0261, 0.010, 0.021, 0.013, 0.567, 'scheduled', 'Monthly warm-start update'
);
```

**Benefits of Warm-Start:**
1. ✅ **Speed**: 7-10× faster than full retraining
2. ✅ **Stability**: Preserves learned patterns, only adjusts to new data
3. ✅ **Cost**: ~$0.03 vs $0.31 per training run
4. ✅ **Flexibility**: Can retrain more frequently without performance penalty
5. ✅ **Reliability**: Early stopping prevents overfitting on small data updates
6. ✅ **Lineage**: Track parent-child relationships between model versions

### 5. Application Layer

**CLI Interface** (`src/cli/`)
- `gefion ingest <symbols>` - Download and store data
- `gefion train` - Train multi-horizon quantile model
- `gefion predict <symbol>` - Get quantile predictions for all horizons
- `gefion query <symbol> --target-return X --horizon H` - Get signal strength for specific move
- `gefion backtest` - Run historical validation with calibration metrics
- `gefion evaluate` - Model performance metrics and diagnostics
- `gefion screen --min-return X --horizon H` - Find stocks meeting criteria

**Future: Web API** (Optional)
- REST API for predictions
- WebSocket for real-time updates
- Dashboard for visualization

## System Composability: One Engine, Multiple Use Cases

**Key Insight**: All use cases consume the same core output (9 quantiles per stock), just with different consumption logic.

### Core Prediction Engine

```python
# THE FOUNDATION - happens once per symbol
predictor.predict('AAPL')
→ {
    '7d':  {'q10': -1.2%, 'q50': 0.8%,  'q90': 3.5%},
    '30d': {'q10': -3.0%, 'q50': 2.5%,  'q90': 8.0%},
    '90d': {'q10': -8.0%, 'q50': 7.0%,  'q90': 20.0%}
  }
```

### Analytics Layer (Built on Top)

All use cases are just different functions consuming the same predictions:

| Use Case | What It Uses | Implementation |
|----------|-------------|----------------|
| **Buy/Sell Signals** | q50 (median return) | `if pred['30d']['q50'] > 2.0: buy()` |
| **Risk Assessment** | q10 (downside) | `risk = price * pred['30d']['q10'] / 100` |
| **Profit Targets** | q90 (upside) | `target = price * (1 + pred['30d']['q90'] / 100)` |
| **Portfolio Risk** | Sum of q10 across positions | `sum(pos.value * pred[pos.symbol]['30d']['q10'])` |
| **Screening** | q50 across many symbols | `[s for s in symbols if pred[s]['30d']['q50'] > 2.0]` |
| **Position Sizing** | Spread (q90-q10) for confidence | `size = base * (1 / (pred['q90'] - pred['q10']))` |
| **Stop-Loss Placement** | q10 for natural support | `stop = entry * (1 + pred['7d']['q10'] / 100)` |
| **Entry Timing** | Track q50 trend over time | Monitor daily predictions for inflection |
| **Sector Rotation** | Average q50 by sector | Group stocks, compare sector averages |
| **Options Strategy** | Full distribution shape | `spread = q90 - q10` → choose strategy |

### Example: Single Prediction Powers Everything

```python
# Core prediction (happens once)
pred = predictor.predict('AAPL')
current_price = 150

# Use Case 1: Should I buy?
if pred['30d']['q50'] > 2.0:
    decision = "BUY"

# Use Case 2: What's my downside risk?
downside_dollars = current_price * (pred['30d']['q10'] / 100)
# Result: -$4.50

# Use Case 3: Where to place stop-loss?
stop_price = current_price * (1 + pred['7d']['q10'] / 100)
# Result: $148.20

# Use Case 4: How much to allocate?
spread = pred['30d']['q90'] - pred['30d']['q10']  # 11%
confidence = 1 / (1 + spread / 10)  # 0.476
position_size = base_allocation * confidence
# Result: $4,760 (if base = $10,000)

# Use Case 5: Which options strategy?
if pred['30d']['q50'] > 2.0 and spread < 12.0:
    strategy = "Bull Call Spread"
else:
    strategy = "Wait"
```

## Trading Strategies: Different Ways to Use Predictions

The quantile predictions are a **foundational data layer** that can power many different trading strategies. Each strategy has different:
- **Risk tolerance** (conservative vs aggressive)
- **Time horizon** (day trading vs position trading)
- **Objective** (capital preservation vs growth vs income)
- **Market conditions** (bull vs bear vs sideways)

**Strategy Architecture:**

```python
# All strategies consume the same predictions
class TradingStrategy(Protocol):
    def evaluate(self, symbol: str, predictions: dict, portfolio: Portfolio) -> Decision
    def name(self) -> str
    def risk_profile(self) -> str  # 'conservative', 'moderate', 'aggressive'
```

### Strategy 1: Momentum Following (Aggressive Growth)

**Objective**: Capture strong upward trends
**Risk Profile**: Aggressive (high volatility tolerance)
**Time Horizon**: 7-30 days

```python
class MomentumStrategy:
    """Buy stocks with strong positive momentum, high conviction"""

    def evaluate(self, symbol: str, pred: dict, portfolio: Portfolio) -> Decision:
        # Strong bullish signal across multiple horizons
        conditions = {
            'strong_7d': pred['7d']['q50'] > 3.0,     # Expect >3% in 7 days
            'strong_30d': pred['30d']['q50'] > 8.0,   # Expect >8% in 30 days
            'high_upside': pred['30d']['q90'] > 15.0, # 90th percentile >15%
            'limited_downside': pred['30d']['q10'] > -5.0,  # Downside <5%
            'tight_spread': (pred['30d']['q90'] - pred['30d']['q10']) < 20.0,  # High conviction
        }

        if all(conditions.values()):
            # Calculate position size based on conviction
            conviction = 1.0 - (pred['30d']['q90'] - pred['30d']['q10']) / 40.0
            position_size = portfolio.base_position * conviction * 1.5  # Aggressive sizing

            return Decision(
                action='BUY',
                size=position_size,
                entry_price=current_price,
                stop_loss=current_price * (1 + pred['7d']['q10'] / 100),  # Use 7d downside
                take_profit=current_price * (1 + pred['30d']['q90'] / 100),  # Use 30d upside
                rationale=f"Strong momentum: {pred['30d']['q50']:.1f}% expected"
            )

        return Decision(action='HOLD')

    def name(self) -> str:
        return "Momentum Following"

    def risk_profile(self) -> str:
        return "aggressive"
```

**Example Output**:
```
NVDA: BUY 150 shares @ $500
  - Expected 30d return: 12.5% (q50)
  - Upside potential: 22% (q90)
  - Downside risk: -3.5% (q10)
  - Stop loss: $482.50
  - Take profit: $610
  - Conviction: 85% (tight spread)
```

### Strategy 2: Value with Catalyst (Moderate Growth)

**Objective**: Buy undervalued stocks showing reversal signals
**Risk Profile**: Moderate (balanced risk/reward)
**Time Horizon**: 30-90 days

```python
class ValueCatalystStrategy:
    """Look for stocks that have been beaten down but showing recovery signs"""

    def evaluate(self, symbol: str, pred: dict, portfolio: Portfolio) -> Decision:
        # Check if stock has been declining but model predicts recovery
        recent_return = self.get_recent_return(symbol, days=30)  # Last 30 days

        conditions = {
            'was_declining': recent_return < -5.0,           # Down >5% recently
            'reversal_signal': pred['30d']['q50'] > 5.0,     # But expect recovery
            'strong_90d': pred['90d']['q50'] > 10.0,         # Long-term positive
            'acceptable_downside': pred['30d']['q10'] > -8.0, # Risk controlled
        }

        if all(conditions.values()):
            # Conservative position sizing for value plays
            position_size = portfolio.base_position * 0.8

            return Decision(
                action='BUY',
                size=position_size,
                entry_price=current_price,
                stop_loss=current_price * (1 + pred['30d']['q10'] / 100),
                take_profit=current_price * (1 + pred['90d']['q50'] / 100),  # Hold longer
                rationale=f"Value reversal: {recent_return:.1f}% → {pred['30d']['q50']:.1f}%"
            )

        return Decision(action='HOLD')

    def name(self) -> str:
        return "Value with Catalyst"

    def risk_profile(self) -> str:
        return "moderate"
```

### Strategy 3: Capital Preservation (Conservative Income)

**Objective**: Preserve capital with modest gains
**Risk Profile**: Conservative (minimize losses)
**Time Horizon**: 30-90 days

```python
class CapitalPreservationStrategy:
    """Only enter positions with high probability of modest positive returns"""

    def evaluate(self, symbol: str, pred: dict, portfolio: Portfolio) -> Decision:
        conditions = {
            'positive_median': pred['30d']['q50'] > 3.0,      # Expect modest gain
            'minimal_downside': pred['30d']['q10'] > -2.0,    # Very limited downside
            'high_confidence': (pred['30d']['q90'] - pred['30d']['q10']) < 8.0,  # Tight range
            'positive_floor': pred['7d']['q10'] > -1.0,       # Even worst case is mild
        }

        if all(conditions.values()):
            # Very conservative sizing
            position_size = portfolio.base_position * 0.5

            return Decision(
                action='BUY',
                size=position_size,
                entry_price=current_price,
                stop_loss=current_price * 0.98,  # Tight 2% stop
                take_profit=current_price * (1 + pred['30d']['q50'] / 100),
                rationale=f"Low-risk play: {pred['30d']['q10']:.1f}% to {pred['30d']['q90']:.1f}%"
            )

        return Decision(action='HOLD')

    def name(self) -> str:
        return "Capital Preservation"

    def risk_profile(self) -> str:
        return "conservative"
```

### Strategy 4: Mean Reversion (Contrarian)

**Objective**: Profit from oversold bounces
**Risk Profile**: Moderate-Aggressive
**Time Horizon**: 7-30 days

```python
class MeanReversionStrategy:
    """Buy stocks that have dropped significantly but model predicts recovery"""

    def evaluate(self, symbol: str, pred: dict, portfolio: Portfolio) -> Decision:
        recent_7d = self.get_recent_return(symbol, days=7)
        recent_30d = self.get_recent_return(symbol, days=30)

        conditions = {
            'oversold': recent_7d < -8.0,                     # Sharp recent drop
            'bounce_expected': pred['7d']['q50'] > 2.0,       # Expected bounce
            'not_falling_knife': pred['30d']['q50'] > 0.0,    # Not in long-term decline
            'high_bounce_potential': pred['7d']['q90'] > 5.0,  # Could bounce strong
        }

        if all(conditions.values()):
            # Medium position size, quick entry/exit
            position_size = portfolio.base_position * 1.0

            return Decision(
                action='BUY',
                size=position_size,
                entry_price=current_price,
                stop_loss=current_price * (1 + pred['7d']['q10'] / 100),
                take_profit=current_price * (1 + pred['7d']['q90'] / 100),  # Take profit early
                holding_period_max=14,  # Exit by 14 days regardless
                rationale=f"Mean reversion: {recent_7d:.1f}% drop → {pred['7d']['q50']:.1f}% expected"
            )

        return Decision(action='HOLD')
```

### Strategy 5: Sector Rotation (Market Conditions)

**Objective**: Shift capital to strongest sectors
**Risk Profile**: Moderate
**Time Horizon**: 30-90 days

```python
class SectorRotationStrategy:
    """Invest in top-performing sectors based on aggregate predictions"""

    def evaluate_sectors(self, predictions: dict[str, dict], portfolio: Portfolio) -> list[Decision]:
        # Group stocks by sector
        sector_predictions = defaultdict(list)
        for symbol, pred in predictions.items():
            sector = self.get_sector(symbol)
            sector_predictions[sector].append(pred['30d']['q50'])

        # Calculate average expected return per sector
        sector_scores = {
            sector: np.mean(preds)
            for sector, preds in sector_predictions.items()
        }

        # Rank sectors
        top_sectors = sorted(sector_scores.items(), key=lambda x: x[1], reverse=True)[:3]

        decisions = []
        for sector, avg_return in top_sectors:
            # Find best stocks in this sector
            sector_stocks = [
                (sym, pred) for sym, pred in predictions.items()
                if self.get_sector(sym) == sector and pred['30d']['q50'] > avg_return
            ]

            # Buy top 2 stocks from each top sector
            for symbol, pred in sorted(sector_stocks, key=lambda x: x[1]['30d']['q50'], reverse=True)[:2]:
                decisions.append(Decision(
                    action='BUY',
                    symbol=symbol,
                    size=portfolio.base_position * 0.8,
                    rationale=f"Sector rotation: {sector} leader ({pred['30d']['q50']:.1f}%)"
                ))

        return decisions
```

### Strategy 6: Volatility Harvesting (Options-Based)

**Objective**: Sell options on high-volatility predictions
**Risk Profile**: Advanced
**Time Horizon**: 30 days

```python
class VolatilityHarvestingStrategy:
    """Sell options when predicted range is narrow (low volatility expected)"""

    def evaluate(self, symbol: str, pred: dict, portfolio: Portfolio) -> Decision:
        spread = pred['30d']['q90'] - pred['30d']['q10']
        current_price = self.get_current_price(symbol)

        conditions = {
            'narrow_range': spread < 10.0,                    # Low expected volatility
            'positive_bias': pred['30d']['q50'] > 1.0,        # Slightly positive expected
            'owned_shares': portfolio.has_position(symbol),   # Only sell covered calls
        }

        if all(conditions.values()):
            # Sell covered call at q90 (upside target)
            strike_price = current_price * (1 + pred['30d']['q90'] / 100)

            return Decision(
                action='SELL_CALL',
                symbol=symbol,
                strike=strike_price,
                expiry_days=30,
                premium_target=current_price * 0.02,  # Target 2% premium
                rationale=f"Low volatility: {spread:.1f}% range, collect premium"
            )

        return Decision(action='HOLD')
```

### Strategy 7: Risk Parity Portfolio

**Objective**: Balance risk contribution across positions
**Risk Profile**: Conservative-Moderate
**Time Horizon**: 30-90 days

```python
class RiskParityStrategy:
    """Size positions inversely to predicted volatility (wider range = smaller position)"""

    def build_portfolio(self, predictions: dict[str, dict], capital: float) -> list[Decision]:
        # Calculate risk (spread) for each stock
        risks = {
            symbol: pred['30d']['q90'] - pred['30d']['q10']
            for symbol, pred in predictions.items()
            if pred['30d']['q50'] > 2.0  # Only positive expected returns
        }

        # Inverse weighting: lower risk = larger position
        risk_inv = {sym: 1.0 / risk for sym, risk in risks.items()}
        total_inv_risk = sum(risk_inv.values())

        decisions = []
        for symbol, inv_risk in risk_inv.items():
            # Allocate capital inversely proportional to risk
            allocation = capital * (inv_risk / total_inv_risk)
            shares = allocation / self.get_current_price(symbol)

            decisions.append(Decision(
                action='BUY',
                symbol=symbol,
                size=shares,
                rationale=f"Risk parity: {risks[symbol]:.1f}% range → {allocation:.0f} allocation"
            ))

        return decisions
```

### Strategy Comparison Matrix

| Strategy | Risk | Time Horizon | Market Condition | Expected Annual Return | Max Drawdown | Uses |
|----------|------|--------------|------------------|----------------------|--------------|------|
| Momentum Following | High | 7-30d | Bull market | 25-40% | -15 to -25% | q50, q90, spread |
| Value Catalyst | Medium | 30-90d | Recovery phase | 15-25% | -10 to -15% | q50, q10, historical |
| Capital Preservation | Low | 30-90d | Any | 5-12% | -3 to -5% | q10, q50, spread |
| Mean Reversion | Medium-High | 7-30d | Volatile market | 18-30% | -12 to -20% | q50, q90, historical |
| Sector Rotation | Medium | 30-90d | Trending market | 12-20% | -8 to -12% | Aggregate q50 |
| Volatility Harvesting | Medium | 30d | Low volatility | 10-18% | -5 to -10% | spread, q90 |
| Risk Parity | Low-Medium | 30-90d | Any | 8-15% | -5 to -8% | spread, q50 |

### Strategy Implementation Structure

```python
# src/strategies/
├── base.py                    # Strategy protocol/interface
├── momentum.py                # Momentum Following strategy
├── value_catalyst.py          # Value Catalyst strategy
├── capital_preservation.py    # Capital Preservation strategy
├── mean_reversion.py          # Mean Reversion strategy
├── sector_rotation.py         # Sector Rotation strategy
├── volatility_harvesting.py   # Options-based strategy
├── risk_parity.py             # Risk Parity portfolio
└── registry.py                # Strategy registry and backtesting

# Usage
from strategies import StrategyRegistry

# Initialize with predictions
predictor = QuantilePredictor.load('v1.0.0')
predictions = {symbol: predictor.predict(symbol) for symbol in universe}

# Run multiple strategies
registry = StrategyRegistry()
registry.register(MomentumStrategy())
registry.register(CapitalPreservationStrategy())
registry.register(SectorRotationStrategy())

# Backtest all strategies
results = registry.backtest_all(
    predictions=predictions,
    start_date='2020-01-01',
    end_date='2024-12-31',
    initial_capital=100000
)

# Compare performance
print(results.summary())
# Output:
# Strategy                  | Return | Sharpe | Max DD | Win Rate
# --------------------------|--------|--------|--------|----------
# Momentum Following        | 32.5%  | 1.45   | -18.2% | 58.3%
# Capital Preservation      | 9.8%   | 1.92   | -3.1%  | 67.1%
# Sector Rotation           | 16.4%  | 1.68   | -9.5%  | 61.2%

# Deploy chosen strategy
best_strategy = results.get_best_by_sharpe()
decisions = best_strategy.evaluate_universe(predictions, portfolio)
```

### Strategy Ensemble (Meta-Strategy)

Combine multiple strategies for diversification:

```python
class EnsembleStrategy:
    """Run multiple strategies and blend their signals"""

    def __init__(self, strategies: list[TradingStrategy], weights: dict[str, float]):
        self.strategies = strategies
        self.weights = weights  # e.g., {'momentum': 0.4, 'value': 0.3, 'preservation': 0.3}

    def evaluate(self, symbol: str, pred: dict, portfolio: Portfolio) -> Decision:
        # Get signals from all strategies
        signals = {
            strategy.name(): strategy.evaluate(symbol, pred, portfolio)
            for strategy in self.strategies
        }

        # Count BUY signals
        buy_votes = sum(1 for decision in signals.values() if decision.action == 'BUY')
        weighted_votes = sum(
            self.weights[name] for name, decision in signals.items()
            if decision.action == 'BUY'
        )

        # Act if weighted consensus
        if weighted_votes > 0.5:
            # Average the position sizes from strategies that voted BUY
            avg_size = np.mean([
                d.size for d in signals.values() if d.action == 'BUY'
            ])

            return Decision(
                action='BUY',
                size=avg_size,
                rationale=f"Ensemble: {buy_votes}/{len(self.strategies)} strategies agree"
            )

        return Decision(action='HOLD')
```

### Project Structure (Single System)

```
g2/
├── config/
│   ├── features.yaml               # Feature provider configuration
│   ├── model.yaml                  # Model hyperparameters
│   └── database.yaml               # Database connection settings
├── src/
│   ├── models/
│   │   └── quantile_model.py      # Dynamic multi-output model (LSTM + 3 heads)
│   ├── data/
│   │   ├── ingestion/
│   │   │   ├── alphavantage.py    # AlphaVantage API client
│   │   │   ├── vix.py             # VIX data ingestion (future)
│   │   │   ├── earnings.py        # Earnings data (future)
│   │   │   └── sentiment.py       # Sentiment data (future)
│   │   └── database.py            # PostgreSQL storage layer
│   ├── features/
│   │   ├── registry.py            # Feature registry and versioning
│   │   ├── providers/
│   │   │   ├── ohlcv.py          # Core price/volume features
│   │   │   ├── technical.py      # Technical indicators
│   │   │   ├── market_context.py # VIX, SPY, breadth (future)
│   │   │   ├── industry.py       # Sector trends (future)
│   │   │   ├── earnings.py       # Earnings features (future)
│   │   │   └── sentiment.py      # Sentiment features (future)
│   │   └── versioning.py         # Feature version tracking
│   ├── training/
│   │   ├── trainer.py             # Training loop with dynamic features
│   │   ├── dataset.py             # PyTorch Dataset (uses registry)
│   │   └── validation.py          # Walk-forward validation
│   ├── inference/
│   │   └── predictor.py           # CORE: predict(symbol) → 9 quantiles
│   ├── analytics/                 # Basic analytics built on predictor
│   │   ├── signals.py             # Buy/sell signals, trend strength
│   │   ├── screening.py           # Multi-stock screening, top N reports
│   │   ├── risk.py                # Portfolio risk assessment
│   │   └── levels.py              # Stop-loss, take-profit levels
│   ├── strategies/                # Trading strategies using predictions
│   │   ├── base.py                # Strategy protocol/interface
│   │   ├── momentum.py            # Momentum Following strategy
│   │   ├── value_catalyst.py      # Value Catalyst strategy
│   │   ├── capital_preservation.py # Capital Preservation strategy
│   │   ├── mean_reversion.py      # Mean Reversion strategy
│   │   ├── sector_rotation.py     # Sector Rotation strategy
│   │   ├── volatility_harvesting.py # Options-based strategy
│   │   ├── risk_parity.py         # Risk Parity portfolio
│   │   ├── ensemble.py            # Ensemble/Meta-strategy
│   │   └── registry.py            # Strategy registry and comparison
│   ├── backtesting/
│   │   ├── engine.py              # Walk-forward backtest
│   │   ├── metrics.py             # Calibration, performance
│   │   └── strategy_backtest.py   # Strategy-specific backtesting
│   └── cli/
│       └── commands.py            # Expose all via CLI
├── tests/
│   ├── unit/
│   │   ├── test_features/         # Test each feature provider
│   │   ├── test_models/
│   │   └── test_analytics/
│   ├── integration/
│   │   ├── test_data_pipeline.py
│   │   ├── test_training.py
│   │   └── test_feature_versioning.py
│   └── e2e/
│       └── test_full_workflow.py
├── models/                        # Saved model checkpoints
│   ├── model_v1.0_features.json  # Feature snapshot
│   └── model_v1.0.pth            # Model weights
└── notebooks/                     # Analysis and experimentation
    └── feature_exploration.ipynb
```

### Incremental Development Path

**Phase 1: Core Engine (Weeks 1-7)**
- Build extensible feature architecture (registry + providers)
- Implement core feature providers (OHLCV + technical indicators)
- Build data pipeline with AlphaVantage integration
- Train quantile model with dynamic input sizing
- Create `predictor.predict(symbol)` function
- Store predictions in database
- **Deliverable**: Can generate 9 quantiles for any stock using core features

**Phase 2: Basic Analytics (Weeks 8-9)**
- Implement 3 core use cases:
  1. Buy/sell signals (`analytics/signals.py`)
  2. Risk assessment (`analytics/risk.py`)
  3. Screening (`analytics/screening.py`)
- **Deliverable**: Basic CLI for trading decisions

**Phase 3: Advanced Analytics (Weeks 10-12)**
- Implement remaining use cases:
  4. Position sizing
  5. Stop-loss/take-profit levels
  6. Sector rotation
  7. Options strategy selection
- **Deliverable**: Comprehensive trading toolkit

**Phase 4: Production Features (Optional)**
- Web API
- Automated daily predictions
- Alerts/notifications
- Portfolio tracking

### Feature Expansion Roadmap

The feature architecture supports incremental addition of new data sources:

**Phase 1 Features (Initial)**:
- ✅ OHLCV (price, volume)
- ✅ Technical indicators (RSI, ADX, SMA, EMA, MACD, Bollinger Bands, Stochastic)
- ✅ **Cross-sectional basics** (relative return vs SPY, sector rank)

**Phase 2 Features (Month 2-3)**:
- 🔲 **Full cross-sectional suite** (beta, correlation, relative volume/RSI)
- 🔲 Market context (VIX, SPY returns, advance/decline, put/call ratio)
- 🔲 Sector/industry relative performance (sector ETF comparisons)

**Phase 3 Features (Month 4-5)**:
- 🔲 Earnings calendar and surprises
- 🔲 Earnings call sentiment (NLP on transcripts)
- 🔲 **Peer group dynamics** (cluster analysis, co-movement patterns)

**Phase 4 Features (Month 6+)**:
- 🔲 News sentiment scores
- 🔲 Social media sentiment (Twitter, Reddit)
- 🔲 Options flow data
- 🔲 Insider trading activity
- 🔲 **Cross-asset correlations** (bonds, commodities, crypto)

**Leveraging Flexible Metadata in Features:**

The entity attribute system allows features to consume arbitrary metadata without code changes:

```python
class MetadataFeatureProvider(FeatureProvider):
    """Convert entity attributes into model features dynamically"""

    def __init__(self, db, entity_repo):
        self.db = db
        self.entity_repo = entity_repo
        self.config = self._load_config()  # Which attributes to use as features

    def get_features(self, symbol: str, as_of: date) -> Dict[str, float]:
        features = {}

        # Get stock entity and its hierarchy
        stock_entity = self.entity_repo.get_entity(symbol)
        sector_entity = self.entity_repo.get_parent(stock_entity, level='sector')
        industry_entity = self.entity_repo.get_parent(stock_entity, level='industry')

        # Static attributes (from entity_attributes table)
        for attr_key in self.config['static_attributes']:
            # Try stock level first, fallback to sector/industry if not found
            value = (
                self.entity_repo.get_attribute(symbol, attr_key) or
                self.entity_repo.get_attribute(sector_entity.id, attr_key) or
                self.entity_repo.get_attribute(industry_entity.id, attr_key)
            )

            if value:
                features[f'meta_{attr_key}'] = self._to_numeric(value)

        # Time-varying attributes (from entity_time_series table)
        for attr_key in self.config['time_series_attributes']:
            # Get most recent value as of the date
            value = self.entity_repo.get_time_series_value(symbol, attr_key, as_of)
            if value:
                features[f'ts_{attr_key}'] = self._to_numeric(value)

            # Get sector-level time series
            sector_value = self.entity_repo.get_time_series_value(sector_entity.id, attr_key, as_of)
            if sector_value:
                features[f'sector_{attr_key}'] = self._to_numeric(sector_value)

        # Derived features from hierarchy
        features['sector_id_encoded'] = self._encode_category(sector_entity.id)
        features['industry_id_encoded'] = self._encode_category(industry_entity.id)

        # Attribute inheritance and comparison
        if self.config.get('compare_to_sector'):
            stock_cap_intensive = self.entity_repo.get_attribute(symbol, 'capital_intensive')
            sector_cap_intensive = self.entity_repo.get_attribute(industry_entity.id, 'capital_intensive')
            features['matches_industry_profile'] = float(stock_cap_intensive == sector_cap_intensive)

        return features

    def _to_numeric(self, value):
        """Convert string/boolean attributes to numeric features"""
        if value.value_type == 'number':
            return float(value.attribute_value)
        elif value.value_type == 'boolean':
            return 1.0 if value.attribute_value.lower() == 'true' else 0.0
        elif value.value_type == 'string':
            # For categorical strings, use encoding
            return self._encode_category(value.attribute_value)
        elif value.value_type == 'json':
            # Extract numeric values from JSON if possible
            import json
            data = json.loads(value.attribute_value)
            if isinstance(data, (int, float)):
                return float(data)
        return 0.0  # Default for non-convertible types

# Configuration file: config/metadata_features.yaml
metadata_features:
  enabled: true

  # Which static attributes to include as features
  static_attributes:
    - market_cap
    - employees
    - is_sp500
    - is_dow30
    - is_cyclical           # Sector-level attribute
    - capital_intensive     # Industry-level attribute
    - avg_rd_pct           # Industry-level attribute

  # Which time-varying attributes to include
  time_series_attributes:
    - analyst_rating
    - esg_score
    - short_interest_pct
    - put_call_ratio
    - news_sentiment
    - sector_sentiment      # Sector-level time series
    - capacity_utilization  # Industry-level time series

  # Whether to create sector comparison features
  compare_to_sector: true
```

**Example Use Cases:**

1. **Adding ESG Scores** (no code changes):
   ```sql
   -- Just add data to entity_time_series
   INSERT INTO entity_time_series VALUES
       ('AAPL', 'esg_score', '2024-01-15', '78', 'number', 'msci');

   -- Update config to include it
   -- config/metadata_features.yaml: add 'esg_score' to time_series_attributes

   -- Retrain model - it now uses ESG scores!
   ```

2. **Adding Sub-Industry Characteristics**:
   ```sql
   -- Define that semiconductors are supply-constrained
   INSERT INTO entity_attributes VALUES
       ('semiconductors', 'supply_constrained', 'true', 'boolean', 'manual', NOW());

   -- This automatically propagates to all semiconductor stocks
   -- Model learns: "stocks in supply-constrained industries behave differently"
   ```

3. **Custom Groupings** (e.g., "FAANG stocks"):
   ```sql
   -- Create custom entity type
   INSERT INTO entity_types VALUES ('custom_group', 'Custom Group', NULL, 'User-defined groups');

   INSERT INTO entities VALUES ('faang', 'custom_group', 'FAANG Stocks', NULL, NOW(), TRUE);

   -- Tag stocks
   INSERT INTO entity_attributes VALUES
       ('AAPL', 'member_of_faang', 'true', 'boolean', 'manual', NOW()),
       ('META', 'member_of_faang', 'true', 'boolean', 'manual', NOW()),
       ('AMZN', 'member_of_faang', 'true', 'boolean', 'manual', NOW());

   -- Model can now learn: "FAANG stocks correlate differently"
   ```

**How to Add a New Feature Source:**

1. **Add metadata** to `entity_attributes` or `entity_time_series` tables (SQL insert or API)
2. **Update config** (`config/metadata_features.yaml`) to include the new attribute
3. **Retrain model** (`gefion train`)
4. **Model automatically uses new metadata** (no code changes!)

This is much more flexible than creating a new feature provider class for every data source.

**Example: Adding VIX**
```bash
# Step 1: Implement VIX ingestion
$ Gefion ingest-vix --start-date 2020-01-01

# Step 2: Enable market_context provider
$ vim config/features.yaml  # Set enabled: true

# Step 3: Retrain (model grows from N to N+60 inputs)
$ Gefion train --version v2.0

# Step 4: Feature snapshot saved automatically
$ cat models/model_v2.0_features.json
# Shows: ohlcv (v1.0), technical (v1.0), market_context (v1.0)
```

### Key Benefits of This Architecture

1. **Separation of Concerns**: Model training ≠ business logic ≠ feature engineering
2. **Testability**: Each component (providers, analytics, model) independently testable
3. **Extensibility**: Add new features OR use cases without changing core code
4. **Reusability**: Same predictions serve multiple purposes, same features serve multiple models
5. **Performance**: Predict once, use many times; modular data ingestion
6. **Maintainability**: Change model without changing features; change features without changing analytics
7. **Feature Versioning**: Know exactly which features each model version uses
8. **Backwards Compatibility**: Old models continue working with their feature snapshots

### Example: Adding New Use Case (No Model Changes)

```python
# New use case: Find pairs with correlated signals
def find_correlated_pairs(symbols):
    """Find stocks with similar predicted patterns"""
    predictions = {s: predictor.predict(s) for s in symbols}

    # Compare 30-day median predictions
    correlations = []
    for s1 in symbols:
        for s2 in symbols:
            if s1 < s2:
                sim = calculate_similarity(
                    predictions[s1]['30d'],
                    predictions[s2]['30d']
                )
                if sim > 0.8:
                    correlations.append((s1, s2, sim))

    return correlations

# No model retraining needed!
# Just new consumption logic on existing predictions
```

## Technology Stack

**Language**: Python 3.11+

**Core Dependencies**:
- **PyTorch**: Model training and inference
- **NumPy/Pandas**: Data manipulation
- **PostgreSQL**: Production-grade time-series database with connection pooling
- **Requests**: API client
- **Pydantic**: Data validation
- **Click**: CLI framework
- **pytest**: Testing framework
- **black/ruff**: Code formatting and linting

**Visualization** (for analysis):
- **matplotlib/seaborn**: Static plots
- **plotly**: Interactive charts

## Development Approach: Test-Driven Development (TDD)

**Principles:**
1. Write tests before implementation
2. Red-Green-Refactor cycle
3. Unit tests for all components
4. Integration tests for pipelines
5. End-to-end tests for workflows

**Test Structure:**
```
tests/
├── unit/
│   ├── test_ingestion.py
│   ├── test_features.py
│   ├── test_models.py
│   └── test_database.py
├── integration/
│   ├── test_data_pipeline.py
│   ├── test_training_pipeline.py
│   └── test_inference_pipeline.py
└── e2e/
    ├── test_full_workflow.py
    └── test_backtesting.py
```

**Testing Strategy:**
- **Unit Tests**: Mock external APIs, test pure functions
- **Integration Tests**: Use test database, verify component interactions
- **E2E Tests**: Small datasets, verify complete workflows
- **Fixtures**: Synthetic data for reproducible tests
- **Coverage Goal**: >90% code coverage

## Implementation Phases

### Phase 1: Foundation (✓ COMPLETED)

- [x] Project setup (repo structure, dependencies, configs)
- [x] Database schema and connection layer (PostgreSQL + TimescaleDB)
- [x] AlphaVantage API client with rate limiting
- [x] Basic ingestion for single stock
- [x] Test framework setup
- **Deliverable**: ✓ Can download and store data for one stock

### Phase 2: Data Pipeline (⚙️ IN PROGRESS)

- [x] Multi-symbol ingestion (parallel workers, connection pooling)
- [x] Technical indicators (local computation + API fallback)
- [x] Data validation and cleaning
- [x] Feature dispatcher system (registry-based, incremental updates)
- [ ] **Feature engineering module** (normalization, derived features)
- [ ] **Dataset builder with rolling windows** (PyTorch DataLoader)
- [ ] **Label generation** (forward returns for supervised learning)
- **Status**: Infrastructure complete, ML preparation needed

### Phase 3: Model Development (Weeks 5-7)
- [ ] Baseline model (simple LSTM)
- [ ] Training pipeline with validation
- [ ] Hyperparameter tuning framework
- [ ] Model serialization and versioning
- [ ] Experiment tracking (MLflow or Weights & Biases)
- **Deliverable**: Trained model for 10-day horizon

### Phase 4: Inference & Validation (Weeks 8-9)
- [ ] Inference pipeline
- [ ] Backtesting engine
- [ ] Performance metrics calculation
- [ ] Model comparison framework
- [ ] Train 30-day and 90-day models
- **Deliverable**: All three models with backtest results

### Phase 5: Analytics & Use Cases (Weeks 10-11)
- [ ] Implement core analytics modules (signals, screening, risk)
- [ ] CLI commands for all use cases
- [ ] Integration tests for analytics layer
- [ ] User documentation and examples
- **Deliverable**: Comprehensive trading toolkit

### Phase 6: Production & Monitoring (Week 12+)
- [ ] Automated daily prediction pipeline
- [ ] Model performance monitoring dashboard
- [ ] Automated retraining pipeline
- [ ] Deployment scripts
- [ ] API documentation
- **Deliverable**: Production-ready system with monitoring

## Success Metrics

**Model Performance:**
- Directional accuracy > 55% (better than random)
- Correlation with actual returns > 0.3
- Positive Sharpe ratio in backtest
- Consistent performance across different market conditions

**Engineering:**
- Test coverage > 90%
- CI/CD pipeline with automated testing
- Documentation for all components
- Reproducible training and inference

## Learning Objectives

1. **PyTorch proficiency**: Model architecture, training loops, optimization, dynamic input sizing
2. **Financial ML**: Feature engineering, walk-forward validation, regime detection, quantile regression
3. **TDD discipline**: Test-first development, comprehensive test suites, modular testing
4. **MLOps basics**: Model versioning, experiment tracking, monitoring, feature versioning
5. **Time-series analysis**: Temporal modeling, sequence prediction, evaluation metrics
6. **API integration**: Rate limiting, error handling, data validation, multi-source data
7. **Database design**: Time-series optimization, efficient queries, extensible schemas
8. **Software architecture**: Plugin patterns, registry pattern, separation of concerns, extensibility

## Key Design Decisions

### Completed (Phase 1-2)

1. ✅ **Database choice**: PostgreSQL + TimescaleDB (30-day chunks, BRIN indexes)
2. ✅ **Feature set**: RSI, MACD, Bollinger Bands, ADX, Stochastic, SMA (20/50/200), EMA (12/26), PSAR
3. ✅ **Universe selection**: ~5,600 NASDAQ stocks actively tracked
4. ✅ **Data pipeline**: Parallel ingestion with connection pooling, bulk filtering, incremental updates
5. ✅ **Feature storage**: Registry-based tall format with feature_definitions + computed_features

### To Decide (Phase 2-3)

1. **Model architecture**: LSTM (recommended start) vs TCN vs Transformer
2. **Feature engineering**: Which derived features (slopes, ratios, cross-sectional)
3. **Training window**: How much history to use (recommend: 10 years for robust patterns)
4. **Lookback window**: How many days of history for each prediction (recommend: 60 days)

### Refinement (Phase 4+)

1. **Retraining frequency**: How often to retrain (recommend: monthly initially)
2. **Quantile selection**: Stick with [10%, 50%, 90%] or add more (25%, 75%)?
3. **Multi-model strategy**: When to split into separate horizon models

## Scalability Analysis

### Training on 3,500 Stocks × 10 Years

The system is designed to handle the full NASDAQ + Dow Jones universe efficiently:

**Data Storage (PostgreSQL)**
- **Raw OHLCV**: 3,500 stocks × 2,520 trading days × 6 columns × 8 bytes ≈ 424 MB
- **Technical Indicators**: 3,500 stocks × 2,520 days × 15 columns × 8 bytes ≈ 1.06 GB
- **Extended Features** (when added): VIX, earnings, sentiment ≈ 800 MB
- **Total Storage**: ~2.3 GB (easily fits on any modern machine)
- **Query Performance**: PostgreSQL's optimized indexing makes time-series queries extremely fast (<100ms for symbol history)

**Training Data Size**
- **Input Features**: 50 features (OHLCV + technical indicators initially)
- **Lookback Window**: 60 days
- **Training Samples**: 3,500 stocks × 2,460 windows (2,520 - 60) ≈ 8.6M samples
- **Memory per Sample**: 60 days × 50 features × 4 bytes = 12 KB
- **Total Training Data**: ~103 GB uncompressed
- **Solution**: PyTorch DataLoader with streaming (loads batches on-demand, not full dataset)
- **GPU Memory**: Batch of 1,024 samples = 12 MB (easily fits in 8GB GPU)

**Training Performance Estimates**
- **Epochs**: 50 epochs (typical for convergence)
- **Batch Size**: 1,024 samples
- **Batches per Epoch**: 8,600,000 / 1,024 ≈ 8,400 batches
- **Forward/Backward per Batch**: ~5ms on GPU (NVIDIA RTX 3090 or better)
- **Time per Epoch**: 8,400 batches × 5ms ≈ 42 seconds
- **Total Training Time**: 50 epochs × 42s ≈ 35 minutes (GPU)
- **CPU Training**: 3-4× slower ≈ 2-2.5 hours (still reasonable)

**Inference Performance**
- **Single Prediction**: <1ms (LSTM forward pass on 60-day window)
- **Batch Prediction** (all 3,500 stocks): 3,500 × 1ms ≈ 3.5 seconds
- **Daily Prediction Pipeline**: Download latest data (5 min) + Generate predictions (4s) ≈ 5 minutes total

**Hardware Requirements**

**Minimum Specification**:
- **CPU**: 4 cores (Intel i5 or AMD Ryzen 5 equivalent)
- **RAM**: 16 GB (8 GB for data, 4 GB for PyTorch, 4 GB for OS)
- **Storage**: 10 GB (data + models + logs)
- **GPU**: Optional (CPU training works, just slower)

**Recommended Specification**:
- **CPU**: 8 cores (Intel i7/i9 or AMD Ryzen 7/9)
- **RAM**: 32 GB (allows larger batch sizes, parallel experimentation)
- **Storage**: 50 GB SSD (faster I/O for PostgreSQL queries)
- **GPU**: NVIDIA RTX 3060 or better (8GB VRAM) - reduces training from hours to minutes

**Cloud Alternative** (if local machine insufficient):
- **AWS**: g4dn.xlarge (4 vCPU, 16 GB RAM, 1× NVIDIA T4 GPU) ≈ $0.526/hour on-demand
- **Training Cost**: 35 minutes × $0.526/hour ≈ $0.31 per training run
- **Monthly Cost** (retrain weekly): 4 runs × $0.31 ≈ $1.24/month

**Scaling Strategies**

1. **Incremental Development**:
   - **Phase 1**: Start with NASDAQ-100 (100 stocks) to prove architecture
   - **Phase 2**: Expand to S&P 500 (500 stocks) to test scaling
   - **Phase 3**: Full NASDAQ + Dow (3,500 stocks) for production

2. **Data Efficiency**:
   - **Incremental Ingestion**: Only download new dates (not full history daily)
   - **Compression**: PostgreSQL with proper indexing (2.3 GB → 600 MB on disk)
   - **Archiving**: Move old predictions to Parquet files (10× compression)

3. **Training Optimization**:
   - **Mixed Precision** (FP16): 2× faster training, 2× less GPU memory
   - **Gradient Accumulation**: Simulate larger batches on smaller GPUs
   - **Distributed Training**: Multi-GPU if needed (unlikely for this scale)

4. **Feature Pipeline**:
   - **Lazy Loading**: Compute features on-demand during training (saves storage)
   - **Caching**: Cache frequently used feature calculations
   - **Parallel Ingestion**: Download data for multiple stocks concurrently

**Conclusion**: The system comfortably scales to 3,500 stocks over 10 years on a standard development laptop (with GPU) or modest cloud instance. No distributed computing or specialized infrastructure required.

## Modern Software Development Practices

### Guiding Principle

**"Consider what would be good modern software development practices"**

This project adheres to contemporary industry standards for maintainability, reliability, scalability, and developer experience.

### Current Implementation Status

| Practice | Status | Implementation |
|----------|--------|----------------|
| **Containerization** | ✅ Implemented | Docker + Docker Compose for all components |
| **Single Source of Truth** | ✅ Implemented | One Dockerfile.training (GPU/CPU auto-detect) |
| **Runtime Flexibility** | ✅ Implemented | Device auto-detection (CUDA/MPS/CPU) |
| **Test-Driven Development** | ✅ Implemented | Pytest with hot-reload, isolated test DBs |
| **CI/CD Automation** | ✅ Implemented | GitHub Actions for tests, linting, type checking |
| **Type Safety** | ✅ Implemented | MyPy strict mode, Pydantic for validation |
| **Code Quality** | ✅ Implemented | Ruff for linting/formatting |
| **Dependency Management** | ✅ Implemented | Requirements files + pyproject.toml |
| **Configuration Management** | ✅ Implemented | .env files for environment-specific config |
| **Documentation** | ✅ Implemented | HIGHLEVEL.md, DEV.md, inline READMEs |
| **Version Control** | ⏳ Pending | Git initialization (next step) |
| **Semantic Versioning** | ⏳ Pending | To be added with first release |
| **Logging & Observability** | ⏳ Pending | Structured logging to be implemented |
| **Error Handling** | ⏳ Pending | To be implemented with TDD |

### Detailed Practices

#### 1. Containerization (Cloud-Native)

**Status**: ✅ **Fully Implemented**

- **Docker Compose**: Multi-service architecture (app, ingestion, training, db)
- **Volume Mounting**: Hot-reload for rapid TDD iteration
- **Single Image Strategy**: GPU-enabled image that auto-detects hardware
- **Profiles**: On-demand training service to optimize resource usage
- **Benefits**:
  - Same environment dev/CI/prod
  - New developer onboarding: `git clone && docker-compose up`
  - Works on macOS, Linux, Windows WSL2

**Evidence**: [docker-compose.yml](../docker-compose.yml), [docker/](../docker/)

#### 2. Runtime Flexibility Over Build-Time Decisions

**Status**: ✅ **Fully Implemented**

- **Device Auto-Detection**: Single codebase adapts to CUDA/MPS/CPU at runtime
- **No Build-Time Branching**: One image works everywhere
- **Graceful Degradation**: GPU unavailable → automatic CPU fallback
- **Benefits**:
  - Kubernetes/ECS can schedule same image on different node types
  - CI/CD simplified (one image to test)
  - Follows PyTorch/TensorFlow official patterns

**Evidence**: [src/gefion/training/device.py](../src/gefion/training/device.py)

#### 3. Test-Driven Development (TDD)

**Status**: ✅ **Infrastructure Ready**, ⏳ **Tests to be Written**

- **Framework**: Pytest with coverage, parallel execution, watch mode
- **Isolation**: Separate test databases via environment variables
- **Hot-Reload**: Volume mounts enable instant test reruns
- **Markers**: `@pytest.mark.slow`, `@pytest.mark.integration`, etc.
- **CI Integration**: Automated on every push/PR

**Evidence**: [pytest.ini](../pytest.ini), [tests/test_device.py](../tests/test_device.py)

#### 4. Continuous Integration/Deployment (CI/CD)

**Status**: ✅ **Fully Implemented**

- **GitHub Actions**: Automated testing on push/PR
- **Multi-Stage Checks**:
  1. Build containers
  2. Run tests with coverage
  3. Linting (Ruff)
  4. Type checking (MyPy)
- **Coverage Reporting**: Codecov integration
- **Benefits**: Catch issues before merge, enforce quality gates

**Evidence**: [.github/workflows/test.yml](../.github/workflows/test.yml)

#### 5. Type Safety (Static Analysis)

**Status**: ✅ **Fully Implemented**

- **MyPy**: Strict mode enabled
  - `disallow_untyped_defs = true`
  - `check_untyped_defs = true`
  - `warn_return_any = true`
- **Pydantic**: Runtime validation for data models
- **Benefits**: Catch bugs at development time, better IDE support

**Evidence**: [pyproject.toml](../pyproject.toml) (tool.mypy section)

#### 6. Code Quality & Consistency

**Status**: ✅ **Fully Implemented**

- **Ruff**: Modern, fast linter and formatter
  - Replaces: flake8, black, isort, pyupgrade
  - 10-100× faster than legacy tools
- **Configuration**: Centralized in pyproject.toml
- **Pre-CI Checks**: Run locally before commit

**Evidence**: [pyproject.toml](../pyproject.toml) (tool.ruff section)

#### 7. Configuration Management

**Status**: ✅ **Fully Implemented**

- **12-Factor App**: Environment-based config (no hardcoded secrets)
- **.env Files**: Development defaults with `.env.example` template
- **Environment Variables**: Database paths, API keys, logging levels
- **Security**: .env files gitignored, secrets never committed

**Evidence**: [.env.example](../.env.example)

#### 8. Documentation as Code

**Status**: ✅ **Fully Implemented**

- **Architecture**: HIGHLEVEL.md (this document)
- **Developer Setup**: DEV.md
- **Component READMEs**: docker/README.md
- **Inline Documentation**: Docstrings with examples
- **Benefits**: Living documentation, versioned with code

**Evidence**: All *.md files in repository

#### 9. Dependency Pinning

**Status**: ✅ **Implemented** (with flexibility)

- **requirements.txt**: Core dependencies with minimum versions
- **requirements-training.txt**: PyTorch ecosystem
- **requirements-dev.txt**: Development tools
- **pyproject.toml**: Project metadata and tool config
- **Strategy**: Minimum versions (not exact pins) for flexibility

**Evidence**: [requirements*.txt](../), [pyproject.toml](../pyproject.toml)

#### 10. Single Source of Truth

**Status**: ✅ **Fully Implemented**

- **One Dockerfile**: Not separate CPU/GPU dockerfiles
- **One Config File**: Docker Compose orchestrates all services
- **DRY Principle**: No duplicated configuration
- **Benefits**: Easier maintenance, fewer merge conflicts

**Evidence**: Removal of `Dockerfile.training-cpu` in recent commit

### Best Practices NOT Yet Implemented (Future)

| Practice | Priority | Planned Phase |
|----------|----------|---------------|
| **Structured Logging** | High | Phase 1 |
| **Metrics/Monitoring** | Medium | Phase 3 |
| **Feature Flags** | Low | Phase 5 |
| **A/B Testing** | Low | Phase 6 |
| **Blue/Green Deployment** | Medium | Phase 6 |
| **Secrets Management** | High | Phase 2 (Vault/AWS Secrets) |

### Comparison to Legacy Folly Project

| Aspect | Folly (Perl, 2000s) | Gefion (Python, 2024) |
|--------|---------------------|------------------------|
| **Testing** | None | TDD with pytest |
| **Type Safety** | None | MyPy strict mode |
| **CI/CD** | None | GitHub Actions |
| **Containerization** | None | Docker Compose |
| **Dependency Mgmt** | Manual | Requirements files |
| **Documentation** | Inline comments | Architecture docs |
| **Code Quality** | None | Ruff linting |
| **Cloud-Ready** | No | Yes (containerized) |

### Key Takeaways

1. ✅ **Modern infrastructure is in place** - Ready for TDD development
2. ✅ **Cloud-native design** - Runs anywhere (local, AWS, GCP, Azure)
3. ✅ **Developer experience** - Hot-reload, fast iteration, clear docs
4. ⏳ **Implementation phase** - Now ready to write actual business logic with TDD

**The foundation follows 2024 best practices. Now we build the ML system on top of it.**

## Next Steps

### Immediate Actions
1. **Review and approve this high-level design** ← You are here
2. **Make critical technology decisions**:
   - Database: PostgreSQL (production)
   - Model: LSTM encoder with multi-output heads
   - Features: Start with OHLCV + RSI, ADX, SMA, EMA, MACD
3. **Set up project structure**:
   - Initialize Git repo
   - Create directory structure (see "Project Structure" section)
   - Set up virtual environment and dependencies
   - Configure pytest and linting

### Phase 1 Kickoff (Weeks 1-2)
4. **Begin TDD cycle for data ingestion**:
   - Write test: fetch AAPL data from AlphaVantage
   - Implement: API client with rate limiting
   - Write test: store in PostgreSQL
   - Implement: database schema and insertion
5. **Create detailed Phase 1 task breakdown**

### Long-term Vision
- **End of Phase 1**: Can download and store data for any stock
- **End of Phase 4**: Can generate predictions and backtest performance
- **End of Phase 5**: Can use predictions for multiple trading use cases
- **End of Phase 6**: Production system running daily

---

## Notes from Folly Analysis

Key insights to carry forward:
- **Slope analysis was central**: Rate of change matters as much as absolute values
- **Multi-timeframe matters**: Different indicators work at different scales
- **Relative comparisons**: Comparing indicators to each other (e.g., price vs PSAR)
- **Whipsaw awareness**: PSAR works best when ADX > 30 (strong trend context matters)
- **Industry context**: Relative strength vs sector/industry could be valuable features

These domain insights from Folly should inform feature engineering in Gefion.
