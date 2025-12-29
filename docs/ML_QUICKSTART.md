# ML Quickstart Guide

Complete walkthrough for training and deploying quantile regression models in g2.

## What is g2's ML Pipeline?

g2 predicts **return distributions** instead of single-value forecasts. This approach provides:

**Key Concepts:**

- **Features**: Input data used to make predictions (technical indicators like RSI, MACD, price changes, volume, sentiment scores, etc.)
- **Labels**: What we're predicting - future returns at different time horizons (7, 30, 90 days by default, fully configurable)
- **Quantile Regression**: Instead of predicting "stock will return 5%", predict "10% chance of worse than -2%, 50% chance of worse than +0.8%, 90% chance of worse than +3.5%"
- **Horizons**: Time periods for predictions (e.g., 7-day, 30-day, 90-day returns) - **fully configurable via `--horizons`**
- **Multi-Horizon**: Train separate models for each time period to capture different market dynamics

**Why Quantiles vs Point Estimates?**

Traditional models predict: "AAPL will return +3.2% in 7 days"

g2 predicts: "AAPL 7-day distribution: q10=-1.5%, q50=+2.1%, q90=+5.8%"

This enables:
- **Risk assessment**: Know downside risk (q10) and upside potential (q90)
- **Position sizing**: Size positions based on uncertainty (q90-q10 spread)
- **Portfolio construction**: Combine stocks with different risk/return profiles
- **Screening**: Filter by risk-adjusted metrics, not just expected return

**Current Implementation:**

- ✅ Quantile regression (q10, q50, q90 predictions)
- ✅ Multi-horizon forecasts (configurable horizons)
- ✅ Trend strength labels computed (weak/strong up/down movements)
- ✅ Trend classification model (XGBoost/LightGBM classifiers)
- ✅ Model ensembles (combine multiple algorithms for better accuracy)
- ✅ Hyperparameter tuning (Optuna integration)
- ✅ Feature importance analysis (SHAP-based)

## Prerequisites

1. **Database running** with prices and features:
   ```bash
   docker compose up -d postgres
   g2 data-update --exchange NASDAQ --timeframe auto --local
   ```

2. **ML dependencies installed**:
   ```bash
   pip install -e .  # Installs scikit-learn, joblib, numpy
   ```

3. **Optional: XGBoost/LightGBM** (for advanced algorithms):
   ```bash
   pip install 'g2[ml_extended]'
   ```

   **When to use extended algorithms:**
   - **Start with `quantile_regression`**: Fast, simple, good for prototyping and linear relationships
   - **Upgrade to `xgboost`**: Better accuracy for non-linear patterns, worth the extra training time for production
   - **Use `lightgbm`**: When you have very large datasets (>100K samples) or training time is critical

## Quick Start (5 Minutes)

### 1. Build Training Dataset

Export features and labels for a small universe:

```bash
g2 ml dataset-build \
  --name quickstart \
  --version v1 \
  --symbols AAPL,MSFT,GOOGL,AMZN,META \
  --horizons 7,30 \
  --weak-thresholds 0.02,0.05 \
  --strong-thresholds 0.05,0.10 \
  --out-dir datasets/quickstart \
  --export
```

**Note:** Horizons are fully configurable! Use any comma-separated list of days:
- Short-term: `--horizons 3,7,14`
- Long-term: `--horizons 30,60,90,180`
- Mixed: `--horizons 7,21,60` (weekly, monthly, quarterly)

**Output:**
- `datasets/quickstart/manifest.json` - Dataset metadata
- `datasets/quickstart/prices.csv` - Historical OHLCV data
- `datasets/quickstart/features.csv` - Technical indicators (long format)
- `datasets/quickstart/labels.csv` - Forward returns for 7/30-day horizons
- Registered in `ml_datasets` table

### 2. Train Model

Train quantile regression models:

```bash
g2 ml train \
  --dataset-name quickstart \
  --dataset-version v1 \
  --model-name quickstart_model \
  --model-version $(date +%Y%m%d) \
  --algorithm quantile_regression \
  --out-dir models
```

**What happens:**
- Loads features and pivots to wide format
- Trains 3 quantile models (q10, q50, q90) for each horizon (7-day, 30-day)
- Saves 6 model files total: `models/quickstart_model_YYYYMMDD_h7/*.joblib` and `models/quickstart_model_YYYYMMDD_h30/*.joblib`
- Registers in `ml_models` table with training metrics
- Creates run record in `ml_runs`

**Training time:** ~5-15 seconds for 10K samples

### 3. Generate Predictions

Generate predictions for today:

```bash
g2 ml predict \
  --model-name quickstart_model \
  --model-version $(date +%Y%m%d) \
  --prediction-date $(date +%Y-%m-%d) \
  --symbols AAPL,MSFT,GOOGL,AMZN,META
```

**What happens:**
- Fetches latest features from `computed_features` for the symbols
- Loads model artifacts for both horizons
- Generates predictions: q10 (pessimistic), q50 (median), q90 (optimistic)
- Stores in `quantile_predictions` table
- Example prediction: AAPL 7-day (q10=-2.1%, q50=0.8%, q90=3.5%)

**Prediction time:** ~450ms for 5 symbols

### 4. Evaluate Performance

Evaluate on historical predictions (if you have past predictions):

```bash
g2 ml eval \
  --model-name quickstart_model \
  --model-version $(date +%Y%m%d) \
  --start-date 2024-01-01 \
  --end-date 2024-11-30
```

**What happens:**
- Fetches predictions from `quantile_predictions`
- Calculates actual returns from price data
- Computes calibration metrics
- Generates evaluation report
- Stores summary in `model_performance`

**Evaluation report example:**
```
======================================================================
Model Evaluation Report: quickstart_model
======================================================================

Horizon: 7 days
--------------------------------------------------
  Samples:              156
  Q10 Calibration:      11.5% (target: 10%, error: 1.5%)
  Q50 Calibration:      49.2% (target: 50%, error: 0.8%)
  Q90 Calibration:      88.5% (target: 90%, error: 1.5%)
  80% Interval Coverage: 77.6% (target: 80%)
  Quantile Loss:        0.0234
  Avg IQR:              0.0456
```

## Production Workflow

### Full Dataset (500 Symbols)

```bash
# 1. Build dataset with larger universe
g2 ml dataset-build \
  --name nasdaq_500 \
  --version v1 \
  --exchange NASDAQ \
  --limit 500 \
  --horizons 7,30,90 \
  --weak-thresholds 0.02,0.05,0.10 \
  --strong-thresholds 0.05,0.10,0.20 \
  --out-dir datasets/nasdaq_500 \
  --export

# 2. Train with XGBoost for better accuracy
g2 ml train \
  --dataset-name nasdaq_500 \
  --dataset-version v1 \
  --model-name nasdaq_xgb \
  --model-version $(date +%Y%m%d) \
  --algorithm xgboost \
  --out-dir models

# 3. Daily prediction cron job
g2 ml predict \
  --model-name nasdaq_xgb \
  --model-version $(date +%Y%m%d) \
  --prediction-date $(date +%Y-%m-%d) \
  --exchange NASDAQ \
  --limit 500

# 4. Weekly evaluation
g2 ml eval \
  --model-name nasdaq_xgb \
  --model-version $(date +%Y%m%d) \
  --start-date $(date -d '30 days ago' +%Y-%m-%d) \
  --end-date $(date +%Y-%m-%d)
```

### Feature Selection

By default, all computed features are included in the dataset. You can customize feature selection:

**Whitelist Mode** (include only specific features):

```bash
g2 ml dataset-build \
  --name selective \
  --version v1 \
  --symbols AAPL,MSFT,GOOGL \
  --horizons 7,30 \
  --features indicator_rsi_14,indicator_macd,indicator_bollinger_bands \
  --export
```

**Blacklist Mode** (exclude specific features):

```bash
g2 ml dataset-build \
  --name filtered \
  --version v1 \
  --symbols AAPL,MSFT,GOOGL \
  --horizons 7,30 \
  --exclude-features indicator_obv,indicator_adx \
  --export
```

**Notes:**

- Cannot use both `--features` and `--exclude-features` together
- Feature names must match those in `feature_definitions` table
- Non-existent feature names are silently ignored
- Use `SELECT DISTINCT name FROM feature_definitions;` to list available features

## Understanding the Output

### Quantile Predictions

Each prediction contains three values representing the return distribution:

- **q10** (10th percentile): "Pessimistic" scenario - 10% of outcomes are worse than this
- **q50** (50th percentile): Median expected return
- **q90** (90th percentile): "Optimistic" scenario - 90% of outcomes are worse than this (only 10% are better)

**Example:** AAPL 7-day prediction
```
q10 = -2.1%  →  10% chance of losing more than 2.1%
q50 =  0.8%  →  Median expected gain of 0.8%
q90 =  3.5%  →  10% chance of gaining more than 3.5%
```

### Calibration Metrics

Good calibration means the predicted quantiles match empirical coverage:

- **q50_calibration = 50%** → Perfect! Half of actuals are below q50 prediction
- **q10_calibration = 15%** → Overconfident! Should be 10% (predicting too pessimistically)
- **q90_calibration = 85%** → Underconfident! Should be 90% (predicting too conservatively)

**Target errors:**
- Excellent: < 2% error
- Good: 2-5% error
- Needs improvement: > 5% error

### Pinball Loss

Lower is better. Measures average prediction error weighted by quantile.

- **< 0.02**: Excellent calibration
- **0.02-0.05**: Good calibration
- **> 0.05**: Needs improvement

## Algorithm Comparison

| Algorithm | Speed | Accuracy | Memory | Use Case |
|-----------|-------|----------|--------|----------|
| quantile_regression | ⚡⚡⚡ Fast | ⭐⭐ Good | 💾 Low | Quick prototypes, linear relationships |
| xgboost | ⚡⚡ Medium | ⭐⭐⭐ Better | 💾💾 Medium | Production models, non-linear patterns |
| lightgbm | ⚡⚡⚡ Fast | ⭐⭐⭐ Better | 💾 Low | Large datasets (>100K samples) |

**Recommendation:**
- Start with `quantile_regression` for prototyping
- Switch to `xgboost` for production after validating the pipeline
- Use `lightgbm` for very large datasets or when training time matters

## Model Ensembles

Combine predictions from multiple algorithms for improved accuracy. Ensembles reduce variance and leverage the strengths of different approaches.

### Training an Ensemble

```bash
# Train ensemble with two algorithms
g2 ml train-ensemble \
  --dataset-name nasdaq_500 \
  --dataset-version v1 \
  --model-name nasdaq_ensemble \
  --model-version $(date +%Y%m%d) \
  --algorithms xgboost,lightgbm \
  --out-dir models

# Train with custom weights (XGBoost weighted higher)
g2 ml train-ensemble \
  --dataset-name nasdaq_500 \
  --dataset-version v1 \
  --model-name weighted_ensemble \
  --model-version $(date +%Y%m%d) \
  --algorithms xgboost,lightgbm \
  --weights 0.6,0.4 \
  --out-dir models
```

**What happens:**
- Trains each algorithm separately on the same data
- Creates ensemble metadata linking the base models
- Stores weights for weighted averaging during prediction
- Registers as `algorithm=ensemble` in `ml_models` table

### Generating Ensemble Predictions

```bash
g2 ml predict-ensemble \
  --model-name nasdaq_ensemble \
  --model-version $(date +%Y%m%d) \
  --prediction-date $(date +%Y-%m-%d) \
  --exchange NASDAQ \
  --limit 500
```

**What happens:**
- Loads each base model for the horizon
- Generates predictions from each model
- Computes weighted average: `q50_ensemble = w1*q50_xgb + w2*q50_lgb`
- Enforces quantile ordering (q10 ≤ q50 ≤ q90)
- Stores in `quantile_predictions` table

### When to Use Ensembles

| Scenario | Recommendation |
|----------|----------------|
| Prototyping | Single model (`quantile_regression`) |
| Production with moderate data | Single `xgboost` model |
| Maximum accuracy needed | Ensemble of `xgboost,lightgbm` |
| Uncertainty about best algorithm | Ensemble with equal weights |

**Benefits:**
- Reduces prediction variance
- More robust to outliers
- Combines linear + non-linear patterns

## Querying Predictions

### SQL Examples

**Latest predictions for a symbol:**
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
JOIN stocks s ON qp.data_id = s.id
WHERE s.symbol = 'AAPL'
    AND qp.model_id = (SELECT id FROM ml_models WHERE name = 'quickstart_model' LIMIT 1)
ORDER BY qp.prediction_date DESC, qp.horizon_days
LIMIT 10;
```

**Top 10 stocks by upside potential (q90 - q50):**
```sql
SELECT
    s.symbol,
    qp.q50 as median_return,
    qp.q90 as optimistic_return,
    (qp.q90 - qp.q50) as upside_potential,
    (qp.q90 - qp.q10) as uncertainty
FROM quantile_predictions qp
JOIN stocks s ON qp.data_id = s.id
WHERE qp.prediction_date = CURRENT_DATE
    AND qp.horizon_days = 7
    AND qp.model_id = (SELECT id FROM ml_models WHERE name = 'quickstart_model' LIMIT 1)
ORDER BY upside_potential DESC
LIMIT 10;
```

## Troubleshooting

### "No features found for prediction date"

**Cause:** Features haven't been computed for that date yet.

**Solution:**
```bash
g2 feat-compute --exchange NASDAQ --local --refresh-existing
```

### "Dataset not found"

**Cause:** Dataset name/version mismatch or not registered.

**Solution:**
```bash
# List datasets
g2 ml dataset-list

# Rebuild if needed
g2 ml dataset-build --name ... --export
```

### "Model artifact directory not found"

**Cause:** Model path structure mismatch (missing _hN suffix).

**Solution:** Check model artifacts exist in the expected location:
```bash
ls -la models/your_model_version_h7/
ls -la models/your_model_version_h30/
```

### Poor calibration (q50 calibration far from 50%)

**Causes:**
- Insufficient training data
- Feature drift (training data distribution ≠ prediction data)
- Look-ahead bias in features

**Solutions:**
- Increase dataset size (more symbols, longer history)
- Retrain model more frequently
- Verify features use only past data (no future peeking)

## Next Steps

1. **Backtest predictions** - Compare predicted vs actual returns over time
2. **Feature engineering** - Add custom indicators to improve accuracy
3. **Model versioning** - Track model performance across versions
4. **Automated retraining** - Set up cron jobs to retrain weekly/monthly
5. **Signal generation** - Convert predictions into trading signals (e.g., buy when q50 > 2% and q10 > 0%)

## End-to-End Testing

Validate the entire ML pipeline with a single command:

```bash
# Quick smoke test (~1 minute)
g2 ml e2e-test --exchange NASDAQ --limit 10

# Full test with cleanup
g2 ml e2e-test --exchange NASDAQ --limit 50 --cleanup
```

This runs all steps automatically: data update → dataset build → train → ensemble → predict → quality check.

See [E2E Test Guide](E2E_TEST_GUIDE.md) for details.

## Trend Classification

Train a classifier to predict trend strength categories (strong_up, weak_up, flat, weak_down, strong_down):

```bash
# Train trend classifier
g2 ml train-classifier \
  --dataset-name nasdaq_500 \
  --dataset-version v1 \
  --model-name trend_classifier \
  --model-version $(date +%Y%m%d) \
  --algorithm xgboost \
  --out-dir models

# Generate trend predictions
g2 ml predict-classifier \
  --model-path models/trend_classifier_YYYYMMDD_h7 \
  --symbols AAPL,MSFT,GOOGL
```

**Combined Strategy** - Use both quantile predictions and trend classification:

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
ORDER BY qp.q90 DESC
LIMIT 20;
```

## Parquet Export

Dataset exports support both CSV (default) and Parquet formats. Use Parquet for:
- **Better compression**: 5-10x smaller files
- **Type preservation**: Maintains int64, float64 types (CSV converts to strings)
- **Faster I/O**: 5-10x faster read/write with columnar format
- **Industry standard**: Compatible with pandas, polars, spark, and ML frameworks

**Usage:**

```bash
# Export as Parquet (recommended for large datasets)
g2 ml dataset-build \
  --name tech --version v1 \
  --symbols AAPL,MSFT,GOOGL \
  --horizons 7,30 \
  --format parquet \
  --export

# CSV is still the default (backward compatible)
g2 ml dataset-build \
  --name tech --version v1 \
  --symbols AAPL,MSFT,GOOGL \
  --horizons 7,30 \
  --export
```

**Requirements:**
- Install with: `pip install g2[ml_extended]` (includes `pyarrow>=14.0`)

**Output:**
- Parquet: `prices.parquet`, `features.parquet`, `labels.parquet`
- CSV: `prices.csv`, `features.csv`, `labels.csv`

## Reference

- [E2E Test Guide](E2E_TEST_GUIDE.md) - Automated pipeline testing
- [ML Roadmap](ML_ROADMAP.md) - Feature status and future enhancements
- [ML System Design](archive/ml/ML_SYSTEM_DESIGN.md) - Database schema and architecture
- [User Guide](USER_GUIDE.md) - Full CLI reference
- [Architecture](ARCHITECTURE.md) - Overall system design
