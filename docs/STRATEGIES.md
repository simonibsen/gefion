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
(momentum effect). Academic research shows momentum is one of the most
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

**Theory:** Extreme price moves tend to revert to the mean. Oversold stocks
(low RSI) are expected to bounce; overbought stocks are expected to fall.

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

**Theory:** When the fast moving average crosses above the slow moving average
(golden cross), the trend is bullish. Crossing below (death cross) is bearish.

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
strong buying interest and potential trend continuation.

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
will eventually converge. Trade the spread by going long the underperformer and
short the outperformer.

**Use Case:** Market-neutral statistical arbitrage.

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

**Theory:** When price makes a new low but RSI doesn't (bullish divergence),
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

**Theory:** Periods of low volatility (Bollinger Band squeeze) tend to precede
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

#### ML Signal

**Theory:** Machine learning models can identify complex patterns that
rule-based systems miss. Uses quantile regression or classification predictions
to generate trading signals.

**Use Case:** Pure ML-driven trading; leveraging trained models.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| model_name | str | *required* | Name of trained model |
| model_version | str | latest | Model version |
| horizon_days | int | 7 | Prediction horizon (7/30/90) |
| prediction_type | str | quantile | "quantile" or "classifier" |
| return_threshold | float | 0.02 | Min expected return (q50) to buy |
| max_positions | int | 10 | Maximum concurrent positions |

```bash
g2 backtest run --strategy ml_signal \
  --model-name quantile --model-version 20260103 \
  --return-threshold 0.03
```

**Important:** ML strategies use D-1 predictions to avoid look-ahead bias.
On day D, the strategy only sees predictions generated on day D-1.

---

#### ML Filter

**Theory:** Combine rule-based signal generation with ML confirmation.
The base strategy generates signals; ML filters out signals with poor
expected outcomes.

**Use Case:** Hybrid approach; reducing false signals from rule-based strategies.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| base_strategy | str | momentum | Strategy to filter |
| model_name | str | *required* | Name of trained model |
| model_version | str | latest | Model version |
| horizon_days | int | 7 | Prediction horizon |
| filter_mode | str | confirm | "confirm" or "veto" |
| min_q50 | float | 0.0 | Min expected return to pass filter |

**Filter Modes:**
- `confirm`: Only pass signals with positive ML outlook (min_q50 > 0)
- `veto`: Block signals with strongly negative outlook (allows neutral)

```bash
g2 backtest run --strategy ml_filter \
  --base-strategy momentum \
  --model-name quantile --model-version 20260103 \
  --filter-mode confirm --min-q50 0.02
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
