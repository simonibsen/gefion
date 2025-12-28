# End-to-End ML Pipeline Test Guide

Complete walkthrough to test the full ML pipeline including ensembles.

## Quick Start

Run the automated e2e test with a single command:

```bash
# Quick smoke test (10 NASDAQ symbols, ~2-5 minutes)
g2 ml e2e-test

# Test with more symbols
g2 ml e2e-test --limit 50

# Skip data update if data is already fresh
g2 ml e2e-test --skip-data-update

# Clean up test artifacts after completion
g2 ml e2e-test --cleanup

# Full options
g2 ml e2e-test --exchange NASDAQ --limit 50 --name my_test --cleanup
```

The command runs all 6 pipeline steps automatically:
1. **Data Update** - Fetch price data from AlphaVantage
2. **Dataset Build** - Create ML dataset with features and labels
3. **Train Model** - Train single XGBoost model
4. **Train Ensemble** - Train ensemble (XGBoost + LightGBM)
5. **Predict** - Generate predictions with single model
6. **Predict Ensemble** - Generate predictions with ensemble

## Prerequisites

```bash
# 1. Verify database is running
docker compose ps

# 2. Verify g2 CLI is installed
g2 --version

# 3. Check system status
g2 system-status
```

## MCP Server

The e2e test is also available via MCP:

```
ml_e2e_test(exchange="NASDAQ", limit=10, skip_data_update=false, cleanup=false)
```

## Success Criteria

| Step | Check |
|------|-------|
| Data Update | Symbols ingested from AlphaVantage |
| Dataset Build | Dataset registered in ml_datasets |
| Single Model | XGBoost model trained for all horizons |
| Ensemble | Ensemble with 2 base models trained |
| Predictions | Single model generated predictions |
| Ensemble Predictions | Ensemble generated predictions |

## Manual Steps (Reference)

For debugging or custom testing, here are the individual steps:

### Step 1: Update Price Data

```bash
g2 data-update --exchange NASDAQ --limit 50

# Verify
g2 query-database --sql "SELECT COUNT(*) FROM stocks WHERE exchange = 'NASDAQ'"
```

### Step 2: Build Dataset

```bash
g2 ml dataset-build \
  --name e2e_test \
  --version v1 \
  --exchange NASDAQ \
  --limit 50 \
  --horizons 7,30

# Verify
g2 query-database --sql "SELECT name, version, num_symbols FROM ml_datasets WHERE name = 'e2e_test'"
```

### Step 3: Train Single Model

```bash
g2 ml train \
  --dataset-name e2e_test \
  --dataset-version v1 \
  --model-name e2e_xgboost \
  --model-version v1 \
  --algorithm xgboost
```

### Step 4: Train Ensemble

```bash
g2 ml train-ensemble \
  --dataset-name e2e_test \
  --dataset-version v1 \
  --model-name e2e_ensemble \
  --model-version v1 \
  --algorithms xgboost,lightgbm
```

### Step 5: Generate Predictions

```bash
# Get latest date with features
PRED_DATE=$(g2 query-database --sql "SELECT MAX(date) FROM computed_features" | tail -1 | tr -d ' ')

# Single model predictions
g2 ml predict \
  --model-name e2e_xgboost \
  --model-version v1 \
  --prediction-date $PRED_DATE \
  --exchange NASDAQ \
  --limit 50

# Ensemble predictions
g2 ml predict-ensemble \
  --model-name e2e_ensemble \
  --model-version v1 \
  --prediction-date $PRED_DATE \
  --exchange NASDAQ \
  --limit 50
```

### Step 6: Verify Predictions

```bash
g2 query-database --sql "
SELECT m.name, COUNT(*) as predictions
FROM quantile_predictions qp
JOIN ml_models m ON qp.model_id = m.id
WHERE m.name IN ('e2e_xgboost', 'e2e_ensemble')
GROUP BY m.name
"
```

### Cleanup

```bash
# Remove test artifacts from database
g2 query-database --sql "DELETE FROM quantile_predictions WHERE model_id IN (SELECT id FROM ml_models WHERE name LIKE 'e2e_%')"
g2 query-database --sql "DELETE FROM ml_models WHERE name LIKE 'e2e_%'"
g2 query-database --sql "DELETE FROM ml_datasets WHERE name = 'e2e_test'"

# Remove model files
rm -rf models/e2e_*
rm -rf datasets/e2e_test
```

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
