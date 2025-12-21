# ML System Design - Quantile Regression for Multi-Horizon Predictions

**Date:** 2024-12-04
**Status:** Design Phase
**Priority:** High

---

## Executive Summary

The g2 ML system will predict **return distributions** (not point estimates) for multiple time horizons, enabling probabilistic assessment of price movements. This document captures the architecture decisions, schema design, and implementation approach.

---

## Core Problem Statement

### Business Question
**For a given stock and timeframe (7, 30, or 90 days), what is the likelihood that it will move X points (positive or negative)?**

### Technical Approach
Use **quantile regression** to predict return distributions rather than single-point forecasts.

**Input:** Historical stock data with technical indicators (OHLCV, RSI, PSAR, ADX, Moving Averages, etc.)

**Output:** Multi-horizon return distribution predictions
- 7-day horizon: Return quantiles (10th, 50th, 90th percentile)
- 30-day horizon: Return quantiles (10th, 50th, 90th percentile)
- 90-day horizon: Return quantiles (10th, 50th, 90th percentile)

### Signal Strength Derivation
For a target move of X%, compare against the quantile distribution to determine likelihood/strength:

- **If X < q10**: Very unlikely (negative signal strength)
- **If q10 < X < q50**: Below median (weak signal)
- **If q50 < X < q90**: Above median (moderate signal)
- **If X > q90**: Highly likely (strong signal)

### Validation
Compare predicted distributions to actual returns over the prediction window using rolling backtests. Evaluate using quantile loss and calibration metrics.

---

## System Architecture

### High-Level Flow

```
1. Data Ingestion
   └─> stock_ohlcv (OHLCV data)

2. Feature Engineering
   ├─> Absolute indicators (RSI, MACD, SMA, EMA, ...)
   ├─> Derivative features (slopes, concavity)
   └─> Cross-sectional features (sector-relative, market-relative)

3. Target Computation
   └─> Forward returns at 7d, 30d, 90d horizons

4. Model Training
   ├─> Quantile regression (predict q10, q50, q90)
   └─> One multi-output model (3 heads: 7d/30d/90d)

5. Prediction Generation
   └─> Store quantile predictions in database

6. Signal Assessment
   └─> Given target return, compute signal strength from distribution

7. Backtesting (Future)
   └─> Validate predictions against actual outcomes
```

### Core Goals

1. **Multi-Horizon Predictions**: Predict return distributions for 7, 30, and 90 day horizons
2. **Dual Prediction Systems**:
   - Quantile Regression for risk assessment and position sizing
   - Trend Classification for screening and pattern recognition
3. **Quantile Regression**: Output full distributions (10th, 50th, 90th percentiles) instead of point estimates
4. **Cross-Sectional Features**: Model stocks relative to their sector and market context

---

## Database Schema Design

### 0. Model Registry + Dataset/Run Lineage (MVP)

The ML system needs two things beyond storing predictions:

1. **Reproducibility**: know exactly what dataset, labels, splits, and feature set produced a model.
2. **Traceability**: connect a training run → model artifact → generated predictions → realized outcomes/metrics.

For that, store **dataset manifests** and **run configs** explicitly, then reference them from models and predictions.

```sql
-- Dataset manifests (what data was built/exported)
CREATE TABLE ml_datasets (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,                    -- e.g. 'nasdaq100_v1'
    version TEXT NOT NULL,                 -- e.g. '2025-12-14'
    created_at TIMESTAMP DEFAULT NOW(),

    -- What went into the dataset
    universe JSONB,                        -- selection criteria (exchange, symbol list, filters)
    feature_names TEXT[] NOT NULL,
    lookback_days INTEGER NOT NULL,        -- rolling window length
    horizons_days INTEGER[] NOT NULL,      -- e.g. {7,30,90}
    label_spec JSONB NOT NULL,             -- forward returns + optional thresholds
    split_spec JSONB NOT NULL,             -- walk-forward/rolling split definition (PIT)

    -- Where it lives (Parquet + manifest)
    artifact_uri TEXT NOT NULL,            -- file path or object-store URI
    checksum TEXT,                         -- hash of manifest/artifacts

    UNIQUE (name, version)
);

-- Runs (train/eval/predict) with config + environment metadata
CREATE TABLE ml_runs (
    id SERIAL PRIMARY KEY,
    run_type TEXT NOT NULL,                -- 'train' | 'predict' | 'eval'
    status TEXT NOT NULL DEFAULT 'running',
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    finished_at TIMESTAMP,

    dataset_id INTEGER REFERENCES ml_datasets(id),
    run_config JSONB NOT NULL,             -- hyperparams, feature filtering, horizons, etc.
    code_version TEXT,                     -- git SHA
    notes TEXT
);

-- Models (artifacts) produced by training runs
CREATE TABLE ml_models (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,                    -- 'trend_quantiles'
    version TEXT NOT NULL,                 -- '2025-12-14_v1'
    created_at TIMESTAMP DEFAULT NOW(),

    train_run_id INTEGER REFERENCES ml_runs(id),
    dataset_id INTEGER REFERENCES ml_datasets(id),

    algorithm TEXT,                        -- 'pytorch' (initial target)
    hyperparams JSONB,
    metrics JSONB,
    artifact_uri TEXT NOT NULL,            -- path/URI to saved model
    active BOOLEAN DEFAULT TRUE,

    UNIQUE (name, version)
);
```

Notes:
- `ml_datasets` and `ml_runs` are the “config layer” so the CLI can stay flexible without hardcoding feature sets.
- For MVP, `artifact_uri` can be a local path (e.g. `models/...`); later it can move to S3/GCS.

### 1. Quantile Predictions Table

Stores model outputs (the predicted distributions).

```sql
CREATE TABLE quantile_predictions (
    model_id INTEGER NOT NULL,
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    prediction_date DATE NOT NULL,  -- When prediction was made
    horizon_days INTEGER NOT NULL,  -- 7, 30, or 90

    -- Quantile predictions (return %)
    q10 NUMERIC(10,4),  -- 10th percentile (downside)
    q50 NUMERIC(10,4),  -- 50th percentile (median)
    q90 NUMERIC(10,4),  -- 90th percentile (upside)

    -- Model metadata
    model_version TEXT,
    features_snapshot JSONB,  -- Feature values used for this prediction

    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (model_id, data_id, prediction_date, horizon_days)
);

SELECT create_hypertable('quantile_predictions', 'prediction_date');

CREATE INDEX quantile_predictions_symbol_date_idx
    ON quantile_predictions(data_id, prediction_date, horizon_days);
```

Recommendation (MVP): add a run reference for traceability.

```sql
ALTER TABLE quantile_predictions
ADD COLUMN run_id INTEGER REFERENCES ml_runs(id);
```

### 1b. Trend Classification Predictions (5-class)

For “trend strength” screening, store classifier outputs separately from quantiles. Use **per-horizon weak/strong thresholds** to label outcomes and train a 5-class model:

- `STRONG_UP`: return ≥ +strong_threshold
- `WEAK_UP`: +weak_threshold ≤ return < +strong_threshold
- `NEUTRAL`: |return| < weak_threshold
- `WEAK_DOWN`: -strong_threshold < return ≤ -weak_threshold
- `STRONG_DOWN`: return ≤ -strong_threshold

Store predicted probabilities per class:

```sql
CREATE TABLE trend_class_predictions (
    model_id INTEGER NOT NULL REFERENCES ml_models(id),
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    prediction_date DATE NOT NULL,
    horizon_days INTEGER NOT NULL,

    weak_threshold NUMERIC(10,4) NOT NULL,
    strong_threshold NUMERIC(10,4) NOT NULL,

    p_strong_up DOUBLE PRECISION,
    p_weak_up DOUBLE PRECISION,
    p_neutral DOUBLE PRECISION,
    p_weak_down DOUBLE PRECISION,
    p_strong_down DOUBLE PRECISION,

    predicted_class TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    run_id INTEGER REFERENCES ml_runs(id),

    PRIMARY KEY (model_id, data_id, prediction_date, horizon_days)
);

SELECT create_hypertable('trend_class_predictions', 'prediction_date');
```

Derived “trend strength” signals can be computed from probabilities, e.g.:
- `strength = p_strong_up + p_weak_up - (p_weak_down + p_strong_down)`
- `confidence = 1 - p_neutral`

### 2. Prediction Outcomes Table

Stores actual outcomes for validation.

```sql
CREATE TABLE prediction_outcomes (
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    prediction_date DATE NOT NULL,  -- When prediction was made
    outcome_date DATE NOT NULL,     -- When outcome was observed
    horizon_days INTEGER NOT NULL,

    -- Actual return
    actual_return NUMERIC(10,4),

    -- Link to prediction
    model_id INTEGER,

    PRIMARY KEY (data_id, prediction_date, horizon_days)
);

SELECT create_hypertable('prediction_outcomes', 'prediction_date');
```

Recommendation (MVP): add a run reference for traceability.

```sql
ALTER TABLE prediction_outcomes
ADD COLUMN run_id INTEGER REFERENCES ml_runs(id);
```

### 3. Model Performance Table

Tracks calibration and accuracy metrics.

```sql
CREATE TABLE model_performance (
    model_id INTEGER PRIMARY KEY,
    model_name TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,

    -- Calibration metrics (how well quantiles match reality)
    q10_calibration NUMERIC(5,2),  -- % of actual returns < q10 (should be ~10%)
    q50_calibration NUMERIC(5,2),  -- % of actual returns < q50 (should be ~50%)
    q90_calibration NUMERIC(5,2),  -- % of actual returns < q90 (should be ~90%)

    -- Quantile loss (lower is better)
    quantile_loss NUMERIC(10,6),

    -- Sharpness (narrower distributions are better if calibrated)
    avg_iqr NUMERIC(10,4),  -- Average interquartile range (q90 - q10)

    -- Backtest period
    eval_start_date DATE,
    eval_end_date DATE,
    num_predictions INTEGER,

    updated_at TIMESTAMP DEFAULT NOW()
);
```

Recommendation (MVP): store evaluation run linkage.

```sql
ALTER TABLE model_performance
ADD COLUMN eval_run_id INTEGER REFERENCES ml_runs(id);
```

### 4. Trend Analysis Table (Future)

Simplified trend detection based on predictions.

```sql
CREATE TABLE trend_analysis (
    data_id INTEGER NOT NULL REFERENCES stocks(id),
    date DATE NOT NULL,

    -- Trend classification
    trend_direction TEXT,  -- 'UP', 'DOWN', 'SIDEWAYS'
    trend_probability NUMERIC(5,2),  -- 0-100, confidence in direction

    -- Strength metrics
    momentum_strength NUMERIC(10,4),  -- Magnitude of price change
    consistency_score NUMERIC(5,2),   -- How consistent the trend is (0-100)
    volume_confirmation NUMERIC(5,2), -- Is volume supporting? (0-100)

    -- Cross-sectional strength
    vs_sector_strength NUMERIC(10,4),  -- Relative to sector
    vs_market_strength NUMERIC(10,4),  -- Relative to market
    sector_rank INTEGER,               -- Rank within sector (1 = strongest)

    -- Risk/uncertainty
    volatility NUMERIC(10,4),
    prediction_spread NUMERIC(10,4),   -- p90 - p10 from quantile model

    -- Overall composite strength score
    composite_strength NUMERIC(5,2),   -- 0-100, combining all factors

    PRIMARY KEY (data_id, date)
);

SELECT create_hypertable('trend_analysis', 'date');
```

---

## Feature Engineering

### Current Features (Implemented)

✅ **Price-based indicators:**
- RSI (14-period)
- SMA (20, 50, 200)
- EMA (12, 26)
- MACD (with signal and histogram)
- Bollinger Bands (upper, middle, lower)
- Parabolic SAR
- ADX (14-period)
- Stochastic Oscillator (K, D)

✅ **Derivative features:**
- Slopes (5-day, 10-day linear regression)
- Concavity (second derivative)
- Applied to: RSI, MACD, price, ADX, stochastic, Bollinger middle

### Required Features (To Be Implemented)

❌ **Volume-based features:**
- On-Balance Volume (OBV)
- Volume trend (increasing on up days?)
- Volume-weighted moving average
- Volume confirmation ratio

❌ **Cross-sectional features:**
- Sector-relative RSI
- Sector-relative momentum
- Market-relative momentum
- Sector rank (percentile within sector)

❌ **Consistency features:**
- Percent of days up (over last 30 days)
- Trend line fit (R²)
- Price/indicator volatility regime

### Feature Calculation Pattern

All features follow the metadata-driven pattern:

```sql
INSERT INTO feature_definitions (name, function_name, params, ...)
VALUES (
    'cross_sectional_rsi',
    'cross_sectional',
    '{"indicator": "rsi_14", "benchmark": "sector", "method": "zscore"}'::jsonb,
    'computed_features',
    'value',
    'computed_features',
    'value',
    true
);
```

---

## Model Architecture

### Technology Choice: LightGBM vs PyTorch

**Decision: Start with LightGBM**

#### LightGBM (Recommended for Phase 1)

**Pros:**
- ✅ Excellent for tabular data (stock features are tabular)
- ✅ Built-in quantile regression support
- ✅ Fast training (10-100x faster than PyTorch on CPU)
- ✅ Less hyperparameter tuning needed
- ✅ Feature importance for interpretability
- ✅ Simple deployment (pickle/joblib)
- ✅ Handles missing data naturally

**Cons:**
- ❌ Can't easily model sequential dependencies
- ❌ Less flexible architecture

#### PyTorch (Optional for Phase 2)

**When to use:**
- If you want to model price sequences (LSTM on OHLCV time series)
- If you want custom neural architectures
- If you want multi-task learning (predict all horizons in one model)
- If LightGBM performance plateaus

**Pros:**
- ✅ Flexible architectures (LSTM, Transformer, attention)
- ✅ Sequential modeling (price history as sequence)
- ✅ GPU acceleration
- ✅ Transfer learning capabilities

**Cons:**
- ❌ More boilerplate code
- ❌ Slower on small/medium tabular data
- ❌ Requires more ML expertise
- ❌ Complex deployment

### Implementation: LightGBM Quantile Regression

```python
import lightgbm as lgb

def train_quantile_model(features, targets, quantiles=[0.1, 0.5, 0.9]):
    """
    Train separate LightGBM model for each quantile.
    """
    models = {}

    for q in quantiles:
        params = {
            'objective': 'quantile',
            'alpha': q,
            'metric': 'quantile',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5
        }

        train_data = lgb.Dataset(features, label=targets)
        model = lgb.train(
            params,
            train_data,
            num_boost_round=500,
            valid_sets=[train_data],
            callbacks=[lgb.early_stopping(50)]
        )

        models[f'q{int(q*100)}'] = model

    return models
```

### Model Validation

**Calibration Check:**
A well-calibrated quantile model should have:
- ~10% of actual returns below q10 prediction
- ~50% of actual returns below q50 prediction
- ~90% of actual returns below q90 prediction

**Metrics:**
1. **Calibration**: % of actuals in each quantile bucket
2. **Quantile Loss**: Mean quantile loss across predictions
3. **Sharpness**: Average IQR (q90 - q10) - narrower is better if calibrated
4. **Backtest Sharpe**: Sharpe ratio of trading signals derived from predictions

---

## Signal Strength Computation

### Algorithm

Given a target return (e.g., +5%), compare against predicted quantiles to determine signal strength:

```python
def compute_signal_strength(target_return, q10, q50, q90):
    """
    Returns:
        - strength_label: 'very_weak', 'weak', 'moderate', 'strong', 'very_strong'
        - strength_score: 0-100
        - probability_estimate: rough probability of hitting target
    """

    if target_return < q10:
        # Below 10th percentile - very unlikely
        distance = (q10 - target_return) / (q90 - q10)
        score = max(0, 10 - distance * 10)
        label = 'very_weak'

    elif target_return < q50:
        # Between 10th and 50th - below median
        pct = (target_return - q10) / (q50 - q10)
        score = 10 + pct * 40
        label = 'weak' if score < 30 else 'moderate'

    elif target_return < q90:
        # Between 50th and 90th - above median
        pct = (target_return - q50) / (q90 - q50)
        score = 50 + pct * 40
        label = 'moderate' if score < 70 else 'strong'

    else:
        # Above 90th percentile - very likely
        score = min(100, 90 + 10)
        label = 'very_strong'

    return label, score
```

### Example Usage

```python
# Prediction for AAPL 30-day horizon
q10 = -2.0  # 10% chance return is below -2%
q50 = +3.0  # 50% chance return is below +3%
q90 = +8.0  # 90% chance return is above +8%

# Assess: What's the strength of hitting +5%?
label, score = compute_signal_strength(
    target_return=5.0,
    q10=-2.0,
    q50=3.0,
    q90=8.0
)

# Result: 'strong', 70
# Interpretation: +5% is between median and 90th percentile
# Signal strength: 70/100
# Estimated probability: ~70%
```

---

## Implementation Roadmap

### Phase 1: Foundation (4-6 weeks)

**Week 1-2: Feature Engineering**
- [ ] Add volume-based features (OBV, volume trend)
- [ ] Add sector/market context data
- [ ] Implement cross-sectional features
- [ ] Add consistency metrics (trend R², up-day %)

**Week 3-4: Target Computation & Training**
- [ ] Create target computation pipeline (forward returns)
- [ ] Build feature matrix from database
- [ ] Train initial LightGBM quantile models (7d, 30d, 90d)
- [ ] Validate calibration on holdout set

**Week 5-6: Prediction Pipeline**
- [ ] Implement prediction generation
- [ ] Store predictions in database
- [ ] Build signal strength computation
- [ ] Create CLI commands

### Phase 2: Validation & Backtesting (2-4 weeks)

**Week 7-8: Model Validation**
- [ ] Rolling window backtest
- [ ] Compute calibration metrics
- [ ] Analyze feature importance
- [ ] Tune hyperparameters

**Week 9-10: Signal Quality**
- [ ] Backtest trading signals
- [ ] Compute Sharpe ratio of signals
- [ ] Analyze false positives/negatives
- [ ] Refine signal strength thresholds

### Phase 3: Production (2-3 weeks)

**Week 11-12: Deployment**
- [ ] Daily prediction pipeline
- [ ] Model monitoring dashboard
- [ ] Automated retraining
- [ ] Alert system for model drift

**Week 13: Documentation & Handoff**
- [ ] User guide
- [ ] API documentation
- [ ] Model cards (transparency)

---

## CLI Command Design

### Prediction Generation

```bash
# Generate predictions for all symbols
g2 predict --horizon 30 --symbols AAPL,MSFT,GOOGL

# Generate for entire universe
g2 predict --horizon 30 --universe nasdaq

# Generate all horizons
g2 predict --horizons 7,30,90 --symbols AAPL
```

### Signal Assessment

```bash
# Assess single signal
g2 signal-assess --symbol AAPL --target 5.0 --horizon 30

# Output:
# Symbol: AAPL
# Target: +5.0% in 30 days
# Prediction Date: 2024-01-15
# Quantiles: q10=-2.0%, q50=+3.0%, q90=+8.0%
# Strength: STRONG (score: 70/100)
# Estimated Probability: 70%
```

### Signal Screening

```bash
# Find all stocks with strong signals for +5% in 30d
g2 signal-screen --target 5.0 --horizon 30 --min-strength 70

# Output: Top 20 stocks ranked by signal strength
```

### Model Validation

```bash
# Validate model performance
g2 model-validate --model-id 1 --start 2020-01-01 --end 2024-01-01

# Output:
# Calibration Metrics:
#   q10: 10.2% (target: 10%) ✓
#   q50: 49.8% (target: 50%) ✓
#   q90: 89.5% (target: 90%) ✓
# Quantile Loss: 0.0234
# Average IQR: 8.5%
# Backtest Sharpe: 1.45
```

---

## Key Decisions & Rationale

### 1. Quantile Regression vs Classification

**Decision:** Use quantile regression as primary approach, with optional trend classification as secondary.

**Rationale:**
- Quantile regression provides full distribution, enabling probabilistic assessment
- More flexible than binary up/down classification
- Allows position sizing based on risk/reward (Kelly criterion)
- Can derive classification from quantiles if needed (q50 > 0 = up trend)

### 2. Multiple Horizons (7d, 30d, 90d)

**Decision:** Start with one multi-output model (shared encoder + separate output heads for 7d/30d/90d), and fall back to separate models only if multi-output materially underperforms.

**Rationale:**
- Single training/deploy surface (one artifact, one pipeline)
- Shared representation improves sample efficiency and consistency across horizons
- Still allows horizon-specific specialization via separate heads
- Fallback is straightforward if validation loss is meaningfully worse than separate models

### 2b. Configuration Storage (Dataset/Run Lineage)

**Decision:** Store MVP configuration in dedicated tables (`ml_datasets`, `ml_runs`) rather than embedding it only in code or in `ml_models`.

**Rationale:**
- Keeps the `g2` CLI flexible while maintaining reproducibility (“what data and labels produced this?”)
- Makes it easy to track and compare experiments, predictions, and evaluations over time
- Provides a clean join path: dataset → train run → model → predict run → predictions → eval run → outcomes/metrics

### 3. LightGBM First, PyTorch Later

**Decision:** Start with LightGBM, experiment with PyTorch only if needed.

**Rationale:**
- Stock features are tabular (RSI, MACD, etc.), not sequences
- LightGBM is faster and simpler for tabular data
- 90% of performance with 10% of effort
- Can always add PyTorch later if sequential modeling is needed

### 4. Cross-Sectional Features

**Decision:** Include sector/market-relative features.

**Rationale:**
- Absolute indicators miss context (RSI=70 in bull market vs bear market)
- Relative strength often predicts better than absolute
- Enables sector rotation strategies
- Industry standard in quant finance

---

## Success Metrics

### Model Performance

1. **Calibration**: q10/q50/q90 coverage within ±2% of target
2. **Quantile Loss**: < 0.03 across all horizons
3. **Backtest Sharpe**: > 1.0 for signal-based strategies

### Business Impact

1. **Signal Accuracy**: > 60% win rate for strong signals (score > 70)
2. **Risk Assessment**: 90% of losses captured in q10 downside
3. **Portfolio Construction**: Enable risk-adjusted position sizing

### Technical Quality

1. **Prediction Latency**: < 100ms per symbol
2. **Model Retraining**: Automated weekly
3. **Data Pipeline**: < 1 hour for full universe update

---

## Future Enhancements

### Short-Term (3-6 months)

1. **Ensemble Models**: Combine LightGBM + XGBoost predictions
2. **Feature Selection**: Automated feature importance analysis
3. **Hyperparameter Optimization**: Optuna/Ray Tune integration
4. **Real-time Updates**: Streaming predictions as new data arrives

### Medium-Term (6-12 months)

1. **PyTorch Sequential Models**: LSTM on price sequences
2. **Multi-task Learning**: Single model for all horizons + trend
3. **Transfer Learning**: Pre-train on all stocks, fine-tune per sector
4. **Fundamental Features**: Incorporate P/E, revenue growth, etc.

### Long-Term (12+ months)

1. **Options Pricing**: Extend to options implied volatility
2. **Portfolio Optimization**: Integrate with Markowitz mean-variance
3. **Reinforcement Learning**: Learn optimal trading policies
4. **Alternative Data**: News sentiment, social media, satellite imagery

---

## References

### Academic Papers

- Koenker, R. (2005). "Quantile Regression." Cambridge University Press.
- Gu, S., Kelly, B., & Xiu, D. (2020). "Empirical Asset Pricing via Machine Learning." Review of Financial Studies.
- Harvey, C. R., & Liu, Y. (2020). "Lucky Factors." Journal of Financial Economics.

### Technical Resources

- LightGBM Documentation: https://lightgbm.readthedocs.io/
- TimescaleDB Best Practices: https://docs.timescale.com/
- Quantile Regression Tutorial: https://scikit-learn.org/stable/modules/linear_model.html#quantile-regression

### Internal Documentation

- [FEATURE_DISPATCHER.md](FEATURE_DISPATCHER.md) - Feature computation architecture
- [DERIVATIVE_FEATURES.md](DERIVATIVE_FEATURES.md) - Derivative feature design
- [FUTURE_DIRECTIONS.md](FUTURE_DIRECTIONS.md) - Long-term vision

---

## Appendix: Example Queries

### Fetch Latest Predictions

```sql
SELECT
    s.symbol,
    qp.prediction_date,
    qp.horizon_days,
    qp.q10,
    qp.q50,
    qp.q90,
    (qp.q90 - qp.q10) as iqr
FROM quantile_predictions qp
JOIN stocks s ON s.id = qp.data_id
WHERE qp.prediction_date = (
    SELECT MAX(prediction_date)
    FROM quantile_predictions
    WHERE horizon_days = 30
)
AND qp.horizon_days = 30
ORDER BY (qp.q90 - qp.q10) DESC  -- Widest distribution first
LIMIT 20;
```

### Validate Model Calibration

```sql
WITH outcomes AS (
    SELECT
        qp.q10,
        qp.q50,
        qp.q90,
        po.actual_return,
        CASE
            WHEN po.actual_return < qp.q10 THEN 'below_q10'
            WHEN po.actual_return < qp.q50 THEN 'q10_to_q50'
            WHEN po.actual_return < qp.q90 THEN 'q50_to_q90'
            ELSE 'above_q90'
        END as bucket
    FROM quantile_predictions qp
    JOIN prediction_outcomes po
        ON po.data_id = qp.data_id
        AND po.prediction_date = qp.prediction_date
        AND po.horizon_days = qp.horizon_days
    WHERE qp.model_id = 1
)
SELECT
    bucket,
    COUNT(*) as count,
    COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () as percentage
FROM outcomes
GROUP BY bucket;

-- Expected output:
-- below_q10: ~10%
-- q10_to_q50: ~40%
-- q50_to_q90: ~40%
-- above_q90: ~10%
```

---

**Last Updated:** 2024-12-04
**Authors:** System Design Discussion
**Next Review:** After Phase 1 implementation
