

# Derivative Features for ML

Slope and concavity features capture trend and acceleration patterns in time series data.

## Two Approaches

### Approach 1: Utility Library (Quick Experimentation)

**Use when**: Experimenting with ML models, ad-hoc analysis

```python
from g2.features.derivatives import add_derivative_features

# Fetch data your way
df = pd.read_sql("SELECT * FROM computed_features WHERE ...", conn)

# Add derivatives on-the-fly
df = add_derivative_features(
    df,
    columns=['rsi_14', 'macd'],
    slope_window=5,
    concavity_window=5
)

# Use in ML model
X = df[['rsi_14', 'rsi_14_slope_5', 'rsi_14_concavity_5']]
model.fit(X, y)
```

**Pros**:
- ✅ Fast to experiment
- ✅ No database changes
- ✅ Flexible (any window size, any column)

**Cons**:
- ❌ Computed every time (not cached)
- ❌ Not integrated with calc_store
- ❌ Manual/imperative

### Approach 2: Calc Store Pattern (Production)

**Use when**: Proven valuable, want to materialize/cache, production ML

```python
from g2.features.calc_store import (
    ensure_derivative_feature_definitions,
    compute_derivative_features,
)

# Step 1: Define features (once) - declarative metadata
feature_map = ensure_derivative_feature_definitions(
    conn,
    source_features=['rsi_14', 'macd'],
    derivative_types=['slope', 'concavity'],
    windows=[5, 10, 20],
)

# Step 2: Compute features (repeatedly) - driven by metadata
inserted = compute_derivative_features(conn, data_id=123)

# Step 3: Query like any other feature
df = pd.read_sql("""
    SELECT cf.date, cf.value
    FROM computed_features cf
    JOIN feature_definitions fd ON fd.id = cf.feature_id
    WHERE fd.name = 'derivative_rsi_14_slope_5'
      AND cf.data_id = 123
""", conn)
```

**Pros**:
- ✅ Follows calc_store pattern
- ✅ Results cached in database
- ✅ Declarative (metadata-driven)
- ✅ Composable (can query via feature_definitions)
- ✅ Production-ready

**Cons**:
- ❌ More setup
- ❌ Requires database changes
- ❌ Less flexible (need to add definitions for new configs)

## Calc Store Pattern Explained

The calc_store pattern separates **definition** from **computation** from **storage**:

```
┌─────────────────────────────────────────┐
│ feature_definitions (metadata)          │
├─────────────────────────────────────────┤
│ id │ name              │ params          │
├────┼───────────────────┼─────────────────┤
│ 5  │ indicator_rsi_14  │ {type: rsi,...} │
│ 42 │ derivative_rsi... │ {source: rsi... │
└─────────────────────────────────────────┘
              ↓
    [Compute Engine reads definitions]
              ↓
┌─────────────────────────────────────────┐
│ computed_features (materialized)        │
├─────────────────────────────────────────┤
│ feature_id │ date       │ value         │
├────────────┼────────────┼───────────────┤
│ 5          │ 2024-01-01 │ 65.3         │
│ 42         │ 2024-01-01 │ 2.5 (slope)  │
└─────────────────────────────────────────┘
```

### Benefits

1. **Declarative**: Features defined as data, not code
2. **Discoverable**: Query metadata to find features
3. **Cacheable**: Computed once, queried many times
4. **Composable**: Build derivatives of derivatives
5. **Versioned**: Track feature definitions over time

### Example: Composability

```python
# Define base indicator
ensure_indicator_feature_definitions(conn, ['rsi'])

# Define derivative of base indicator
ensure_derivative_feature_definitions(
    conn,
    source_features=['rsi_14'],
    derivative_types=['slope']
)

# Define derivative of derivative (acceleration of acceleration!)
ensure_derivative_feature_definitions(
    conn,
    source_features=['derivative_rsi_14_slope_5'],
    derivative_types=['slope']  # Slope of slope = 3rd derivative!
)
```

## Feature Types

### Slope (First Derivative)

**What**: Rate of change over time

**Interpretation**:
- Positive: Upward trend
- Negative: Downward trend
- Magnitude: Trend strength

**ML Value**: Captures momentum/direction

**Example**:
```
Price: [100, 102, 105, 109, 114]
Slope: [  -, 2.0, 3.0, 4.0, 5.0]  # Increasing slope = accelerating
```

### Concavity (Second Derivative)

**What**: Acceleration/deceleration

**Interpretation**:
- Positive: Accelerating upward / decelerating downward
- Negative: Decelerating upward / accelerating downward
- Zero: Steady trend

**ML Value**: Early reversal signal (changes before slope changes)

**Example**:
```
Price:      [100, 105, 109, 112, 114, 115, 115.5]
Slope:      [  -, 5.0, 4.0, 3.0, 2.0, 1.0, 0.5]  # Slowing down
Concavity:  [  -,   -, -1.0, -1.0, -1.0, -1.0, -0.5]  # Negative = decelerating
```

## Recommended Workflow

1. **Start with Approach 1** (utility library)
   - Experiment with different windows (3, 5, 10, 20, 50)
   - Test ML model performance
   - Iterate quickly

2. **If valuable, migrate to Approach 2** (calc_store)
   - Define proven features in `feature_definitions`
   - Compute and cache in database
   - Use in production ML pipeline

3. **Monitor feature importance**
   - Track which derivatives improve model performance
   - Deactivate unused derivatives
   - Add new derivatives based on analysis

## Performance Comparison

| Aspect | Utility Library | Calc Store |
|--------|----------------|------------|
| First run | Fast (compute on-demand) | Slow (initial computation) |
| Subsequent runs | Slow (recompute) | Fast (cached) |
| Storage | None | Database |
| Flexibility | High | Medium |
| Production | Good | Better |

## Examples

- **Quick experiment**: [examples/derivative_features_example.py](../examples/derivative_features_example.py)
- **Calc store pattern**: [examples/calc_store_derivatives.py](../examples/calc_store_derivatives.py)

## See Also

- [src/g2/features/derivatives.py](../src/g2/features/derivatives.py) - Utility library
- [src/g2/features/calc_store.py](../src/g2/features/calc_store.py) - Calc store integration
- [tests/test_derivative_features.py](../tests/test_derivative_features.py) - Tests and examples
