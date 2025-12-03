# SQL Scripts

This directory contains SQL scripts for schema setup and feature definitions.

## Files

### schema.sql

Complete database schema for g2 application. Creates all tables, hypertables, and indexes.

**Usage:**
```bash
# Initialize or reset database schema
psql -d g2 -f sql/schema.sql
```

**Tables created:**
- `stocks` - Stock symbols dimension table
- `stock_prices` - OHLCV price data (hypertable)
- `feature_definitions` - Feature metadata (calc_store pattern)
- `computed_features` - Computed features (hypertable)
- `company_fundamentals_history` - Fundamental data (hypertable)

**Note:** Safe to run multiple times (idempotent). All tables use `IF NOT EXISTS`.

### derivative_features.sql

Defines 15 recommended derivative features organized by category:

- **RSI**: 3 derivatives (slope_5, slope_10, concavity_5)
- **MACD**: 3 derivatives (slope_5, concavity_5, signal_slope_5)
- **Price**: 3 derivatives (close_slope_5, close_slope_10, close_concavity_5)
- **ADX**: 2 derivatives (slope_5, concavity_5)
- **Stochastic**: 2 derivatives (stoch_k_slope_5, stoch_k_concavity_5)
- **Bollinger**: 2 derivatives (bb_middle_slope_5, bb_middle_concavity_5)

**Usage:**
```bash
# Define all 15 derivative features
psql -d g2 -f sql/derivative_features.sql

# Then compute them
g2 features-compute --function-names derivative
```

**Note:** These definitions use `ON CONFLICT (name) DO NOTHING`, so running this script multiple times is safe.

## Alternative: Using CLI

You can also define features one at a time using the CLI:

```bash
g2 features-register --definition '{
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

## See Also

- [DERIVATIVE_FEATURES_QUICK_START.md](../docs/DERIVATIVE_FEATURES_QUICK_START.md) - Quick start guide
- [FEATURE_DISPATCHER.md](../docs/FEATURE_DISPATCHER.md) - Generic dispatcher documentation
