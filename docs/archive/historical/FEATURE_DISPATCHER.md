# Generic Feature Dispatcher

## Overview

The generic feature dispatcher provides a unified, metadata-driven approach to computing all types of features (indicators, derivatives, fundamentals, etc.) from a central registry.

## Architecture

### Core Components

```
┌─────────────────────────────────────────────┐
│ feature_definitions (metadata)              │
├─────────────────────────────────────────────┤
│ id │ name            │ function_name │ ...  │
├────┼─────────────────┼───────────────┼──────┤
│ 1  │ indicator_rsi   │ indicator     │ ...  │
│ 2  │ derivative_rsi..│ derivative    │ ...  │
└─────────────────────────────────────────────┘
              ↓
    [Generic Dispatcher]
      - Reads definitions
      - Fetches source data (source_table/source_column)
      - Routes to compute functions
      - Stores results
              ↓
┌─────────────────────────────────────────────┐
│ COMPUTE_FUNCTIONS Registry                  │
├─────────────────────────────────────────────┤
│ 'indicator' → compute_indicators()          │
│ 'derivative' → compute_derivatives()        │
│ 'fundamental' → compute_fundamentals()      │
└─────────────────────────────────────────────┘
              ↓
    [Pure Compute Functions]
      - Take source data + specs
      - Return computed values
      - No side effects
              ↓
┌─────────────────────────────────────────────┐
│ computed_features (materialized)            │
├─────────────────────────────────────────────┤
│ feature_id │ data_id │ date │ value        │
└─────────────────────────────────────────────┘
```

### Design Principles

1. **Metadata-Driven**: Feature definitions drive all computation
2. **Generic**: Works for any feature type via function registry
3. **Declarative**: What to compute, not how
4. **Extensible**: Add new feature types by registering compute functions
5. **Composable**: Features can depend on other features

## Usage

### Computing Features

```python
from gefion.features.dispatcher import compute_features

with psycopg.connect(db_url) as conn:
    # Compute all indicators for a stock
    result = compute_features(
        conn,
        data_id=123,
        function_names=['indicator']
    )

    # Compute specific features
    result = compute_features(
        conn,
        data_id=123,
        feature_names=['indicator_rsi_14', 'derivative_rsi_14_slope_5']
    )

    # Full refresh (recompute all dates)
    result = compute_features(
        conn,
        data_id=123,
        function_names=['indicator', 'derivative'],
        full_refresh=True
    )
```

### Registering Compute Functions

```python
from gefion.features.dispatcher import register_compute_function

def compute_my_features(source_rows, feature_specs):
    """
    Pure function: takes source data + specs, returns computed values.

    Args:
        source_rows: List[Dict] with source data
        feature_specs: List[Dict] with feature specifications

    Returns:
        List[Dict] with computed features
    """
    results = []
    # ... computation logic ...
    return results

# Register your compute function
register_compute_function('my_feature_type', compute_my_features)
```

### Feature Definitions

Feature definitions specify:
- `name`: Unique feature name
- `function_name`: Type of computation ('indicator', 'derivative', etc.)
- `params`: JSON with computation parameters
- `source_table`: Where to fetch source data
- `source_column`: Which column to read
- `store_table`: Where to store results
- `store_column`: Which column to write

**Example: Indicator**
```sql
INSERT INTO feature_definitions (name, function_name, params, source_table, source_column, store_table, store_column)
VALUES (
    'indicator_rsi_14',
    'indicator',
    '{"indicator": "rsi", "period": 14}'::jsonb,
    'stock_ohlcv',
    'close',
    'computed_features',
    'value'
);
```

**Example: Derivative**
```sql
INSERT INTO feature_definitions (name, function_name, params, source_table, source_column, store_table, store_column)
VALUES (
    'derivative_rsi_14_slope_5',
    'derivative',
    '{"source_feature": "indicator_rsi_14", "type": "slope", "window": 5}'::jsonb,
    'computed_features',
    'value',
    'computed_features',
    'value'
);
```

## CLI Usage

The new `features-compute` command uses the dispatcher:

```bash
# Compute all indicators for AAPL
gefion features-compute --symbols AAPL --function-names indicator

# Compute specific features for multiple stocks
gefion features-compute --symbols AAPL,MSFT --features indicator_rsi_14,derivative_rsi_14_slope_5

# Full refresh of all features for all stocks
gefion features-compute --all-features --full

# Incremental computation (only new dates)
gefion features-compute --symbols AAPL --function-names indicator,derivative --incremental
```

## Incremental vs Full Refresh

### Incremental (Default)
- Only computes dates newer than existing data
- Fast for regular updates
- Queries for latest date per function_name

### Full Refresh
- Recomputes all dates
- Use when:
  - Changing computation logic
  - Fixing historical data
  - Migrating feature definitions

## Data Source Handling

The dispatcher automatically handles different source types:

### stock_ohlcv
Fetches OHLC data for indicators:
```sql
SELECT date, open, high, low, close, adjusted_close, volume
FROM stock_ohlcv
WHERE data_id = %s
ORDER BY date
```

### computed_features
Fetches computed feature values for derivatives:
```sql
-- Looks up source_feature from params
-- Gets feature_id for source feature
-- Fetches that feature's values
SELECT date, value
FROM computed_features
WHERE data_id = %s AND feature_id = %s
ORDER BY date
```

### Generic tables
Supports any table with data_id, date, and value columns.

## Error Handling

The dispatcher aggregates errors per function_name:

```python
result = compute_features(conn, data_id=123)

# Result structure:
{
    'indicator': {
        'inserted': 100,
        'errors': []
    },
    'derivative': {
        'inserted': 50,
        'errors': [
            {'error': 'Source feature not found', 'features': ['derivative_x']}
        ]
    },
    'summary': {
        'total_inserted': 150,
        'total_errors': 1
    }
}
```

## Performance

### Optimizations

1. **Grouping**: Features with same source_table are batched
2. **Incremental**: Only new dates computed by default
3. **Prepared statements**: When enabled in connection pool
4. **Batch inserts**: 200 rows per batch

### Benchmarks

- **Indicators** (500 stocks, 3 indicators): ~30-45 seconds
- **Derivatives** (500 stocks, 5 derivatives): ~10-15 seconds
- **Incremental update** (500 stocks, 1 new day): ~5-10 seconds

## Extending the Dispatcher

### Adding a New Feature Type

1. **Create compute function**:
```python
def compute_fundamentals(source_rows, feature_specs):
    """Compute fundamental analysis features."""
    results = []
    for spec in feature_specs:
        # ... computation logic ...
    return results
```

2. **Register it**:
```python
from gefion.features.dispatcher import register_compute_function
register_compute_function('fundamental', compute_fundamentals)
```

3. **Define features**:
```python
from gefion.db.ingest import ensure_feature_definitions

defs = [{
    'name': 'fundamental_pe_ratio',
    'function_name': 'fundamental',
    'params': {'metric': 'pe_ratio'},
    'source_table': 'fundamentals',
    'source_column': 'earnings_per_share',
    'store_table': 'computed_features',
    'store_column': 'value',
    'active': True,
}]

ensure_feature_definitions(conn, defs)
```

4. **Compute**:
```python
compute_features(conn, data_id=123, function_names=['fundamental'])
```

## Comparison: Old vs New

### Old Pattern (Indicators Only)
```python
# Manual, indicators only
indicators = ['rsi', 'macd']
feature_map = ensure_indicator_feature_definitions(conn, indicators)

# Fetch price data
price_rows = fetch_prices(conn, data_id)

# Compute
results = compute_indicators(price_rows, indicators)

# Store
insert_computed_features(conn, data_id, results, feature_map)
```

### New Pattern (Any Feature Type)
```python
# Generic, all feature types
compute_features(
    conn,
    data_id=123,
    function_names=['indicator', 'derivative', 'fundamental']
)
```

## Benefits

✅ **Unified**: One system for all feature types
✅ **Declarative**: Metadata drives computation
✅ **Extensible**: Easy to add new feature types
✅ **Composable**: Features can depend on features
✅ **Testable**: Pure compute functions
✅ **Maintainable**: Clear separation of concerns
✅ **Discoverable**: Query feature_definitions to see what's available

## Files

- [src/gefion/features/dispatcher.py](../src/gefion/features/dispatcher.py) - Generic dispatcher
- [src/gefion/features/derivatives.py](../src/gefion/features/derivatives.py) - Derivative compute function
- [src/gefion/indicators/local.py](../src/gefion/indicators/local.py) - Indicator compute function
- [tests/test_feature_dispatcher.py](../tests/test_feature_dispatcher.py) - Dispatcher tests
- [tests/test_compute_derivatives.py](../tests/test_compute_derivatives.py) - Derivative tests
- [src/gefion/cli.py](../src/gefion/cli.py) - CLI integration (features-compute command)

## See Also

- [DERIVATIVE_FEATURES.md](DERIVATIVE_FEATURES.md) - Derivative features documentation
- [OPTIMIZATION_COMPLETE.md](../OPTIMIZATION_COMPLETE.md) - Performance optimizations
