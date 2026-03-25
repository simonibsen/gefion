# Trading Strategies Guide

This guide covers g2's trading strategies system: the theory behind each strategy,
how to configure them, and how to create new ones.

## Architecture Overview

### Strategies vs Configs

**Strategies** are Python classes that implement trading logic. They are defined
in code (`src/g2/strategies/`) and cannot be modified at runtime.

**Configs** are parameterized instances of strategies stored in the database.
They allow you to create variations without modifying code:

```
momentum (strategy)     →  momentum_aggressive (config: lookback_days=10)
                        →  momentum_conservative (config: lookback_days=40)
ml_filter (strategy)    →  ml_filter_h7 (config: horizon_days=7)
                        →  ml_filter_h30 (config: horizon_days=30)
```

### System Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    STRATEGY REGISTRY                        │
│    Database table storing strategy metadata and defaults    │
│    (strategy_registry table)                                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    STRATEGY CONFIGS                         │
│    Database table storing parameterized instances           │
│    (strategy_configs table)                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    DISPATCHER                               │
│    Dynamically loads Python classes and instantiates        │
│    with merged parameters                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    BACKTEST ENGINE                          │
│    Simulates trading with point-in-time correctness         │
└─────────────────────────────────────────────────────────────┘
```

## Built-in Strategies

### Rule-Based Strategies

#### Momentum

**Theory:** Stocks that have performed well tend to continue performing well
([momentum effect](https://en.wikipedia.org/wiki/Momentum_(finance))). Academic research shows momentum is one of the most
persistent market anomalies.

**Use Case:** Trend-following; riding winners.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| lookback_days | int | 20 | Days to measure momentum |
| top_n | int | 10 | Number of top stocks to hold |
| rebalance_days | int | 5 | Days between rebalancing |

```bash
g2 backtest run --strategy momentum --lookback-days 10 --top-n 5
```

---

#### Mean Reversion

**Theory:** Extreme price moves tend to [revert to the mean](https://en.wikipedia.org/wiki/Mean_reversion_(finance)). Oversold stocks
(low [RSI](https://en.wikipedia.org/wiki/Relative_strength_index)) are expected to bounce; overbought stocks are expected to fall.

**Use Case:** Counter-trend trading; buying dips.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| rsi_period | int | 14 | RSI calculation period |
| rsi_oversold | int | 30 | RSI level to trigger buy |
| rsi_overbought | int | 70 | RSI level to trigger sell |
| max_positions | int | 5 | Maximum concurrent positions |

```bash
g2 backtest run --strategy mean_reversion --rsi-oversold 25 --rsi-overbought 75
```

---

#### Moving Average Crossover

**Theory:** When the fast [moving average](https://en.wikipedia.org/wiki/Moving_average) crosses above the slow moving average
([golden cross](https://en.wikipedia.org/wiki/Moving_average_crossover)), the trend is bullish. Crossing below (death cross) is bearish.

**Use Case:** Trend identification; timing entries and exits.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| fast_period | int | 50 | Fast moving average period |
| slow_period | int | 200 | Slow moving average period |

```bash
g2 backtest run --strategy ma_crossover --fast-period 20 --slow-period 50
```

---

#### Breakout

**Theory:** When price breaks above recent highs with high volume, it signals
strong buying interest and potential [trend continuation](https://en.wikipedia.org/wiki/Trend_following).

**Use Case:** Catching breakouts from consolidation patterns.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| lookback_days | int | 20 | Days for calculating high/low range |
| volume_threshold | float | 1.5 | Volume multiplier required for confirmation |

```bash
g2 backtest run --strategy breakout --lookback-days 30 --volume-threshold 2.0
```

---

#### Pairs Trading

**Theory:** Correlated stock pairs that diverge from their historical relationship
will eventually converge ([pairs trade](https://en.wikipedia.org/wiki/Pairs_trade)). Trade the spread by going long the underperformer and
short the outperformer.

**Use Case:** Market-neutral [statistical arbitrage](https://en.wikipedia.org/wiki/Statistical_arbitrage).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| lookback_days | int | 60 | Days for correlation/spread calculation |
| entry_zscore | float | 2.0 | Z-score threshold to enter trade |
| exit_zscore | float | 0.5 | Z-score threshold to exit trade |

```bash
g2 backtest run --strategy pairs_trading --entry-zscore 2.5
```

---

#### RSI Divergence

**Theory:** When price makes a new low but [RSI](https://en.wikipedia.org/wiki/Relative_strength_index) doesn't (bullish [divergence](https://en.wikipedia.org/wiki/Divergence_(technical_analysis))),
momentum is shifting upward even though price is falling. This often precedes
reversals.

**Use Case:** Reversal trading; catching bottoms.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| rsi_period | int | 14 | RSI calculation period |
| divergence_lookback | int | 10 | Days to detect divergence pattern |

```bash
g2 backtest run --strategy rsi_divergence --divergence-lookback 15
```

---

#### Volatility Contraction

**Theory:** Periods of low volatility ([Bollinger Band](https://en.wikipedia.org/wiki/Bollinger_Bands) squeeze) tend to precede
periods of high volatility. Trade the expansion by entering when bands widen.

**Use Case:** Volatility breakout trading.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| bb_period | int | 20 | Bollinger Band period |
| bb_std_dev | float | 2.0 | Standard deviations for bands |
| squeeze_threshold | float | 0.05 | Band width to detect squeeze |

```bash
g2 backtest run --strategy volatility_contraction --squeeze-threshold 0.04
```

---

### ML-Integrated Strategies

g2's ML strategies use trained machine learning models to generate or filter
trading signals. These strategies require:

1. **Trained models** - Use `gefion ml train` or `gefion ml train-ensemble`
2. **Stored predictions** - Use `gefion ml predict-ensemble` or `gefion ml predict-classifier`

See [ML Quickstart](ML_QUICKSTART.md) for training workflow.

#### Understanding ML Model Types

g2 supports two types of ML models:

##### Quantile Regression Models

Predict the **distribution of future returns** using [quantile regression](https://en.wikipedia.org/wiki/Quantile_regression) at three quantiles:

| Quantile | Meaning | Use |
|----------|---------|-----|
| **q10** | 10th percentile (downside) | Risk assessment: "90% chance return is above this" |
| **q50** | 50th percentile (median) | Expected return: "most likely outcome" |
| **q90** | 90th percentile (upside) | Opportunity: "10% chance return exceeds this" |

**Example prediction:** AAPL at horizon=7 days
- q10 = -0.03 (3% downside risk)
- q50 = +0.02 (2% expected return)
- q90 = +0.08 (8% upside potential)

**Trading logic:** Buy when q50 > threshold (expect positive returns).

##### Classifier Models

Predict **trend direction** using [gradient boosting](https://en.wikipedia.org/wiki/Gradient_boosting) classifiers ([XGBoost](https://en.wikipedia.org/wiki/XGBoost)/LightGBM) as one of 5 classes:

| Class | Meaning | Expected Return |
|-------|---------|-----------------|
| `strong_down` | Strong bearish | < -10% |
| `weak_down` | Mild bearish | -10% to -2% |
| `flat` | Neutral | -2% to +2% |
| `weak_up` | Mild bullish | +2% to +10% |
| `strong_up` | Strong bullish | > +10% |

**Trading logic:** Buy when predicted class is `weak_up` or `strong_up`.

#### Look-Ahead Bias Prevention

**Critical:** ML strategies use **D-1 predictions** to prevent look-ahead bias.

```
Timeline:
  Day D-1: Generate predictions using features from D-1 close
  Day D:   Strategy sees D-1 predictions, makes trading decision
  Day D+1: Trade executes at D+1 open
```

This ensures the strategy never sees "future" information. When backtesting,
predictions must exist in the database for the historical period.

---

#### ML Signal

**Theory:** Pure ML-driven strategy that uses model predictions directly
to generate buy/sell signals. No rule-based logic—decisions are entirely
based on what the model predicts.

**Use Case:** When you trust your ML model's predictions and want to
trade them directly without additional filters.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| model_name | str | *required* | Name of trained model (e.g., "quantile") |
| model_version | str | latest | Model version (e.g., "20260103-ensemble") |
| horizon_days | int | 7 | Prediction horizon: 7, 30, or 90 days |
| prediction_type | str | quantile | "quantile" or "classifier" |
| return_threshold | float | 0.02 | Min q50 to buy (quantile mode) |
| max_positions | int | 10 | Maximum concurrent positions |

**Quantile Mode Example:**

```bash
# Buy stocks where expected 7-day return (q50) > 3%
g2 backtest run --strategy ml_signal \
  --model-name quantile --model-version 20260103-ensemble \
  --horizon-days 7 \
  --prediction-type quantile \
  --return-threshold 0.03 \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2024-01-01 --end-date 2024-12-01
```

**Classifier Mode Example:**

```bash
# Buy stocks predicted as weak_up or strong_up
g2 backtest run --strategy ml_signal \
  --model-name trend_classifier --model-version 20260103 \
  --horizon-days 30 \
  --prediction-type classifier \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2024-01-01 --end-date 2024-12-01
```

**How It Works:**

1. On each trading day, fetch D-1 predictions from database
2. For quantile: rank symbols by q50, buy top N where q50 > threshold
3. For classifier: buy symbols with bullish class predictions
4. Sell positions when prediction turns bearish or neutral

**When to Use:**
- You have a well-calibrated model with good historical performance
- You want systematic, emotion-free trading
- You're comfortable with pure ML decision-making

---

#### ML Filter

**Theory:** Hybrid strategy that combines rule-based signal generation
with ML-based filtering. The base strategy (e.g., momentum) generates
candidate signals; ML predictions filter out signals with poor expected
outcomes.

**Use Case:** When you trust your rule-based strategy's signal generation
but want to reduce false positives using ML predictions.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| base_strategy | str | momentum | Rule-based strategy to filter |
| model_name | str | *required* | Name of trained model |
| model_version | str | latest | Model version |
| horizon_days | int | 7 | Prediction horizon (must match model) |
| filter_mode | str | confirm | "confirm" or "veto" |
| min_q50 | float | 0.0 | Min expected return to pass filter |
| max_q10 | float | None | Block if downside risk exceeds this |

**Filter Modes Explained:**

##### Confirm Mode (Default)

**Logic:** Signal passes only if ML predicts positive outcome.

```
Base strategy: BUY AAPL
ML prediction: q50 = +0.03 (3% expected return)
min_q50 = 0.02

Decision: PASS (0.03 > 0.02) → Execute buy
```

```
Base strategy: BUY MSFT
ML prediction: q50 = -0.01 (negative expected return)
min_q50 = 0.02

Decision: BLOCK → Skip this signal
```

**Best for:** Conservative trading, reducing false positives.

##### Veto Mode

**Logic:** Signal passes unless ML predicts strongly negative outcome.

```
Base strategy: BUY AAPL
ML prediction: q10 = -0.15 (15% downside risk)
max_q10 = -0.10

Decision: BLOCK (high downside risk)
```

```
Base strategy: BUY MSFT
ML prediction: q10 = -0.03, q50 = 0.00 (neutral)

Decision: PASS (not strongly negative) → Execute buy
```

**Best for:** Aggressive trading, only blocking high-risk signals.

**Examples:**

```bash
# Momentum + ML confirmation: only buy momentum signals with positive outlook
g2 backtest run --strategy ml_filter \
  --base-strategy momentum \
  --model-name quantile --model-version 20260103-ensemble \
  --filter-mode confirm \
  --min-q50 0.02 \
  --symbols AAPL,MSFT,GOOGL,NVDA,TSLA \
  --start-date 2024-01-01 --end-date 2024-12-01
```

```bash
# Mean reversion + ML veto: block only high-risk reversals
g2 backtest run --strategy ml_filter \
  --base-strategy mean_reversion \
  --model-name quantile --model-version 20260103-ensemble \
  --filter-mode veto \
  --max-q10 -0.10 \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2024-01-01 --end-date 2024-12-01
```

**How It Works:**

1. Base strategy generates candidate buy signals
2. For each signal, fetch D-1 ML prediction for that symbol
3. Apply filter logic based on mode:
   - Confirm: Check q50 > min_q50
   - Veto: Check q10 > max_q10 (if set)
4. Execute only signals that pass the filter
5. Sell signals pass through without ML filtering

**When to Use:**
- You have a working rule-based strategy but too many false signals
- You want to add ML "sanity check" without abandoning rule-based logic
- You're experimenting with combining traditional and ML approaches

---

#### ML Strategy Comparison

| Aspect | ML Signal | ML Filter |
|--------|-----------|-----------|
| **Decision source** | 100% ML | Rule-based + ML |
| **Signal generation** | ML predictions | Base strategy |
| **ML role** | Generate signals | Filter signals |
| **Complexity** | Simpler | More complex |
| **Dependency** | Only ML model | ML model + base strategy |
| **Best for** | Pure quant trading | Hybrid approach |

---

#### Creating ML Strategy Configs

Compare different ML configurations:

```bash
# Create configs for different horizons
g2 strategy create-config --name ml_signal_h7 --strategy ml_signal \
  --params '{"model_name": "quantile", "model_version": "20260103-ensemble", "horizon_days": 7, "return_threshold": 0.02}'

g2 strategy create-config --name ml_signal_h30 --strategy ml_signal \
  --params '{"model_name": "quantile", "model_version": "20260103-ensemble", "horizon_days": 30, "return_threshold": 0.05}'

# Create configs for different filter modes
g2 strategy create-config --name ml_filter_confirm --strategy ml_filter \
  --params '{"base_strategy": "momentum", "model_name": "quantile", "filter_mode": "confirm", "min_q50": 0.02}'

g2 strategy create-config --name ml_filter_veto --strategy ml_filter \
  --params '{"base_strategy": "momentum", "model_name": "quantile", "filter_mode": "veto", "max_q10": -0.10}'

# Compare them
g2 backtest compare \
  --strategies ml_signal_h7,ml_signal_h30,ml_filter_confirm,ml_filter_veto \
  --symbols AAPL,MSFT,GOOGL --start-date 2024-01-01 --end-date 2024-12-01
```

---

## Working with Strategy Configs

### Creating Configs

Use the CLI:

```bash
g2 strategy create-config \
  --name momentum_aggressive \
  --strategy momentum \
  --params '{"lookback_days": 10, "top_n": 5}' \
  --description "Aggressive momentum with short lookback"
```

Or use the UI: **Backtesting → Strategy Configs → Create New Config**

### Listing Configs

```bash
g2 strategy configs
```

### Using Configs in Backtests

Configs can be used anywhere strategy names are accepted:

```bash
# Single config
g2 backtest run --strategy momentum_aggressive --symbols AAPL,MSFT

# Compare configs
g2 backtest compare \
  --strategies momentum,momentum_aggressive,momentum_conservative \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2024-01-01 --end-date 2024-12-01
```

### Unregistering Configs

Configs can be removed from the database:

```bash
g2 strategy delete-config --name momentum_aggressive
```

Or use the UI: **Backtesting → Strategy Configs → Unregister**

Note: Unregistering a config does NOT affect the underlying strategy.

---

## Creating New Strategies

To add a new strategy, you must write Python code.

### Step 1: Create Strategy Class

Create a new file in `src/g2/strategies/`:

```python
# src/g2/strategies/my_strategy.py
"""My custom trading strategy."""

from datetime import date
from typing import Any, Dict, List


class MyStrategy:
    """
    Brief description of strategy.

    Theory: Explain the market insight or anomaly this exploits.
    """

    def __init__(
        self,
        param1: int = 10,
        param2: float = 0.5,
    ):
        """Initialize strategy with parameters."""
        self.param1 = param1
        self.param2 = param2

    def generate_signals(
        self,
        current_date: date,
        portfolio: Dict[str, Dict[str, Any]],
        price_data: Dict[str, List[Dict[str, Any]]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate trading signals for the current date.

        Args:
            current_date: The date to generate signals for
            portfolio: Current positions {symbol: {shares, avg_cost}}
            price_data: Historical prices {symbol: [list of OHLCV dicts]}
            initial_cash: Starting capital

        Returns:
            List of signals: [{"action": "buy"|"sell", "symbol": "AAPL",
                              "shares": 100, "reason": "..."}]
        """
        signals = []

        for symbol, prices in price_data.items():
            # Filter to only data up to current_date (point-in-time)
            historical = [p for p in prices if p["date"] <= current_date]

            if len(historical) < self.param1:
                continue  # Not enough data

            # Your trading logic here
            recent_prices = historical[-self.param1:]
            # ...

            if should_buy:
                signals.append({
                    "action": "buy",
                    "symbol": symbol,
                    "shares": calculate_shares(),
                    "reason": f"My strategy triggered (param2={self.param2})"
                })

        return signals
```

### Step 2: Register in Dispatcher

Add to `BUILTIN_STRATEGIES` in `src/g2/strategies/dispatcher.py`:

```python
BUILTIN_STRATEGIES = {
    # ... existing strategies ...

    "my_strategy": {
        "module_path": "g2.strategies.my_strategy",
        "class_name": "MyStrategy",
        "description": "My custom trading strategy",
        "default_params": {
            "param1": 10,
            "param2": 0.5,
        },
        "tags": ["custom", "rule-based"],
    },
}
```

### Step 3: Add CLI Parameters (Optional)

If you want CLI support for parameters, add to `src/g2/cli.py`
in the `backtest_run` function.

### Step 4: Seed Database

Run to update the strategy registry:

```bash
g2 db-init
```

Or manually:

```python
from g2.strategies.dispatcher import seed_builtin_strategies
seed_builtin_strategies(conn)
```

### Step 5: Test

```bash
g2 backtest run --strategy my_strategy --symbols AAPL,MSFT \
  --start-date 2024-01-01 --end-date 2024-12-01
```

---

## Best Practices

### Point-in-Time Correctness

**Critical:** Strategies must only use data available at `current_date`.

```python
# CORRECT: Filter to historical data only
historical = [p for p in prices if p["date"] <= current_date]

# WRONG: Using future data (look-ahead bias)
future_price = prices[-1]["close"]  # May be after current_date!
```

### Position Sizing

Use the `initial_cash` parameter to calculate appropriate position sizes:

```python
position_value = initial_cash * 0.1  # 10% of capital per position
shares = int(position_value / current_price)
```

### Signal Format

Signals must include:

```python
{
    "action": "buy" or "sell",
    "symbol": "AAPL",
    "shares": 100,  # Positive integer
    "reason": "Human-readable explanation"  # For debugging
}
```

### Error Handling

Handle edge cases gracefully:

```python
if not historical:
    return []  # No data, no signals

if symbol not in price_data:
    continue  # Skip missing symbols
```

---

## Strategy Comparison

| Strategy | Type | Best For | Risk Level |
|----------|------|----------|------------|
| momentum | Trend | Bull markets | Medium |
| mean_reversion | Counter-trend | Range-bound markets | Medium-High |
| ma_crossover | Trend | Long-term trends | Low-Medium |
| breakout | Trend | Volatile markets | High |
| pairs_trading | Market-neutral | Any market | Low |
| rsi_divergence | Reversal | Bottoms/tops | High |
| volatility_contraction | Volatility | Pre-breakout | Medium |
| ml_signal | ML-driven | Complex patterns | Varies |
| ml_filter | Hybrid | Reducing false signals | Varies |

---

## See Also

- [Backtesting Guide](BACKTESTING.md) - How to run backtests
- [ML Quickstart](ML_QUICKSTART.md) - Training ML models
- [Architecture](ARCHITECTURE.md) - System design
