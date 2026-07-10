# Gefion MCP Server Workflows

Comprehensive end-to-end workflows for using the G2 MCP Server through Claude Desktop.

## Table of Contents

1. [Complete ML Pipeline: Quantile Regression](#complete-ml-pipeline-quantile-regression)
2. [Trend Classification System](#trend-classification-system)
3. [Combined Signal System](#combined-signal-system)
4. [Model Performance Monitoring](#model-performance-monitoring)
5. [Data Quality & Exploration](#data-quality--exploration)
6. [Production Deployment Patterns](#production-deployment-patterns)
7. [Performance Monitoring & Observability](#performance-monitoring--observability)

---

## Complete ML Pipeline: Quantile Regression

**Goal:** Build a quantile regression model for multi-horizon return prediction.

### Step 1: Data Preparation

**Prompt:**
```
Update prices and features for NASDAQ exchange, limit to 100 symbols.
Use local computation for faster processing.
```

**What happens:**
- Fetches latest OHLCV data from AlphaVantage
- Time-aware: before 4pm ET gets yesterday's data, after 4pm ET includes today
- Computes technical indicators (RSI, MACD, Bollinger Bands, etc.)
- Computes cross-sectional features (market-relative percentile ranks, z-scores)

**Verification:**
```
Show me how many stocks have price data from the last week
```

### Step 2: Build Training Dataset

**Prompt:**
```
Build a dataset named "nasdaq_momentum" version "v1" for NASDAQ exchange,
limit to 100 symbols, horizons 7, 30, 90 days.
Export CSVs to datasets/nasdaq_momentum.
```

**Output:**
- Creates manifest in `ml_datasets` table
- Exports 3 CSV files:
  - `prices.csv` - Historical OHLCV data
  - `features.csv` - Technical indicators + cross-sectional features
  - `labels.csv` - Future return labels for each horizon

**Verification:**
```
Check the database - how many rows are in the nasdaq_momentum dataset?
```

### Step 3: Train Model

**Prompt:**
```
Train a quantile regression model named "momentum_v1" version "20251218"
on dataset "nasdaq_momentum" version "v1" using XGBoost.
Save artifacts to models directory.
```

**What happens:**
- Trains 9 models (3 quantiles × 3 horizons): q10, q50, q90 for h7, h30, h90
- Saves model artifacts to `models/momentum_v1_20251218_h7/`, `h30/`, `h90/`
- Registers model in `ml_models` table

**Training time:** 5-15 seconds for 100 stocks

### Step 4: Generate Predictions

**Prompt:**
```
Generate predictions for AAPL, MSFT, GOOGL, TSLA, NVDA using model
"momentum_v1" version "20251218" for today's date.
```

**Output:**
- Fetches latest features from database
- Generates q10/q50/q90 predictions for each horizon
- Stores in `predictions` table (prediction_type='quantile')

### Step 5: Query Results

**Prompt:**
```
Show me the latest predictions for AAPL from momentum_v1.
What's the median 30-day return prediction and uncertainty (IQR)?
```

**Example response:**
```
AAPL (2024-12-18):
- 7-day:  q10=-0.5%, q50=+1.2%, q90=+3.1% (IQR=3.6%)
- 30-day: q10=-1.8%, q50=+2.5%, q90=+7.2% (IQR=9.0%)
- 90-day: q10=-5.2%, q50=+4.8%, q90=+15.1% (IQR=20.3%)
```

### Step 6: Evaluate Performance

**Prompt:**
```
Evaluate momentum_v1 version 20251218 from 2024-01-01 to 2024-12-01.
How well calibrated are the quantiles?
```

**Metrics returned:**
- **q10 calibration:** Should be ~10% (actual coverage rate)
- **q50 calibration:** Should be ~50% (median accuracy)
- **q90 calibration:** Should be ~90% (upper bound coverage)
- **Pinball loss:** Lower is better
- **IQR:** Average uncertainty width

---

## Trend Classification System

**Goal:** Build a 5-class trend classifier (strong_down, weak_down, flat, weak_up, strong_up).

### Step 1: Same Data Preparation

```
Update prices and features for NASDAQ exchange, limit to 100 symbols.
```

### Step 2: Build Dataset (Same as Quantile)

```
Build a dataset named "nasdaq_trends" version "v1" for NASDAQ exchange,
limit to 100 symbols, horizons 7, 30, 90 days.
Use weak thresholds 0.02, 0.05, 0.10 and strong thresholds 0.05, 0.10, 0.20.
Export to datasets/nasdaq_trends.
```

**Note:** The weak/strong thresholds define class boundaries:
- `strong_down`: return < -strong_threshold
- `weak_down`: -strong_threshold ≤ return < -weak_threshold
- `flat`: -weak_threshold ≤ return ≤ +weak_threshold
- `weak_up`: +weak_threshold < return ≤ +strong_threshold
- `strong_up`: return > +strong_threshold

### Step 3: Train Classifier

**Prompt:**
```
Train a trend classifier named "trend_v1" version "20251218"
on dataset "nasdaq_trends" version "v1" using XGBoost.
```

**What happens:**
- Trains 3 multi-class classifiers (one per horizon)
- Each predicts 5 classes with probabilities
- Saves to `models/trend_v1_20251218_h7/`, `h30/`, `h90/`

### Step 4: Generate Trend Predictions

**Prompt:**
```
Generate trend predictions for AAPL, MSFT, GOOGL using trend_v1
version 20251218 for today.
```

**Output stored in `predictions` table (prediction_type='trend_class'):**
```
AAPL (h7):
  strong_down: 5%
  weak_down: 15%
  flat: 30%
  weak_up: 35%
  strong_up: 15%
  → Predicted class: weak_up (35% probability)
```

### Step 5: Query Trend Predictions

**Prompt:**
```
Show me all stocks predicted as "strong_up" for the 7-day horizon
with probability > 40%
```

**SQL Query (via query_database):**
```sql
SELECT
    s.symbol,
    p.horizon_days,
    p.prediction_values->>'predicted_class' as predicted_class,
    (p.prediction_values->>'strong_up')::numeric as strong_up_prob,
    p.prediction_date
FROM predictions p
JOIN stocks s ON p.data_id = s.id
JOIN ml_models m ON p.model_id = m.id
WHERE p.prediction_type = 'trend_class'
  AND m.name = 'trend_v1'
  AND p.horizon_days = 7
  AND p.prediction_values->>'predicted_class' = 'strong_up'
  AND (p.prediction_values->>'strong_up')::numeric > 0.40
ORDER BY (p.prediction_values->>'strong_up')::numeric DESC
LIMIT 20
```

---

## Combined Signal System

**Goal:** Use both quantile predictions and trend classifications for robust signals.

### Signal Logic

**High-conviction long signal:**
- Quantile: q50 > +2% AND q10 > 0% (median positive, downside protected)
- Trend: predicted_class IN ('weak_up', 'strong_up') AND probability > 40%
- Cross-sectional: percentile_rank > 0.70 (top 30% momentum)

**Prompt:**
```
Find stocks with strong positive outlook:
- 30-day median return prediction > 2%
- 30-day q10 (downside) > 0%
- Trend classification is weak_up or strong_up with > 40% probability
- Include the stock's momentum percentile rank

Show top 10 ranked by q50 prediction.
```

**SQL Query:**
```sql
WITH latest_predictions AS (
    SELECT
        s.symbol,
        (pq.prediction_values->>'q10')::numeric as q10,
        (pq.prediction_values->>'q50')::numeric as q50,
        (pq.prediction_values->>'q90')::numeric as q90,
        (pq.prediction_values->>'q90')::numeric - (pq.prediction_values->>'q10')::numeric as iqr,
        pt.prediction_values->>'predicted_class' as predicted_class,
        (pt.prediction_values->>'weak_up')::numeric + (pt.prediction_values->>'strong_up')::numeric as up_prob
    FROM predictions pq
    JOIN predictions pt ON
        pq.data_id = pt.data_id AND
        pq.horizon_days = pt.horizon_days AND
        pq.prediction_date = pt.prediction_date AND
        pt.prediction_type = 'trend_class'
    JOIN stocks s ON pq.data_id = s.id
    WHERE pq.prediction_type = 'quantile'
      AND pq.horizon_days = 30
      AND pq.prediction_date = (SELECT MAX(prediction_date) FROM predictions WHERE prediction_type = 'quantile')
)
SELECT *
FROM latest_predictions
WHERE q50 > 0.02
  AND q10 > 0
  AND predicted_class IN ('weak_up', 'strong_up')
  AND up_prob > 0.40
ORDER BY q50 DESC
LIMIT 10
```

### Portfolio Construction

**Prompt:**
```
For the top 10 stocks from the signal screening:
1. Show me their current momentum percentile rank (cross-sectional feature)
2. What's the average IQR (uncertainty)?
3. Which ones have the best risk/reward (q50 / IQR)?
```

---

## Model Performance Monitoring

**Goal:** Track model degradation and trigger retraining when needed.

### Daily Performance Check

**Prompt:**
```
Show me all model evaluation runs from the last 30 days.
Focus on calibration drift: are the q10/q50/q90 coverage rates staying close to target?
```

**Expected output:**
```
momentum_v1 (evaluated 2024-12-15):
  h7:  q10=11.2% (target 10%), q50=52.1% (target 50%), q90=88.9% (target 90%)
  h30: q10=9.8%  (target 10%), q50=49.5% (target 50%), q90=91.2% (target 90%)
  h90: q10=8.5%  (target 10%), q50=47.8% (target 50%), q90=89.1% (target 90%)

trend_v1 (evaluated 2024-12-15):
  Overall accuracy: 42% (5-class)
  Macro F1: 0.38
```

### Degradation Detection

**Prompt:**
```
Compare model performance over time - has momentum_v1's calibration gotten worse?
Show me q50_calibration for the last 5 evaluation runs.
```

**Decision rule:**
- If q50 calibration drifts > 5% from target (50%) → retrain
- If q10/q90 coverage drifts > 10% from target → retrain
- If pinball loss increases > 20% from baseline → retrain

### A/B Testing New Models

**Prompt:**
```
I just trained momentum_v2 with different features.
Compare its calibration metrics to momentum_v1 for the same evaluation period.
Which one performs better?
```

---

## Data Quality & Exploration

### Coverage Audit

**Prompt:**
```
How complete is our data coverage?
- How many stocks have price data?
- What's the date range?
- Are there any gaps in recent data?
- How many stocks were updated today vs yesterday?
```

**Follow-up queries:**
```
Show me stocks that haven't been updated in over 3 days

Which stocks have the most complete feature coverage?

Are there any stocks with unusual price patterns (e.g., > 50% single-day moves)?
```

### Feature Distribution Analysis

**Prompt:**
```
What's the distribution of RSI values across all stocks today?
Are we seeing any unusual clustering (e.g., everything oversold)?
```

**SQL:**
```sql
SELECT
    CASE
        WHEN rsi_14 < 30 THEN 'oversold'
        WHEN rsi_14 > 70 THEN 'overbought'
        ELSE 'neutral'
    END as rsi_zone,
    COUNT(*) as stock_count,
    ROUND(AVG(rsi_14), 2) as avg_rsi
FROM computed_features cf
JOIN feature_definitions fd ON cf.feature_id = fd.id
WHERE fd.name = 'rsi_14'
  AND cf.date = CURRENT_DATE
GROUP BY rsi_zone
```

### Cross-Sectional Sanity Checks

**Prompt:**
```
Check cross-sectional features for today:
- Verify percentile ranks are uniformly distributed (should be ~10% in each decile)
- Check for any stocks with extreme z-scores (> 3 std devs)
```

### Computing Cross-Sectional Rankings

**Goal:** Generate relative rankings for stocks within comparison groups.

**Step 1: Ensure fundamentals are loaded**

First, stocks need sector/industry data for sector-relative rankings:
```
Check if stocks have sector data. Show me how many stocks are missing sector information.
```

If needed:
```sql
SELECT COUNT(*) as total, COUNT(sector) as with_sector
FROM stocks WHERE status = 'Active'
```

**Step 2: Compute rankings for a feature**

**Prompt:**
```
Compute cross-sectional rankings for indicator_rsi_14.
Include both market and sector rankings.
```

**MCP Tool Used:** `cross_sectional_compute`

**What happens:**
1. Fetches latest RSI values for all stocks
2. Computes market-wide rankings (all stocks)
3. Computes sector-relative rankings (e.g., tech vs tech, finance vs finance)
4. Stores results in `cross_sectional_features` table

**Output example:**
```json
{
  "success": true,
  "feature_name": "indicator_rsi_14",
  "date": "2025-12-24",
  "stocks_count": 100,
  "total_rankings": 156,
  "groups": ["market", "sector:TECHNOLOGY", "sector:HEALTHCARE", "sector:FINANCE"]
}
```

**Step 3: Query ranking results**

**Prompt:**
```
Show me the top 5 stocks by RSI percentile within their sector.
I want to see which stocks are overbought relative to sector peers.
```

**SQL (via query_database):**
```sql
SELECT
    s.symbol,
    s.sector,
    csf.value as rsi,
    csf.rank as sector_rank,
    csf.percentile as sector_percentile
FROM cross_sectional_features csf
JOIN stocks s ON csf.data_id = s.id
WHERE csf.feature_name = 'indicator_rsi_14'
  AND csf.comparison_group LIKE 'sector:%'
  AND csf.percentile > 0.90
ORDER BY csf.percentile DESC
LIMIT 10
```

**Use case: Find relative strength leaders**

**Prompt:**
```
Find stocks that rank in the top 20% within their sector for RSI
but are not overbought in absolute terms (RSI < 70).
These are sector momentum leaders with room to run.
```

---

## Production Deployment Patterns

### Daily Automation

**Cron job (outside MCP):**
```bash
#!/bin/bash
# Daily at 5pm ET (after market close)
0 17 * * 1-5 /path/to/daily_update.sh
```

**daily_update.sh:**
```bash
#!/bin/bash
cd /path/to/gefion

# 1. Update data
.venv/bin/gefion data-update --exchange NASDAQ --limit 500 --local

# 2. Generate predictions
TODAY=$(date +%Y-%m-%d)
.venv/bin/gefion ml predict \
  --model-name momentum_v1 --model-version 20251218 \
  --prediction-date $TODAY --exchange NASDAQ --limit 500

.venv/bin/gefion ml predict-classifier \
  --model-name trend_v1 --model-version 20251218 \
  --prediction-date $TODAY --exchange NASDAQ --limit 500

# 3. Check for degradation (weekly on Fridays)
if [ $(date +%u) -eq 5 ]; then
  END_DATE=$(date +%Y-%m-%d)
  START_DATE=$(date -d '30 days ago' +%Y-%m-%d)

  .venv/bin/gefion ml eval \
    --model-name momentum_v1 --model-version 20251218 \
    --start-date $START_DATE --end-date $END_DATE
fi
```

### Interactive Morning Briefing (via MCP)

**Prompt:**
```
Good morning! Give me today's trading briefing:

1. How many stocks were updated overnight?
2. Show me the top 10 stocks by combined signal strength (quantile + trend)
3. Any unusual market conditions (extreme RSI, volatility spikes)?
4. Model performance - any degradation detected?
```

### Real-time Signal Alerts

**Prompt:**
```
Monitor for new strong_up signals throughout the day.
When a stock gets classified as strong_up with > 50% probability,
show me its full profile: predictions, features, recent price action.
```

---

## Best Practices

### Data Management

1. **Update frequency:** Daily after market close (5-6pm ET)
2. **Feature computation:** Use `--local` for speed (no API rate limits)
3. **Time-awareness:** System automatically prevents partial intraday data

### Model Training

1. **Dataset versioning:** Use semantic versions (v1, v2, v3) for datasets
2. **Model versioning:** Use YYYYMMDD format for easy sorting
3. **Retraining cadence:** Weekly for trend models, monthly for quantile models
4. **A/B testing:** Always compare new model to baseline before deployment

### Prediction Generation

1. **Generate fresh predictions daily** after data update
2. **Store all predictions** for backtesting and analysis
3. **Set prediction_date explicitly** for reproducibility

### Monitoring

1. **Weekly evaluation runs** to catch degradation early
2. **Track calibration drift** as primary metric
3. **Compare against baseline** for relative performance
4. **Alert on >5% calibration drift** or >20% loss increase

### Signal Generation

1. **Combine multiple signals** (quantile + trend + cross-sectional)
2. **Use uncertainty (IQR)** for position sizing
3. **Apply cross-sectional filters** to find relative strength
4. **Backtest signal combinations** before live deployment

---

## Troubleshooting Workflows

### "No predictions found"

**Check:**
1. Did prediction generation complete? Check logs
2. Is the date correct? Use ISO format YYYY-MM-DD
3. Are symbols in the database? Query stocks table
4. Check model_id matches in predictions table

### "Model performance degraded"

**Actions:**
1. Evaluate on recent data to confirm
2. Check for data quality issues (missing features, stale prices)
3. Compare feature distributions (before vs now)
4. Retrain with updated data
5. A/B test new model vs current

### "Features missing for some stocks"

**Diagnosis:**
```
Which stocks are missing RSI?
SELECT s.symbol
FROM stocks s
LEFT JOIN computed_features cf ON s.id = cf.data_id
LEFT JOIN feature_definitions fd ON cf.feature_id = fd.id AND fd.name = 'rsi_14'
WHERE cf.id IS NULL
  AND s.status = 'Active'
```

**Fix:**
```
Run feature computation for missing stocks:
gefion feat-compute --symbols <comma-separated> --local
```

---

## Advanced Workflows

### Multi-Model Ensemble

Train 3 models with different algorithms, average their predictions:

```
1. Train momentum_xgb (XGBoost)
2. Train momentum_lgb (LightGBM)
3. Train momentum_sklearn (quantile regression)

Query all 3, compute ensemble average, use for final signal
```

### Rolling Window Backtesting

Evaluate model on expanding window to detect regime changes:

```
Evaluate model on:
- 2024-01-01 to 2024-03-31 (Q1)
- 2024-01-01 to 2024-06-30 (H1)
- 2024-01-01 to 2024-09-30 (Q1-Q3)
- 2024-01-01 to 2024-12-31 (Full year)

Plot calibration drift over time
```

### Feature Importance Analysis

Ask Claude to:
1. Query prediction confidence by feature availability
2. Find which features correlate most with accurate predictions
3. Identify redundant features for pruning

---

## Performance Monitoring & Observability

**Goal:** Use Grafana Tempo traces to monitor system performance, debug slow operations, and optimize the ML pipeline.

### Prerequisites

Start Tempo + Grafana:
```bash
docker compose -f docker/tempo/docker-compose.tempo.yml up -d
```

Enable tracing:
```bash
export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_OTLP_ENDPOINT=http://localhost:4317
export OTEL_SERVICE_NAME=gefion
```

### Workflow 1: Health Check After Operations

**Use case:** After running a data update or feature computation, verify the operation completed successfully and check for performance issues.

**Prompt:**
```
I just ran a data update for NASDAQ. Check the recent traces to see:
1. Did it complete without errors?
2. How long did it take?
3. Are there any performance bottlenecks?
```

**MCP Tools Used:**
- `span_check` - Get overview of recent traces with error detection

**What Claude does:**
1. Calls `span_check` with default parameters
2. Analyzes trace statistics:
   - Total spans vs application spans vs database spans
   - Error span count (should be 0)
   - Duration of the operation
3. Reports any issues or confirms healthy execution

**Example output:**
```
Trace Check Results:
✓ Found 15 recent traces for service "gefion"
✓ Most recent: cli.data-update (4523ms)
✓ Total spans: 124
  - Application spans: 42
  - Database spans: 82
  - Error spans: 0

Performance: Normal (4.5 seconds for data update)
Status: Healthy - no errors detected
```

### Workflow 2: Investigate Slow Operations

**Use case:** Feature computation is taking longer than expected. Find the bottleneck.

**Prompt:**
```
My feature computation for 100 symbols took 5 minutes. That seems slow.
Can you find the bottleneck?
```

**MCP Tools Used:**
1. `tempo_search` - Find recent feature computation traces
2. `tempo_trace_detail` - Get detailed span information for the slowest trace

**What Claude does:**
1. Searches for traces with `tags=function_name=indicator`
2. Identifies the slowest trace
3. Fetches detailed span data
4. Analyzes span hierarchy to find bottlenecks:
   - Database write time
   - Computation time
   - Connection pool contention
5. Provides optimization recommendations

**Example analysis:**
```
Bottleneck Analysis:

Trace: cli.feat-compute (5m 23s)
├─ compute_features (5m 18s)
│  └─ process_function_group (4m 45s)
│     └─ insert_computed_features (4m 12s) ⚠️ BOTTLENECK

Root cause: Database writes taking 78% of total time

Recommendations:
1. Increase --batch-size from 2000 to 10000
2. Consider using --parallel flag for multi-symbol computation
3. Check database connection pool size (currently seeing pool_available=0)

Expected improvement: 60-70% faster with batch size increase
```

### Workflow 3: Compare Before/After Optimization

**Use case:** You've made an optimization change and want to verify it improved performance.

**Prompt:**
```
I increased the batch size to 10000. Can you compare the trace before and after
to see if it's faster?
```

**MCP Tools Used:**
1. `tempo_search` - Find traces before and after the change
2. `span_check` - Get detailed span breakdown for comparison

**What Claude does:**
1. Searches for recent traces, sorted by time
2. Identifies "before" and "after" traces
3. Compares key metrics:
   - Total duration
   - Database span count
   - Insert operation time
4. Calculates improvement percentage

**Example output:**
```
Performance Comparison:

Before (batch_size=2000):
- Total time: 5m 23s (323s)
- insert_computed_features: 4m 12s (252s)
- Database spans: 156 operations

After (batch_size=10000):
- Total time: 2m 8s (128s)
- insert_computed_features: 1m 24s (84s)
- Database spans: 32 operations

Improvement:
✓ 60% faster overall (323s → 128s)
✓ 67% faster database writes (252s → 84s)
✓ 79% fewer database operations (156 → 32)

Result: Optimization successful!
```

### Workflow 4: Find Errors and Debug Issues

**Use case:** Something went wrong during ML training. Find what failed.

**Prompt:**
```
My model training failed. Can you check the traces to see what went wrong?
```

**MCP Tools Used:**
1. `span_check` - Check for error spans
2. `tempo_trace_detail` - Get detailed error information

**What Claude does:**
1. Calls `span_check` to find traces with errors
2. Identifies which span failed
3. Fetches detailed trace to see error attributes and events
4. Extracts error message and stack trace
5. Suggests fix based on error type

**Example debugging:**
```
Error Found:

Trace: cli.ml-train (12.3s)
├─ ml_train_quantile (12.1s)
│  └─ load_training_data (0.2s) ❌ ERROR

Error details:
- Status: STATUS_CODE_ERROR
- Message: "Dataset 'nasdaq_momentum' version 'v2' not found"
- Location: src/gefion/ml/train.py:145

Root cause: Dataset version mismatch

Fix: Either:
1. Build dataset version 'v2': ml dataset-build --name nasdaq_momentum --version v2
2. Use existing version 'v1': ml train --dataset-version v1
```

### Workflow 5: Search for Specific Operations

**Use case:** Find all traces where AAPL feature computation took longer than 1 second.

**Prompt:**
```
Show me all AAPL feature computations that took longer than 1 second in the last hour.
I want to understand why AAPL is slower than other symbols.
```

**MCP Tools Used:**
- `tempo_search` with tag and duration filters

**What Claude does:**
1. Searches with `tags=symbol=AAPL`, `min_duration=1s`
2. Lists matching traces
3. Analyzes patterns across multiple traces
4. Compares AAPL traces to other symbols

**Example analysis:**
```
Found 8 traces for AAPL > 1s:

Trace Pattern Analysis:
- Average duration: 1.8s
- Compare to MSFT average: 0.4s
- AAPL is 4.5x slower

Common pattern in slow AAPL traces:
- indicator_macd span: 1.2s (vs 0.2s for MSFT)
- Likely cause: AAPL has 10 years of historical data vs 2 years for MSFT

Recommendation:
- Consider limiting historical data window for computation
- Or implement incremental updates for long-history symbols
```

### Workflow 6: Production Monitoring

**Use case:** Set up continuous monitoring to catch performance regressions.

**Prompt:**
```
I want to monitor my production data pipeline. Set up observability with 1% sampling
so I can catch performance issues without overhead.
```

**Configuration Claude suggests:**
```bash
# .env.production
OTEL_ENABLED=true
OTEL_EXPORTER=otlp
OTEL_OTLP_ENDPOINT=http://tempo.production:4317
OTEL_SERVICE_NAME=gefion-production
OTEL_SAMPLING_RATE=0.01  # 1% sampling = low overhead
```

**Monitoring workflow:**
```
Every day at 6pm:
1. Check span_check for any error spans
2. Compare p95 duration vs baseline
3. Alert if >20% slower than baseline or any errors
```

### Integration with ML Workflows

**Combined workflow:** After training a model, automatically check performance.

**Prompt:**
```
Train model momentum_v2 on nasdaq_momentum dataset, then check the trace
to see how long training took and if there were any issues.
```

**What happens:**
1. Claude calls `ml_train` tool
2. Training completes
3. Claude automatically calls `span_check`
4. Reports training time and any performance issues
5. Stores baseline for future comparison

---

## Advanced Workflows

### Multi-Model Ensemble

Train 3 models with different algorithms, average their predictions:

```
1. Train momentum_xgb (XGBoost)
2. Train momentum_lgb (LightGBM)
3. Train momentum_sklearn (quantile regression)

Query all 3, compute ensemble average, use for final signal
```

### Rolling Window Backtesting

Evaluate model on expanding window to detect regime changes:

```
Evaluate model on:
- 2024-01-01 to 2024-03-31 (Q1)
- 2024-01-01 to 2024-06-30 (H1)
- 2024-01-01 to 2024-09-30 (Q1-Q3)
- 2024-01-01 to 2024-12-31 (Full year)

Plot calibration drift over time
```

### Feature Importance Analysis

Ask Claude to:
1. Query prediction confidence by feature availability
2. Find which features correlate most with accurate predictions
3. Identify redundant features for pruning

---

## Autonomous Experiment Workflows

The experiment framework is fully reachable via MCP — an AI agent can run
the entire research loop conversationally.

### Run a cycle and read the verdict

```
1. experiment_cycle_start   → creates the cycle (holdout window + FDR rate)
2. experiment_cycle_run     → discover → propose → run → holdout-evaluate → FDR → promote
3. experiment_cycle_status  → survivors, p-values, promotion state
4. chart_experiment_fdr     → cycle summary chart (p-values vs FDR threshold)
5. chart_experiment_trials  → per-experiment trial scatter
```

Every cycle experiment earns a **one-sided holdout p-value** (trials train on
pre-holdout rows only; the holdout window is scored exactly once). FDR is
fail-closed: an experiment with no p-value cannot survive.

### Take a winner to production

```
1. experiment_apply           → dataset rebuild → retrain → predict → ml_signal backtest
                                 (opens a 7-day probation window)
2. experiment_probation_check → re-measures realized performance; auto-demotes degradation
                                 (also runs automatically at the end of data_update)
3. experiment_demote          → manual demotion with a recorded reason
```

### Manual experiment flow

```
experiment_discover → experiment_propose → experiment_approve → experiment_run
experiment_status   → live progress of a running experiment
experiment_chain / experiment_children → chained experiments using parent outputs
```

### Inspect the estate

```
experiment_list        → all experiments (filter by status/type)
experiment_cycle_list  → all cycles
experiment_results     → one experiment's trials, best params, holdout summary
```

## Documentation Tools

Remote MCP clients can't read the repo, so the documentation itself is
reachable as tools:

```
docs_list    → documentation files with one-line summaries
docs_read    → one doc by name (e.g. USER_GUIDE.md)
docs_search  → case-insensitive search with line context
```

## Next Steps

After mastering these workflows:

1. **Add backtesting tools** to MCP server (coming in Phase 2)
2. **Implement portfolio optimization** using predictions
3. **Build screening dashboards** with real-time signal updates
4. **Create automated trading signals** with risk management
5. **Deploy production monitoring** with alerting

See [.specify/memory/backlog.md](../.specify/memory/backlog.md) for roadmap.

## Regime slicing tools (spec 005)

The `regime_*` MCP tools mirror the `gefion regime` CLI (see [REGIMES.md](REGIMES.md)):

- `regime_define` — define and store a regime (expression AST + bucketing).
- `regime_list` — list regime definitions.
- `regime_show` — show a regime definition.
- `regime_compute` — compute causal labels for a regime.
- `regime_labels` — summarize computed labels (bucket coverage).
- `regime_archive` — archive a regime definition (recommended lifecycle exit).
- `regime_delete` — delete a definition + labels. Dry-run by default (`confirm=false`)
  reporting the full blast radius; **mutating and destructive** with `confirm=true` —
  always show the user the dry-run and get explicit approval first. Machine-origin
  regimes need `force=true`; the candidate ledger is never touched either way.
- `regime_definitions_export` / `regime_definitions_import` — JSON backup/restore.
- `regime_interaction` — continuous-interaction test (does a signal's edge scale with a conditioning variable?).
- `chart_regime` — chart a symbol's price with regime-episode bands overlaid.

## Agentic regime discovery tools (spec 006)

The `regime_discover_*` tools mirror `gefion regime discover` (see
[REGIMES.md](REGIMES.md) § Agentic discovery for the threat model and guardrails):

- `regime_discover_start` — pre-register and run a bounded discovery run. **Mutating and
  potentially long**: excluded from read-only allowlists; confirm with the user before
  invoking (same class as experiment runs). Expect mostly/entirely rejections — that is
  the loop working, not failing.
- `regime_discover_list` — list discovery runs (status, FDR family size, dataset).
- `regime_discover_show` — inspect a run: pre-registration (search space + declared
  seams), segregation boundaries, family size, status.
- `regime_discover_ledger` — the candidate ledger: every candidate evaluated, losers
  included (they are the FDR family's denominator). Filterable by verdict.
- `regime_discover_verdicts` — FDR survivors (most runs: none), always with the family
  size beside them. Never present an unadmitted candidate as a finding.
- `regime_discover_spa` — selection-aware Superior Predictive Ability (SPA) re-verdict
  over a completed run's counted family: reconstructs each unit from the ledger +
  pre-registration, verifies the recomputed p-values reproduce the stored ones
  (refuses honestly on drift), runs Hansen's SPA with a joint stationary bootstrap,
  and records the result append-only beside the run. Never rewrites BH verdicts or
  the ledger. A SMALL consistent p SUPPORTS the family (R9); UNSUPPORTED is only
  alarming beside admissions — report it as caution, never as a demotion.
  **Mutating** (appends one durable row) and compute-heavy
  (reconstruction + B bootstrap iterations): confirm with the user before invoking.
- `regime_discover_delete` — delete an invalid/test discovery run and its ledger rows
  (run cascade). Dry-run by default (`confirm=false`); **mutating and destructive**
  with `confirm=true` — show the user the dry-run and get explicit approval first.
  A run with admitted candidates refuses always (no force door).
- `regime_discover_diagnostics` — the diagnostics ledger: limits hit with quantitative
  reasons, tagged sample-dependent (re-test on new data) vs structural (accumulate).
- `regime_discover_grades` — forward-accruing trust grades (fold 1 = probation);
  descriptive backward era-slices are flagged and never counted toward the grade.
- `regime_discover_register` — re-declare an admitted edge's grading grid (fold
  width). Allowed only until real evidence exists; after the first confirmed/failed
  fold the grid is locked. **Mutating**: confirm with the user before invoking.
- `regime_discover_grade_fold` — re-test an admitted edge on a forward fold window
  and record the outcome — confirmed, failed, or *no evidence* (a power-refused
  fold is recorded but never counted). **Mutating** (appends a trust-grade row):
  confirm with the user before invoking.

## Entity lifecycle

- `entity_delete` — delete an entity (stock, macro series) and its feature-store
  values, registry-driven and uniform across entity kinds. Dry-run by default
  (`confirm=false`): reports the full blast radius — feature-value counts,
  hard-FK dependents with ON DELETE rules, blockers — and changes nothing.
  With `confirm=true` it is **mutating and destructive**: always show the user
  the dry-run plan and get explicit confirmation first. Refuses when a
  RESTRICT/NO-ACTION dependent still has rows. Audit ledgers are never in scope.

## Macro series

- `macro_ingest` — ingest a macro series (VIX, CPI, rates …) into the macro home
  and materialize its `macro_<name>` feature, making it available to discovery
  atoms, regime expressions, and interaction tests with zero equity-pipeline
  changes. Default provider `fred:<SERIES>` is keyless (`fred:VIXCLS` for VIX;
  AlphaVantage INDEX_DATA is premium and not entitled on the current key).
  **Mutating**, and `full=true` backfills decades — confirm with the user first.
- `macro_list` — the macro-series catalog with per-series value coverage
  (first/last date, row count) and materialization status (read-only).

## Data quality (spec 008)

Provider-garbage detection: definitionally impossible or self-contradictory
values are convicted as trash and kept out of research by default; degenerate-
but-real extremes stay usable. Always show the verdict tier — a suspect is not
a conviction.

- `quality_findings` — list detections (rule, observed vs expected, verdict);
  default unresolved, newest first. Read-only.
- `quality_catalog` — the validation catalog: covered metrics and the uncovered
  (unvalidated) columns. Read-only.
- `quality_backfill` — **mutating (ledger only)**: validate stored history and
  record findings for pre-existing garbage; changes no stored value. Also
  reconciles: unresolved findings in the run's scope that no longer reproduce
  under the current catalog are auto-resolved (superseded, never deleted) — a
  catalog retune is self-cleaning. Confirm first (may take minutes on full
  history).
- `quality_resolve` — **mutating**: supersede a finding (reason required; never
  deletes). Confirm first.
- db-health / `health_check` gain a `data_quality` section automatically
  (per-metric unresolved counts by verdict).

## Tool reference (remaining tools)

One-liners for every tool not covered by a workflow section above. The
docs-drift test (`tests/test_docs_drift.py`) enforces that every MCP tool is
documented here or in a section above.

### System & health

- `system_status` — comprehensive status with intelligent suggestions: infrastructure health, data freshness, missing features.
- `health_check` — quick infrastructure health check (PostgreSQL, Tempo, Docker); use `system_status` for the full picture.
- `docker_status` — docker compose services status (quick docker-specific check).
- `get_role_info` — current MCP server role (developer/operator) and behavioral guidelines.
- `dev_status` — parse DEVELOPMENT/NEXT_STEPS/PROGRESS docs for roadmap position and next steps.

### Data & features

- `feature_compute` — compute features for symbols via the generic dispatcher.
- `features_list` — list feature definitions with metadata.
- `feature_show` — one feature definition: function, params, source/store tables, entity table, active.
- `feature_functions_list` — list registered feature functions (the computation bodies).
- `feature_definitions_export` / `feature_definitions_import` — sync feature definitions with `feature-definitions/*.json` (idempotent; exports carry `entity_table`).
- `feature_functions_export` / `feature_functions_import` — sync feature functions with `feature-functions/*.json` (idempotent).
- `volatility_compute` — per-stock adaptive volatility thresholds (trend-classifier labels).

### ML pipeline

- `ml_dataset_build` — build a training dataset (manifest + price/feature/label files).
- `ml_dataset_inspect` — dataset configuration and dependent models.
- `ml_train_classifier` — train the 5-class trend classifier.
- `ml_train_ensemble` — train a multi-algorithm ensemble.
- `ml_predict` — quantile predictions (q10/q50/q90) for symbols on a date.
- `ml_predict_classifier` — trend-class predictions with probabilities.
- `ml_predict_ensemble` — weighted-average ensemble predictions.
- `ml_eval` — calibration metrics (coverage, pinball loss, IQR) over stored predictions.
- `ml_calibrate` — conformal-prediction shift corrections from a holdout period.
- `ml_tune` — Optuna hyperparameter tuning with time-series cross-validation.
- `ml_feature_importance` — SHAP-based feature importance for a trained model.
- `query_predictions` — stored predictions by symbol/date/horizon/type.
- `query_model_performance` — evaluation metrics from past runs.

### Backtesting & strategies

- `backtest_run` — backtest a strategy with optional realistic execution (costs, slippage). `mode=long_short` (spec 009) enables short-side execution so strategies act on bearish signals; the result carries `short_costs`, `margin_events`, and an `exposure` series — surface those, never a short's return without its borrow/dividend/margin costs. Default `long_only` is byte-identical to before.
- `backtest_compare` — side-by-side strategy comparison (return, Sharpe, drawdown).
- `strategy_list` — registered strategies with defaults.
- `strategy_configs` — saved strategy configurations.
- `strategy_create_config` — create a named strategy configuration.

### Charts

- `chart_price` — candlestick chart with volume and technical context.
- `chart_predictions` — price with quantile prediction bands.
- `chart_pred_vs_actual` — predicted vs realized returns scatter.
- `chart_calibration` — model calibration curve.
- `chart_confusion_matrix` — trend-classifier confusion matrix.
- `chart_features` — price with technical-indicator subplots.
- `chart_pipeline_health` — data freshness / feature coverage / prediction distribution dashboard.

### Principles & observability

- `principles_list` — the quantitative-finance principles catalog.
- `principles_suggest` — principle-seeded experiment suggestions.
- `trace_search` — find traces by tags/duration/service.
- `trace_compare` — quantify performance changes between two traces.
