# End-to-End ML Pipeline Test Guide

Complete walkthrough to test the full ML pipeline including ensembles.

## Quick Start

Run the automated e2e test with a single command:

```bash
# Quick smoke test (10 NASDAQ symbols, ~2-5 minutes)
gefion ml e2e-test

# Test with more symbols
gefion ml e2e-test --limit 50

# Skip data update if data is already fresh
gefion ml e2e-test --skip-data-update

# Clean up test artifacts after completion
gefion ml e2e-test --cleanup

# Full options
gefion ml e2e-test --exchange NASDAQ --limit 50 --name my_test --cleanup
```

The command runs all 7 pipeline steps automatically:
1. **Data Update** - Fetch price data from AlphaVantage
2. **Dataset Build** - Create ML dataset with features and labels
3. **Train Model** - Train single XGBoost model
4. **Train Ensemble** - Train ensemble (XGBoost + LightGBM)
5. **Predict** - Generate predictions with single model
6. **Predict Ensemble** - Generate predictions with ensemble
7. **Quality Check** - Validate prediction quality metrics

## Prerequisites

```bash
# 1. Verify database is running
docker compose ps

# 2. Verify Gefion CLI is installed
gefion --version

# 3. Check system status
gefion system-status
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
| Predictions | Single model generated predictions (count > 0) |
| Ensemble Predictions | Ensemble generated predictions (count > 0) |
| Quality Check | IQR reasonable, quantile ordering valid |

## Quality Metrics

The quality check step validates prediction quality:

- **Average IQR** - Average interquartile range (q90 - q10). Measures prediction confidence/uncertainty. Typical values: 5-20% for stock returns.
- **Ordering Valid** - Confirms q10 ≤ q50 ≤ q90 for all predictions. Should always be true for properly trained models.

Example output:
```
[7/7] Validating prediction quality...
  ✓ quality_check completed - IQR: 8.3%, ordering: OK

Artifacts created:
  predictions_count: 20
  ensemble_predictions_count: 20
  quality: {avg_iqr: 0.083, ordering_valid: true, ...}
```

**Interpreting IQR:**
- **< 5%**: Very confident predictions (tight range)
- **5-15%**: Normal uncertainty
- **> 20%**: High uncertainty (volatile stocks or uncertain conditions)

## Manual Steps (Reference)

For debugging or custom testing, here are the individual steps:

### Step 1: Update Price Data

```bash
gefion data-update --exchange NASDAQ --limit 50

# Verify
psql $DATABASE_URL -c "SELECT COUNT(*) FROM stocks WHERE exchange = 'NASDAQ'"
```

### Step 2: Build Dataset

```bash
gefion ml dataset-build \
  --name e2e_test \
  --version v1 \
  --exchange NASDAQ \
  --limit 50 \
  --horizons 7,30

# Verify
psql $DATABASE_URL -c "SELECT name, version, num_symbols FROM ml_datasets WHERE name = 'e2e_test'"
```

### Step 3: Train Single Model

```bash
gefion ml train \
  --dataset-name e2e_test \
  --dataset-version v1 \
  --model-name e2e_xgboost \
  --model-version v1 \
  --algorithm xgboost
```

### Step 4: Train Ensemble

```bash
gefion ml train-ensemble \
  --dataset-name e2e_test \
  --dataset-version v1 \
  --model-name e2e_ensemble \
  --model-version v1 \
  --algorithms xgboost,lightgbm
```

### Step 5: Generate Predictions

```bash
# Get latest date with features
PRED_DATE=$(psql $DATABASE_URL -t -c "SELECT MAX(date) FROM computed_features" | tr -d ' ')

# Single model predictions
gefion ml predict \
  --model-name e2e_xgboost \
  --model-version v1 \
  --prediction-date $PRED_DATE \
  --exchange NASDAQ \
  --limit 50

# Ensemble predictions
gefion ml predict-ensemble \
  --model-name e2e_ensemble \
  --model-version v1 \
  --prediction-date $PRED_DATE \
  --exchange NASDAQ \
  --limit 50
```

### Step 6: Verify Predictions

```bash
psql $DATABASE_URL -c "
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
psql $DATABASE_URL -c "DELETE FROM quantile_predictions WHERE model_id IN (SELECT id FROM ml_models WHERE name LIKE 'e2e_%')"
psql $DATABASE_URL -c "DELETE FROM ml_models WHERE name LIKE 'e2e_%'"
psql $DATABASE_URL -c "DELETE FROM ml_datasets WHERE name = 'e2e_test'"

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
gefion data-update --exchange NASDAQ --limit 50
```

### "Model not found"

Check model was registered:
```bash
psql $DATABASE_URL -c "SELECT * FROM ml_models ORDER BY created_at DESC LIMIT 5"
```
