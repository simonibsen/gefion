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

**Status**: Planned

**Goal**: Achieve true DB-first architecture by storing all feature functions in the database.

**Current State**: Built-in technical indicators (RSI, MACD, Bollinger Bands, etc.) are implemented in Python code.

**Benefits**:
- Users can modify indicator parameters without code changes
- Consistent versioning and export/import workflow
- Easier to add new indicators via JSON files
- Better isolation and sandboxing

**Implementation**:
- Migrate indicator functions from `src/g2/compute/indicators.py` to JSON files in `feature-functions/`
- Update seeding process to import from JSON instead of hardcoded Python
- Maintain backward compatibility during migration

### 1.2 Feature Selection During Dataset Build

**Status**: Planned

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

**Status**: Planned

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

**Status**: Planned

**Goal**: Add categorical trend prediction alongside quantile regression.

**Current State**: Trend labels (weak_up, strong_up, neutral, weak_down, strong_down) are computed and stored but not used for predictions.

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

### 3.1 Sector-Relative Features

**Status**: Planned

**Goal**: Enable sector and market-relative analysis.

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

### 4.1 Seven Complete Trading Strategies

**Status**: Planned

Implement production-ready strategies with full backtesting:

1. **Momentum Following** (aggressive, 7-30d horizons)
   - Buy stocks with strong_up trend + q50 > 3%
   - Position size by inverse IQR (q90-q10)
   - Exit on trend reversal or 30-day horizon

2. **Value with Catalyst** (moderate, 30-90d horizons)
   - Undervalued stocks (low P/E, low P/B) with positive catalyst events
   - Use 30-90d quantile predictions for entry timing
   - Hold until reversion to fair value

3. **Capital Preservation** (conservative, 30-90d horizons)
   - Only buy when q10 > 0 (downside protected)
   - Focus on low-volatility, dividend stocks
   - Position size: equal weight or risk parity

4. **Mean Reversion** (aggressive, 7-30d horizons)
   - Buy oversold stocks (RSI < 30) with positive q50
   - Sector-relative reversion signals
   - Quick exits on profit targets

5. **Sector Rotation** (moderate, 30-90d horizons)
   - Identify strongest sectors using cross-sectional features
   - Buy sector leaders with strong quantile predictions
   - Rotate monthly based on updated predictions

6. **Volatility Harvesting** (advanced, options-focused)
   - Identify stocks with IQR mismatch to implied volatility
   - Sell options when predicted volatility < market IV
   - Buy options when predicted volatility > market IV

7. **Risk Parity** (moderate, 30-90d horizons)
   - Allocate capital based on inverse volatility (q90-q10)
   - Diversify across sectors and strategies
   - Rebalance weekly or monthly

### 4.2 Backtesting Engine

**Goal**: Simulate full portfolio performance with realistic constraints.

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

**Goal**: Understand which features drive predictions.

**Outputs**:
- SHAP values for feature attribution
- Feature importance rankings
- Partial dependence plots
- Feature selection recommendations

**Usage**:
```bash
# Analyze feature importance
g2 ml feature-importance \
  --model-name mvp_model --model-version 20251217 \
  --horizon 7 \
  --top-k 20

# Output: Ranked features with importance scores
```

### 5.4 Hyperparameter Tuning

**Goal**: Automatically find best model hyperparameters.

**Implementation**:
- Grid search or random search
- Bayesian optimization
- Cross-validation on time-series data
- Save best parameters to model metadata

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

**High Priority** (next 3-6 months):
1. Move indicators to database
2. Feature selection during dataset build
3. Parquet export format
4. Trend classification model

**Medium Priority** (6-12 months):
5. Cross-sectional features
6. Backtesting engine
7. First 3 trading strategies

**Long-term** (12+ months):
8. Remaining trading strategies
9. Warm-start retraining
10. Model ensembles
11. Online prediction API

## Contributing

Interested in implementing any of these features? See the main [ARCHITECTURE.md](ARCHITECTURE.md) for system design and [CONTRIBUTING.md](../CONTRIBUTING.md) for development guidelines.

## Related Documentation

- [ML Quickstart](ML_QUICKSTART.md) - Get started with current ML features
- [Architecture](ARCHITECTURE.md) - System design and DB-first architecture
- [ML System Design](archive/ml/ML_SYSTEM_DESIGN.md) - Detailed ML schemas and pipelines
- [ML Vision](archive/ml/HIGHLEVEL.md) - Long-term vision and goals
