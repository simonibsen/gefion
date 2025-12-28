# g2 ML Roadmap

This document outlines planned enhancements to g2's machine learning capabilities.

## Overview

g2 aims to provide a comprehensive ML-driven quantitative analysis platform with:
- **Dual Prediction Systems**: Quantile regression for risk + trend classification for screening
- **Configurable Multi-Horizon Forecasts**: User-defined prediction windows
- **Cross-Sectional Features**: Sector and market-relative metrics
- **Complete Trading Strategies**: Production-ready backtesting and execution
- **Parquet Support**: Industry-standard data formats

## Phase 1: Core Improvements (Current Focus)

### 1.1 Move Built-in Indicators to Database

**Status**: ✅ Complete (2025-12-26)

**Goal**: Achieve true DB-first architecture by storing all feature functions in the database.

**Implementation**:
- 9 indicator functions in `feature-functions/` (RSI, MACD, Bollinger Bands, SMA, EMA, ADX, Stochastic, PSAR, price_change)
- 18 feature definitions in `feature-definitions/` (specific configurations like indicator_rsi_14, indicator_sma_20, etc.)
- `db-init` automatically seeds feature functions and definitions
- `feat-fx-import` / `feat-def-import` commands for manual import
- `feat-fx-export` / `feat-def-export` commands for exporting to JSON

**Benefits**:
- Users can modify indicator parameters without code changes
- Consistent versioning and export/import workflow
- Easier to add new indicators via JSON files
- Better isolation and sandboxing

### 1.2 Feature Selection During Dataset Build

**Status**: ✅ Complete (2025-12-17)

**Goal**: Allow users to select specific features for training instead of including all computed features.

**Current State**: All features in `computed_features` are automatically included in datasets.

**Usage**:
```bash
# Include only specific features
g2 ml dataset-build --name selective --version v1 \
  --symbols AAPL,MSFT \
  --horizons 7,30 \
  --features indicator_rsi_14,indicator_macd,news_sentiment_score \
  --export

# Exclude features (blacklist)
g2 ml dataset-build --name filtered --version v1 \
  --symbols AAPL,MSFT \
  --horizons 7,30 \
  --exclude-features indicator_obv,indicator_adx \
  --export
```

### 1.3 Parquet Export Format

**Status**: ✅ Complete (2025-12-28)

**Goal**: Support Parquet format for dataset exports alongside CSV.

**Benefits**:
- 5-10x smaller files (compression)
- Type preservation (no string conversion)
- Faster I/O (columnar format)
- Industry standard for ML pipelines
- Compatible with Apache Arrow, Spark, Pandas

**Usage**:
```bash
# Export as Parquet
g2 ml dataset-build --name mvp --version v1 \
  --symbols AAPL,MSFT \
  --horizons 7,30 \
  --export --format parquet

# Output: datasets/mvp/prices.parquet, features.parquet, labels.parquet
```

## Phase 2: Trend Classification

### 2.1 Trend Classification Model

**Status**: ✅ Complete (2025-12-17)

**Goal**: Add categorical trend prediction alongside quantile regression.

**Current State**: Trend labels and predictions are computed and stored. Full 5-class classifier implemented with training, prediction, and evaluation workflows.

**Benefits**:
- Screen stocks by predicted trend strength
- Combine with quantile predictions for risk-adjusted selection
- Enable momentum and reversal strategies
- Categorical confidence scores

**Implementation**:
- New table: `trend_class_predictions` (already exists in schema)
- Train multi-class classifier (XGBoost, Random Forest, or Neural Network)
- Store class probabilities for each category
- CLI: `g2 ml train-classifier` and `g2 ml predict-classifier`

**Usage Example**:
```bash
# Train trend classifier
g2 ml train-classifier \
  --dataset-name mvp --dataset-version v1 \
  --model-name trend_model --model-version 20251217 \
  --algorithm xgboost

# Generate trend predictions
g2 ml predict-classifier \
  --model-name trend_model --model-version 20251217 \
  --prediction-date 2024-12-14 \
  --symbols AAPL,MSFT

# Query predictions
SELECT s.symbol, tcp.horizon_days, tcp.predicted_class, tcp.confidence
FROM trend_class_predictions tcp
JOIN stocks s ON tcp.data_id = s.id
WHERE tcp.prediction_date = '2024-12-14'
ORDER BY tcp.confidence DESC;
```

### 2.2 Combined Screening Strategy

**Goal**: Use both quantile and trend predictions for stock selection.

**Example**:
```sql
-- Find stocks with strong uptrend AND protected downside
SELECT s.symbol, qp.q10, qp.q50, qp.q90, tcp.predicted_class, tcp.confidence
FROM quantile_predictions qp
JOIN trend_class_predictions tcp ON
  qp.data_id = tcp.data_id AND
  qp.prediction_date = tcp.prediction_date AND
  qp.horizon_days = tcp.horizon_days
JOIN stocks s ON qp.data_id = s.id
WHERE qp.prediction_date = CURRENT_DATE
  AND qp.horizon_days = 7
  AND tcp.predicted_class = 'strong_up'
  AND tcp.confidence > 0.7
  AND qp.q10 > 0  -- Downside protected
ORDER BY qp.q90 DESC  -- Highest upside potential
LIMIT 20;
```

## Phase 3: Cross-Sectional Features

### 3.1 Cross-Sectional Features

**Status**: ✅ Complete (2025-12-17)

**Goal**: Enable sector and market-relative analysis.

**Note**: Implemented as market-relative features (MVP). Sector-specific analysis can be added in future iterations.

**New Table**:
```sql
CREATE TABLE cross_sectional_features (
    data_id INTEGER REFERENCES stocks(id),
    date DATE,
    sector VARCHAR(50),
    feature_name VARCHAR(255),
    value DOUBLE PRECISION,
    sector_mean DOUBLE PRECISION,
    sector_std DOUBLE PRECISION,
    rank_in_sector INTEGER,
    percentile_in_sector DOUBLE PRECISION,
    market_percentile DOUBLE PRECISION,
    PRIMARY KEY (data_id, date, feature_name)
);
```

**Features to Compute**:
- Return vs sector average
- Volume vs sector average
- Volatility vs sector average
- RSI relative to sector
- Sector rotation momentum

**Usage**:
```bash
# Compute cross-sectional features
g2 feat-compute-cross-sectional \
  --exchange NASDAQ \
  --features return_vs_sector,volume_vs_sector

# Query sector leaders
SELECT s.symbol, csf.sector, csf.value, csf.percentile_in_sector
FROM cross_sectional_features csf
JOIN stocks s ON csf.data_id = s.id
WHERE csf.date = CURRENT_DATE
  AND csf.feature_name = 'return_vs_sector'
  AND csf.percentile_in_sector > 0.9  -- Top 10% in sector
ORDER BY csf.value DESC;
```

## Phase 4: Trading Strategies & Backtesting

### 4.1 Trading Strategies

**Status**: ✅ 7 Strategies Implemented (2025-12-17)

Production-ready strategies available via `g2 backtest run --strategy <name>`:

1. **Momentum** - Buy top-N stocks by momentum, rebalance periodically
   - Parameters: `--lookback-days`, `--top-n`, `--rebalance-days`

2. **Mean Reversion** - Buy oversold (RSI < threshold), sell overbought
   - Parameters: `--rsi-oversold`, `--rsi-overbought`, `--rsi-period`

3. **MA Crossover** - Buy on fast MA crossing above slow MA
   - Parameters: `--fast-period`, `--slow-period`, `--max-positions`

4. **Breakout** - Buy on price/volume breakout above resistance
   - Parameters: `--volume-threshold`

5. **Pairs Trading** - Trade spread between correlated pairs
   - Parameters: `--entry-zscore`, `--exit-zscore`

6. **RSI Divergence** - Detect bullish/bearish RSI divergence
   - Parameters: `--divergence-lookback`

7. **Volatility Contraction** - Trade Bollinger Band squeezes
   - Parameters: `--bb-period`, `--bb-std-dev`, `--squeeze-threshold`

**Usage**:
```bash
# Momentum strategy on tech stocks
g2 backtest run --symbols AAPL,MSFT,GOOGL,NVDA \
  --start-date 2024-01-01 --end-date 2024-12-01 \
  --strategy momentum --top-n 3

# Compare all strategies
g2 backtest compare --all-strategies \
  --start-date 2024-01-01 --end-date 2024-12-01
```

### 4.2 Backtesting Engine

**Status**: ✅ MVP Complete (2025-12-17)

**Goal**: Simulate full portfolio performance with realistic constraints.

**Current State**: Core backtesting engine with point-in-time correctness, portfolio tracking, and performance metrics. Transaction costs and advanced features planned for Phase 2.

**Features**:
- Point-in-time data (no look-ahead bias)
- Transaction costs and slippage
- Position sizing and rebalancing
- Portfolio constraints (max position, sector limits)
- Risk metrics (Sharpe, Sortino, max drawdown, Calmar)

**Usage**:
```bash
# Backtest momentum strategy
g2 backtest run \
  --strategy momentum_following \
  --start-date 2023-01-01 \
  --end-date 2024-12-01 \
  --initial-capital 100000 \
  --max-positions 20 \
  --rebalance-frequency weekly

# Output: Performance metrics, trade log, equity curve CSV
```

### 4.3 Strategy Comparison

**Goal**: Compare multiple strategies side-by-side.

**Metrics**:
- Total return
- Sharpe ratio
- Max drawdown
- Calmar ratio
- Win rate
- Average gain/loss
- Turnover
- Beta to market

**Visualization**: Generate equity curves and comparison tables.

## Phase 5: Advanced Features

### 5.1 Warm-Start Retraining

**Goal**: Efficiently update models monthly without retraining from scratch.

**Benefits**:
- 10-100x faster retraining
- Incremental learning from new data
- Maintain model quality
- Suitable for production deployment

**Implementation**:
- Save model state after training
- Load existing model and continue training on new data
- Supported by XGBoost and LightGBM (not basic quantile regression)

### 5.2 Model Ensembles

**Goal**: Combine multiple model predictions for better accuracy.

**Approaches**:
- Average predictions from multiple algorithms
- Weighted ensemble by validation performance
- Stacking (meta-model on top of base models)

### 5.3 Feature Importance Analysis

**Status**: ✅ Complete (2025-12-28)

**Goal**: Understand which features drive predictions.

**Implementation**:
- SHAP-based feature importance using TreeSHAP for XGBoost/LightGBM (fast, exact)
- Permutation importance fallback for sklearn models
- CLI command: `g2 ml feature-importance`
- MCP tool: `ml_feature_importance`
- Automatically adapts to any features in the trained model

**Usage**:
```bash
# Show top 20 features for 7-day horizon
g2 ml feature-importance \
  --model-name mvp_model --model-version 20251217 \
  --horizon 7 \
  --top-k 20

# Output as JSON for programmatic use
g2 ml feature-importance \
  --model-name mvp_model --model-version 20251217 \
  --horizon 7 --json
```

### 5.4 Hyperparameter Tuning

**Status**: ✅ Complete (2025-12-28)

**Goal**: Automatically find best model hyperparameters.

**Implementation**:
- Bayesian optimization via Optuna
- Time-series cross-validation (prevents data leakage)
- Supports XGBoost, LightGBM, sklearn algorithms
- CLI command: `g2 ml tune`
- MCP tool: `ml_tune`
- Saves best parameters to JSON

**Usage**:
```bash
# Tune XGBoost quantile model with 50 trials
g2 ml tune --dataset-name mvp --dataset-version v1 \
  --algorithm xgboost --n-trials 50

# Tune classifier with LightGBM
g2 ml tune --dataset-name mvp --dataset-version v1 \
  --algorithm lightgbm --model-type classifier

# Quick tuning with timeout
g2 ml tune --dataset-name mvp --dataset-version v1 --timeout 300
```

### 5.5 Online Prediction API

**Goal**: Serve predictions via HTTP API for integration with trading systems.

**Features**:
- REST API with FastAPI
- Batch predictions
- Real-time model loading
- Authentication and rate limiting

**Usage**:
```bash
# Start prediction server
g2 ml serve --model-name mvp_model --model-version 20251217 --port 8000

# Query predictions
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["AAPL", "MSFT"], "date": "2024-12-14"}'
```

## Implementation Priority

### Completed
- ✅ 1.1 Move indicators to database (2025-12-28)
- ✅ 1.2 Feature selection during dataset build (2025-12-17)
- ✅ 1.3 Parquet export format (2025-12-28)
- ✅ 2.1 Trend classification model (2025-12-17)
- ✅ 3.1 Cross-sectional features (2025-12-17)
- ✅ 4.1 Trading strategies - 7 implemented (2025-12-17)
- ✅ 4.2 Backtesting engine MVP (2025-12-17)
- ✅ 5.3 Feature importance analysis (2025-12-28)
- ✅ 5.4 Hyperparameter tuning with Optuna (2025-12-28)

### Future
1. 5.1 Warm-start retraining - Incremental learning for monthly updates
2. 5.2 Model ensembles - Combine multiple algorithms
3. 5.5 Online prediction API - Lower priority, defer

## Contributing

Interested in implementing any of these features? See the main [ARCHITECTURE.md](ARCHITECTURE.md) for system design and [CONTRIBUTING.md](../CONTRIBUTING.md) for development guidelines.

## Related Documentation

- [ML Quickstart](ML_QUICKSTART.md) - Get started with current ML features
- [Architecture](ARCHITECTURE.md) - System design and DB-first architecture
- [ML System Design](archive/ml/ML_SYSTEM_DESIGN.md) - Detailed ML schemas and pipelines
- [ML Vision](archive/ml/HIGHLEVEL.md) - Long-term vision and goals
