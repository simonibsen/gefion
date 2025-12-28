# End-to-End ML Pipeline Test Guide

Complete walkthrough to test the full ML pipeline including ensembles on a GPU system.

## Prerequisites

```bash
# 1. Verify database is running
docker compose ps

# 2. Verify g2 CLI is installed
g2 --version

# 3. Check system status
g2 system-status
```

## Step 1: Update Price Data

```bash
# Update NASDAQ prices (uses AlphaVantage API)
g2 data-update --exchange NASDAQ --limit 50

# Verify data was ingested
g2 query-database --sql "SELECT COUNT(*) as symbols FROM stocks WHERE exchange = 'NASDAQ'"
g2 query-database --sql "SELECT COUNT(*) as price_rows FROM stock_ohlcv"
```

**Expected:** 50 symbols, thousands of price rows.

## Step 2: Build Dataset

```bash
# Build dataset with 50 NASDAQ symbols, 7 and 30 day horizons
g2 ml dataset-build \
  --name e2e_test \
  --version v1 \
  --exchange NASDAQ \
  --limit 50 \
  --horizons 7,30 \
  --format parquet \
  --export

# Verify dataset was created
g2 query-database --sql "SELECT name, version, num_symbols, horizons_days FROM ml_datasets WHERE name = 'e2e_test'"
```

**Expected:** Dataset registered with ~50 symbols and horizons [7, 30].

## Step 3: Train Single Model (Baseline)

```bash
# Train XGBoost model as baseline
g2 ml train \
  --dataset-name e2e_test \
  --dataset-version v1 \
  --model-name e2e_xgboost \
  --model-version v1 \
  --algorithm xgboost \
  --out-dir models

# Verify model was registered
g2 query-database --sql "SELECT name, version, algorithm FROM ml_models WHERE name = 'e2e_xgboost'"
```

**Expected:** Model trained for both 7-day and 30-day horizons.

## Step 4: Train Ensemble Model

```bash
# Train ensemble combining XGBoost and LightGBM
g2 ml train-ensemble \
  --dataset-name e2e_test \
  --dataset-version v1 \
  --model-name e2e_ensemble \
  --model-version v1 \
  --algorithms xgboost,lightgbm \
  --out-dir models

# Verify ensemble was registered
g2 query-database --sql "SELECT name, version, algorithm, hyperparams FROM ml_models WHERE name = 'e2e_ensemble'"
```

**Expected:** Ensemble registered with `algorithm=ensemble` and hyperparams showing algorithms and weights.

## Step 5: Generate Predictions

```bash
# Get latest date with features
PRED_DATE=$(g2 query-database --sql "SELECT MAX(date) FROM computed_features" | tail -1 | tr -d ' ')
echo "Prediction date: $PRED_DATE"

# Generate predictions with single model
g2 ml predict \
  --model-name e2e_xgboost \
  --model-version v1 \
  --prediction-date $PRED_DATE \
  --exchange NASDAQ \
  --limit 50

# Generate predictions with ensemble
g2 ml predict-ensemble \
  --model-name e2e_ensemble \
  --model-version v1 \
  --prediction-date $PRED_DATE \
  --exchange NASDAQ \
  --limit 50

# Verify predictions were stored
g2 query-database --sql "
SELECT m.name, COUNT(*) as predictions
FROM quantile_predictions qp
JOIN ml_models m ON qp.model_id = m.id
WHERE m.name IN ('e2e_xgboost', 'e2e_ensemble')
GROUP BY m.name
"
```

**Expected:** Both models should have predictions stored (50 symbols x 2 horizons = 100 predictions each).

## Step 6: Compare Predictions

```bash
# Compare single model vs ensemble predictions
g2 query-database --sql "
SELECT
    s.symbol,
    'xgboost' as model,
    qp.horizon_days,
    ROUND(qp.q10::numeric, 4) as q10,
    ROUND(qp.q50::numeric, 4) as q50,
    ROUND(qp.q90::numeric, 4) as q90
FROM quantile_predictions qp
JOIN stocks s ON qp.data_id = s.id
JOIN ml_models m ON qp.model_id = m.id
WHERE m.name = 'e2e_xgboost'
  AND qp.horizon_days = 7
ORDER BY qp.q50 DESC
LIMIT 5
"

g2 query-database --sql "
SELECT
    s.symbol,
    'ensemble' as model,
    qp.horizon_days,
    ROUND(qp.q10::numeric, 4) as q10,
    ROUND(qp.q50::numeric, 4) as q50,
    ROUND(qp.q90::numeric, 4) as q90
FROM quantile_predictions qp
JOIN stocks s ON qp.data_id = s.id
JOIN ml_models m ON qp.model_id = m.id
WHERE m.name = 'e2e_ensemble'
  AND qp.horizon_days = 7
ORDER BY qp.q50 DESC
LIMIT 5
"
```

**Expected:** Ensemble predictions should be slightly different (weighted average of XGBoost + LightGBM).

## Step 7: Feature Importance (Optional)

```bash
# Analyze which features drive predictions
g2 ml feature-importance \
  --model-name e2e_xgboost \
  --model-version v1 \
  --horizon 7 \
  --top-k 10
```

## Step 8: Hyperparameter Tuning (Optional)

```bash
# Find optimal hyperparameters with Optuna
g2 ml tune \
  --dataset-name e2e_test \
  --dataset-version v1 \
  --algorithm xgboost \
  --n-trials 20 \
  --timeout 300
```

## Cleanup (Optional)

```bash
# Remove test models and datasets
g2 query-database --sql "DELETE FROM quantile_predictions WHERE model_id IN (SELECT id FROM ml_models WHERE name LIKE 'e2e_%')"
g2 query-database --sql "DELETE FROM ml_models WHERE name LIKE 'e2e_%'"
g2 query-database --sql "DELETE FROM ml_datasets WHERE name = 'e2e_test'"

# Remove model artifacts
rm -rf models/e2e_*
rm -rf datasets/e2e_test
```

## Success Criteria

| Step | Check |
|------|-------|
| Data Update | 50 symbols ingested |
| Dataset Build | Dataset registered in ml_datasets |
| Single Model | XGBoost model trained for 2 horizons |
| Ensemble | Ensemble with 2 base models trained |
| Predictions | Both models generated predictions |
| Comparison | Ensemble predictions differ from single model |

## Troubleshooting

### "XGBoost Library could not be loaded"

Install OpenMP runtime:
```bash
# macOS
brew install libomp

# Linux
apt-get install libgomp1
```

### "No features found for prediction date"

Features haven't been computed for the date:
```bash
g2 data-update --exchange NASDAQ --limit 50
```

### "Model not found"

Check model was registered:
```bash
g2 query-database --sql "SELECT * FROM ml_models ORDER BY created_at DESC LIMIT 5"
```
