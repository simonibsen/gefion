# Derivative Features - Quick Start

## What Are Derivative Features?

Derivatives capture **rate of change** (slope) and **acceleration** (concavity) of indicators:

- **Slope**: Is RSI trending up or down? How fast?
- **Concavity**: Is the trend accelerating or decelerating?

## Quick Start

### 1. Define Derivative Features (Like Indicators)

Just like indicators, you define which derivatives you want. Features are DATA in the database, not code.

**Option A: Using SQL directly**
```bash
psql -d gefion -f sql/derivative_features.sql
```

**Option B: Using the CLI (one feature at a time)**
```bash
gefion features-register --definition '{
  "name": "derivative_rsi_14_slope_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_rsi_14", "type": "slope", "window": 5, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'
```

See [sql/derivative_features.sql](../sql/derivative_features.sql) for all 15 recommended derivatives.

### 2. Compute Derivatives (Using Dispatcher)

```bash
# Compute all derivatives for all stocks
gefion features-compute --function-names derivative

# Compute for specific stocks
gefion features-compute --symbols AAPL,MSFT --function-names derivative

# Compute specific features
gefion features-compute --features derivative_rsi_14_slope_5,derivative_macd_slope_5
```

### 3. Query Results

```sql
-- Get RSI and its derivatives for AAPL
SELECT
    cf.date,
    MAX(CASE WHEN fd.name = 'indicator_rsi_14' THEN cf.value END) as rsi,
    MAX(CASE WHEN fd.name = 'derivative_rsi_14_slope_5' THEN cf.value END) as rsi_slope,
    MAX(CASE WHEN fd.name = 'derivative_rsi_14_concavity_5' THEN cf.value END) as rsi_concavity
FROM computed_features cf
JOIN feature_definitions fd ON fd.id = cf.feature_id
JOIN stocks s ON s.id = cf.data_id
WHERE s.symbol = 'AAPL'
  AND cf.date >= '2024-11-01'
GROUP BY cf.date
ORDER BY cf.date DESC
LIMIT 10;
```

## Defining Custom Derivatives

Just like indicators, you can define any derivatives you want. Features are defined as data, not code.

**Using CLI:**
```bash
gefion features-register --definition '{
  "name": "derivative_ema12_slope_3",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_ema12", "type": "slope", "window": 3, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'
```

**Using SQL:**
```sql
INSERT INTO feature_definitions (
    name, function_name, params,
    source_table, source_column,
    store_table, store_column, store_type,
    active
) VALUES (
    'derivative_ema12_slope_3',
    'derivative',
    '{"source_feature": "indicator_ema12", "type": "slope", "window": 3, "method": "linreg"}'::jsonb,
    'computed_features',
    'value',
    'computed_features',
    'value',
    'double precision',
    true
);
```

Then compute:
```bash
gefion features-compute --features derivative_ema12_slope_3
```

## Recommended 15 Features

The recommended derivative features are:

| Category | Count | Features |
|----------|-------|----------|
| **RSI** | 3 | slope_5, slope_10, concavity_5 |
| **MACD** | 3 | slope_5, concavity_5, signal_slope_5 |
| **Price** | 3 | close_slope_5, close_slope_10, close_concavity_5 |
| **ADX** | 2 | slope_5, concavity_5 |
| **Stochastic** | 2 | stoch_k_slope_5, stoch_k_concavity_5 |
| **Bollinger** | 2 | bb_middle_slope_5, bb_middle_concavity_5 |

## ML Use Cases

### 1. Bearish Divergence Detection
```python
# Price making higher highs, but RSI trending down
divergence = (close_slope_5 > 0) & (rsi_14_slope_5 < 0)
```

### 2. Momentum Exhaustion
```python
# Price still rising, but decelerating (losing steam)
exhaustion = (close_slope_5 > 0) & (close_concavity_5 < 0)
```

### 3. Trend Acceleration
```python
# Strong trend that's accelerating
strong_trend = (close_slope_5 > 1.0) & (close_concavity_5 > 0)
```

### 4. MACD Crossover Prediction
```python
# MACD trending up faster than signal line
bullish_cross_coming = (macd_slope_5 > macd_signal_slope_5)
```

## Integration with Existing Workflow

Derivatives work just like indicators - they're computed features:

```bash
# 1. Ingest prices and compute indicators (existing workflow)
gefion data-update --exchange NASDAQ --limit 10

# 2. Define derivative features (once) - use SQL or features-register CLI
psql -d gefion -f sql/derivative_features.sql

# 3. Compute derivatives (uses dispatcher)
gefion features-compute --function-names derivative

# 4. Query all features together
```

All features (indicators and derivatives) live in the same `computed_features` table with the same structure.

## Pattern: Derivatives Are Just Features

Features are defined as data in `feature_definitions`:

```sql
-- Indicators (defined in feature_definitions)
-- Derivatives (also defined in feature_definitions)

-- Compute both using same dispatcher
SELECT compute_features(data_id, ARRAY['indicator', 'derivative']);
```

Or via CLI:
```bash
# Compute both feature types
gefion features-compute --function-names indicator,derivative --symbols AAPL
```

## Troubleshooting

### "Source feature not found"
Derivatives depend on indicators. Ensure indicators exist first:
```bash
# Check indicators
psql -d gefion -c "SELECT COUNT(*) FROM computed_features cf
               JOIN feature_definitions fd ON fd.id = cf.feature_id
               WHERE fd.function_name = 'indicator'"

# Compute missing indicators
gefion features-run --all-features --local
```

### "No data inserted"
Check source feature has data:
```sql
SELECT s.symbol, COUNT(*) as rows
FROM computed_features cf
JOIN feature_definitions fd ON fd.id = cf.feature_id
JOIN stocks s ON s.id = cf.data_id
WHERE fd.name = 'indicator_rsi_14'
GROUP BY s.symbol;
```

## See Also

- [FEATURE_DISPATCHER.md](FEATURE_DISPATCHER.md) - Generic dispatcher architecture
- [DERIVATIVE_FEATURES.md](DERIVATIVE_FEATURES.md) - Detailed derivative documentation
- [sql/derivative_features.sql](../sql/derivative_features.sql) - 15 recommended derivative definitions
