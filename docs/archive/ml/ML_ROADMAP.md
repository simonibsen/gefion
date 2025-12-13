# ML & Trading System Roadmap

## High-Level Goals

Build a production-grade ML trading system using the existing metadata-driven feature architecture.

### Core Capabilities

1. **Multi-Horizon Predictions**: 7, 30, and 90-day return forecasts
2. **Quantile Regression**: Full distribution predictions (10th, 50th, 90th percentiles)
3. **Trend Classification**: Binary/multi-class predictions for screening
4. **Cross-Sectional Features**: Relative rankings, sector context, market regime
5. **Dynamic Feature Engineering**: Metadata-driven feature generation
6. **Warm-Start Retraining**: Efficient monthly model updates
7. **Trading Strategies**: 7 complete strategies with portfolio simulation
8. **Backtesting Engine**: Rigorous historical validation with point-in-time correctness

---

## Architecture Extensions

### 1. Quantile Distribution Storage (Decision)

**Decision**: Store ML predictions in **dedicated prediction tables** (not as `computed_features`).

- Primary tables: `quantile_predictions`, `prediction_outcomes`, `model_performance`
- Rationale: prediction rows have different semantics and lifecycle than feature engineering (model versioning, PIT evaluation, feature snapshots, calibration metrics).

Schema and details live in `docs/archive/ml/ML_SYSTEM_DESIGN.md`.

---

### 2. Cross-Sectional Features

**Problem**: Features requiring context from all stocks (relative rankings, sector averages, market regime).

**Challenge**: Breaks the "process one stock at a time" model.

**Approach 1: Batch Processing Mode**
```python
def compute_cross_sectional(
    all_stocks_data: Dict[str, pd.DataFrame],  # All stocks, same dates
    feature_specs: List[Dict],
) -> Dict[str, List[Dict]]:
    """
    Compute features requiring cross-sectional context.

    Examples:
    - Sector momentum rank (needs all stocks in sector)
    - Market cap percentile (needs all stocks in universe)
    - Beta relative to market (needs market data + stock data)
    """
    # Group by sector/industry
    # Compute relative metrics
    # Return results for all stocks
```

**CLI Usage:**
```bash
# Normal per-stock processing
g2 features-compute --function-names indicator,derivative

# Cross-sectional batch processing
g2 features-compute --function-names cross_sectional --mode batch
```

**Approach 2: Materialized Views**
```sql
-- Pre-compute sector statistics
CREATE MATERIALIZED VIEW sector_stats AS
SELECT
    sector,
    date,
    AVG(momentum) as avg_momentum,
    STDDEV(momentum) as std_momentum
FROM stocks s
JOIN computed_features cf ON cf.data_id = s.id
-- ... group by sector, date

-- Then compute relative features
SELECT
    (stock_momentum - sector_avg) / sector_std as momentum_zscore
```

**Pros:**
- ✅ Leverages PostgreSQL materialized views
- ✅ Can refresh incrementally

**Cons:**
- ❌ More complex SQL management
- ❌ Less flexible than Python

**Recommendation**: **Approach 1** (batch mode) for flexibility, with caching for performance.

---

### 3. Model Versioning & Lineage

**Schema:**
```sql
CREATE TABLE ml_models (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,  -- 'momentum_predictor'
    version TEXT NOT NULL,  -- '2024-12-v1'
    algorithm TEXT,  -- 'xgboost', 'lightgbm', 'linear'
    hyperparams JSONB,
    training_start DATE,
    training_end DATE,
    trained_at TIMESTAMP DEFAULT NOW(),
    feature_names TEXT[],  -- Which features it uses
    metrics JSONB,  -- {"train_rmse": 0.01, "val_sharpe": 1.2}
    model_path TEXT,  -- Path to pickled model file
    active BOOLEAN DEFAULT TRUE,
    UNIQUE (name, version)
);

-- Link predictions to models
ALTER TABLE feature_definitions
ADD COLUMN model_id INTEGER REFERENCES ml_models(id);
```

**Workflow:**
```bash
# 1. Train model externally
python scripts/train_model.py \
    --name momentum_predictor \
    --features rsi,macd,derivatives \
    --horizon 7 \
    --output models/momentum_7d_v1.pkl

# 2. Register model
g2 ml-register-model \
    --name momentum_predictor \
    --version 2024-12-v1 \
    --path models/momentum_7d_v1.pkl \
    --metrics metrics.json

# 3. Register prediction features (references model)
g2 features-register --definition '{
    "name": "prediction_momentum_7d_q50",
    "function_name": "ml_predict",
    "params": {"model_id": 42, "quantile": 0.5},
    ...
}'

# 4. Generate predictions
g2 features-compute --function-names ml_predict --start 2024-12-01
```

**Benefits:**
- Full audit trail of which model generated which predictions
- Easy A/B testing (activate different model versions)
- Reproducibility (can rerun exact model version)

---

### 4. Trading Strategies

**Schema:**
```sql
CREATE TABLE strategy_definitions (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    strategy_type TEXT,  -- 'momentum', 'value', 'mean_reversion'
    params JSONB,  -- Strategy-specific parameters
    features TEXT[],  -- Which predictions/features to use
    position_sizing TEXT,  -- 'equal_weight', 'volatility_scaled', 'kelly'
    rebalance_frequency TEXT,  -- 'daily', 'weekly', 'monthly'
    max_positions INTEGER,
    active BOOLEAN DEFAULT TRUE
);

-- Event sourcing for portfolio state
CREATE TABLE strategy_events (
    id BIGSERIAL PRIMARY KEY,
    strategy_id INTEGER REFERENCES strategy_definitions(id),
    date DATE NOT NULL,
    event_type TEXT,  -- 'open', 'close', 'rebalance', 'stop_loss'
    symbol TEXT,
    shares NUMERIC,
    price NUMERIC,
    reason TEXT,  -- Why this trade happened
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Materialized current positions
CREATE TABLE strategy_positions (
    strategy_id INTEGER REFERENCES strategy_definitions(id),
    symbol TEXT,
    shares NUMERIC,
    avg_cost NUMERIC,
    current_value NUMERIC,
    unrealized_pnl NUMERIC,
    last_updated DATE,
    PRIMARY KEY (strategy_id, symbol)
);

-- Performance metrics
CREATE TABLE strategy_performance (
    strategy_id INTEGER REFERENCES strategy_definitions(id),
    date DATE,
    nav NUMERIC,
    cash NUMERIC,
    daily_return NUMERIC,
    cumulative_return NUMERIC,
    sharpe_ratio NUMERIC,
    max_drawdown NUMERIC,
    win_rate NUMERIC,
    PRIMARY KEY (strategy_id, date)
);
```

**Strategy Implementation Pattern:**
```python
def compute_strategy_signals(
    stock_data: pd.DataFrame,
    strategy_spec: Dict,
) -> List[Dict]:
    """
    Generate trading signals from predictions/features.

    Returns list of signals:
    [
        {'date': '2024-12-01', 'action': 'buy', 'confidence': 0.8},
        {'date': '2024-12-02', 'action': 'hold', 'confidence': 0.5},
    ]
    """
    # Read strategy params
    # Combine predictions + features
    # Apply strategy logic
    # Return signals
```

**7 Strategy Examples:**

1. **Momentum Following** (aggressive, 7-30d)
   - Buy top decile momentum, short bottom decile
   - Use `prediction_return_7d_q50 > threshold`

2. **Value with Catalyst** (moderate, 30-90d)
   - Fundamental value + positive momentum derivative
   - Use `pe_ratio < sector_avg AND derivative_close_slope_5 > 0`

3. **Capital Preservation** (conservative, 30-90d)
   - Only buy when high confidence, otherwise cash
   - Use `prediction_return_30d_q10 > 0` (even worst case is positive)

4. **Mean Reversion** (aggressive, 7-30d)
   - Buy oversold with positive concavity (turning up)
   - Use `rsi_14 < 30 AND derivative_rsi_14_concavity_5 > 0`

5. **Sector Rotation** (moderate, 30-90d)
   - Overweight strongest sectors
   - Use cross-sectional `sector_momentum_rank`

6. **Volatility Harvesting** (advanced, options)
   - Implied vol vs realized vol arbitrage
   - Use `indicator_atr` and derivatives

7. **Risk Parity** (moderate, 30-90d)
   - Size positions by inverse volatility
   - Use `1 / indicator_atr` for position sizing

---

### 5. Backtesting Engine

**Key Principle: Point-in-Time Correctness**

```python
def backtest(
    strategy_id: int,
    start_date: date,
    end_date: date,
    initial_capital: float = 100000,
) -> Dict:
    """
    Backtest strategy with strict point-in-time data access.

    Critical: Only use data that would have been available at decision time.
    """
    # For each trading date:
    for current_date in trading_days:
        # CRITICAL: Only use data with created_at <= current_date
        # This prevents look-ahead bias
        available_features = fetch_features_as_of(current_date)

        # Generate signals using only available data
        signals = strategy.generate_signals(available_features)

        # Execute trades (simulate fills, slippage)
        trades = execute_trades(signals, current_date)

        # Update portfolio state
        update_portfolio(trades)

        # Record metrics
        record_performance(current_date)

    # Return performance summary
    return {
        'sharpe': compute_sharpe(),
        'max_drawdown': compute_max_drawdown(),
        'win_rate': compute_win_rate(),
        'final_nav': portfolio.nav
    }
```

**Look-Ahead Bias Prevention:**
```sql
-- WRONG: Uses all data with that date (includes future knowledge)
SELECT value FROM computed_features
WHERE date = '2024-01-15' AND feature_id = 42;

-- RIGHT: Only data that existed at that time
SELECT value FROM computed_features
WHERE date = '2024-01-15'
  AND feature_id = 42
  AND created_at <= '2024-01-15 23:59:59';
```

**CLI Usage:**
```bash
# Run backtest
g2 backtest \
    --strategy momentum_7d \
    --start 2020-01-01 \
    --end 2024-12-01 \
    --capital 100000

# Compare strategies
g2 backtest-compare \
    --strategies momentum_7d,value_30d,mean_reversion \
    --start 2020-01-01

# Output performance metrics
```

---

## Implementation Phases

### Phase 1: ML Predictions (Foundation)
- [ ] Implement dataset builder (rolling windows → Parquet / PyTorch DataLoader)
- [ ] Implement label generation (forward returns for 7/30/90d)
- [ ] Define point-in-time splits (walk-forward / rolling validation)
- [ ] Register `ml_predict` compute function
- [ ] Add model versioning tables
- [ ] Implement quantile regression predictions
- [ ] Generate first predictions for 7d horizon

### Phase 2: Cross-Sectional Features
- [ ] Implement batch processing mode
- [ ] Add sector/industry grouping
- [ ] Compute relative rankings
- [ ] Add market regime detection

### Phase 3: Trading Signals
- [ ] Register `trading_signal` compute function
- [ ] Implement signal generation logic
- [ ] Add strategy definitions table
- [ ] Implement first strategy (momentum)

### Phase 4: Backtesting
- [ ] Build backtesting engine with PIT correctness
- [ ] Add portfolio simulation
- [ ] Implement performance metrics
- [ ] Add strategy comparison tools

### Phase 5: Production (Optional)
- [ ] Real-time signal generation
- [ ] Broker integration
- [ ] Risk management
- [ ] Monitoring & alerting

---

## Key Design Decisions

### 1. Feature Store vs ML Training
- **Approach**: Use DB for feature serving, export to Parquet for training
- **Rationale**: SQL pivots are slow for wide matrices, Parquet is optimized for columnar ML access

### 2. Model Artifacts
- **Approach**: Store pickled models in filesystem, reference in DB
- **Rationale**: Binary blobs don't belong in PostgreSQL, use object storage pattern

### 3. Backtest Data Isolation
- **Approach**: Use `created_at` timestamps for point-in-time correctness
- **Rationale**: Prevents look-ahead bias, enables reproducible backtests

### 4. Strategy Execution
- **Approach**: Event sourcing with materialized current state
- **Rationale**: Full audit trail + fast current state queries

---

## Success Metrics

### ML Model Quality
- **Sharpe Ratio**: > 1.5 on validation set
- **Calibration**: Predicted quantiles match realized distributions
- **Stability**: Performance consistent across different time periods

### System Performance
- **Feature Computation**: < 1 hour for full universe daily update
- **Prediction Generation**: < 5 minutes for 500 stocks
- **Backtest Speed**: Full 5-year backtest < 10 minutes

### Trading Strategy
- **Sharpe Ratio**: > 1.0 on out-of-sample backtest
- **Max Drawdown**: < 25%
- **Win Rate**: > 50%
- **Calmar Ratio**: > 0.5 (return / max_drawdown)

---

## References

- [FEATURE_DISPATCHER.md](FEATURE_DISPATCHER.md) - Generic dispatcher architecture
- [DERIVATIVE_FEATURES.md](DERIVATIVE_FEATURES.md) - Derivative feature patterns
- Current Architecture: Metadata-driven, registry-based feature computation
