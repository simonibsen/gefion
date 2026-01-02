# Backtesting Guide

This guide covers using g2's backtesting engine to test trading strategies on historical data.

## Overview

g2 provides a complete backtesting framework for testing trading strategies with:

- **Point-in-time correctness**: No look-ahead bias - strategies only see past data
- **Portfolio tracking**: Accurate position sizing, cash management, and rebalancing
- **Performance metrics**: Total return, Sharpe ratio, max drawdown, and more
- **Multiple data sources**: Load price data by symbols, exchange, or custom filters
- **Strategy flexibility**: Built-in momentum strategy or implement custom strategies

## Quick Start

### Basic Backtest

Run a momentum backtest on specific symbols:

```bash
g2 backtest run \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --initial-cash 100000 \
  --strategy momentum
```

### Exchange-Based Backtest

Test on NASDAQ stocks with a limit:

```bash
g2 backtest run \
  --exchange NASDAQ \
  --limit 20 \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --initial-cash 100000 \
  --strategy momentum \
  --lookback-days 20 \
  --top-n 5 \
  --rebalance-days 7
```

### JSON Output

Get machine-readable results:

```bash
g2 backtest run \
  --symbols AAPL,MSFT \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --initial-cash 100000 \
  --json > backtest_results.json
```

## CLI Parameters

### Data Selection

- `--symbols`: Comma-separated list of stock symbols (e.g., `AAPL,MSFT,GOOGL`)
- `--exchange`: Exchange name filter (e.g., `NASDAQ`, `NYSE`)
- `--limit`: Maximum number of symbols to test (useful for quick tests)

**Note**: Use either `--symbols` or `--exchange`, not both.

### Backtest Period

- `--start-date`: Start date (YYYY-MM-DD) **[Required]**
- `--end-date`: End date (YYYY-MM-DD) **[Required]**
- `--initial-cash`: Starting portfolio value (default: 100,000)

### Strategy Parameters

For the momentum strategy:

- `--strategy`: Strategy name (default: `momentum`)
- `--lookback-days`: Momentum calculation period (default: 20)
- `--top-n`: Number of top momentum stocks to hold (default: 10)
- `--rebalance-days`: Days between rebalancing (default: 5)

### Output

- `--json`: Output results as JSON instead of formatted text

## Performance Metrics

The backtest engine calculates the following metrics:

### Return Metrics

- **Total Return**: Overall percentage gain/loss from initial capital
- **Final Value**: Ending portfolio value in dollars

### Risk Metrics

- **Sharpe Ratio**: Risk-adjusted return (annualized, assuming 252 trading days)
  - Higher is better (> 1.0 is good, > 2.0 is excellent)
  - Measures return per unit of volatility
- **Max Drawdown**: Largest peak-to-trough decline during backtest period
  - Expressed as negative percentage (e.g., -20% means 20% decline from peak)
  - Measures downside risk

### Activity Metrics

- **Total Trades**: Number of buy/sell transactions executed
- **Symbols Tested**: Number of unique stocks included in backtest

## Example Results

Here's an example backtest output:

```
Backtest Results
Strategy: momentum
Period: 2024-01-01 to 2025-12-17
Symbols: 2

Performance:
  Initial Value: $100,000.00
  Final Value:   $87,010.65
  Total Return:  -12.99%
  Sharpe Ratio:  0.403
  Max Drawdown:  -64.18%

Activity:
  Total Trades:  1
```

**Interpreting these results:**

- The strategy lost 12.99% over the period (underperformed cash)
- Sharpe ratio of 0.403 indicates modest risk-adjusted returns
- Max drawdown of 64.18% shows significant downside risk
- Only 1 trade executed (low activity, possible data issues or poor momentum signals)

## Built-in Strategies

### Momentum Strategy

The momentum strategy identifies stocks with strong recent price performance and holds the top performers.

**How it works:**

1. Calculate momentum for each stock over `lookback_days` period
2. Rank stocks by momentum (price change percentage)
3. Buy equal-weighted positions in top `top_n` stocks
4. Rebalance every `rebalance_days` to maintain top momentum holdings
5. Hold remaining cash when fewer than `top_n` stocks have positive momentum

**Parameters:**

- `--lookback-days`: Momentum calculation period (default: 20 days)
- `--top-n`: Number of top stocks to hold (default: 10)
- `--rebalance-days`: Days between rebalancing (default: 5)

**Best use cases:**

- Trending markets with persistent momentum
- Mid-to-long term horizons (weeks to months)
- Liquid stocks with sufficient trading volume

**Limitations:**

- Performs poorly in mean-reverting or choppy markets
- Requires sufficient historical data (at least `lookback_days` + backtest period)
- Does not account for transaction costs or slippage

## Data Requirements

### Minimum Data

For reliable backtests, ensure you have:

- **Sufficient history**: At least `lookback_days` before your `start_date`
- **Clean data**: No gaps or missing dates during backtest period
- **Multiple symbols**: At least `top_n` stocks with complete data

### Checking Data Availability

Before running a backtest, verify data availability:

```bash
# Check available symbols
psql $DATABASE_URL -c "
  SELECT s.symbol, COUNT(*) as records,
         MIN(o.date) as first_date, MAX(o.date) as last_date
  FROM stocks s
  JOIN stock_ohlcv o ON s.id = o.data_id
  WHERE s.status = 'Active'
  GROUP BY s.symbol
  HAVING COUNT(*) >= 100
  ORDER BY COUNT(*) DESC
  LIMIT 20;
"
```

### Ingesting Historical Data

To get full historical data for backtesting:

```bash
# Ingest specific stocks with full history
g2 data-update --exchange NASDAQ --limit 50 --timeframe full --refresh

# Or ingest from a universe file
g2 universe-ingest --exchange NASDAQ --limit 50 --status Active
```

## Programmatic Usage

You can also use the backtesting engine programmatically:

```python
from datetime import date
from g2.backtest.data_loader import load_price_data_for_backtest
from g2.backtest.engine import BacktestEngine
from g2.strategies.momentum import MomentumStrategy
from g2.config import load_settings

# Load settings
settings = load_settings()

# Load historical price data
price_data = load_price_data_for_backtest(
    db_url=settings.database_url,
    symbols=["AAPL", "MSFT", "GOOGL"],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)

# Initialize strategy
strategy = MomentumStrategy(
    lookback_days=20,
    top_n=5,
    rebalance_days=7,
)

# Create wrapper function for engine
def strategy_fn(current_date, portfolio, prices):
    return strategy.generate_signals(
        current_date=current_date,
        portfolio=portfolio,
        price_data=prices,
        initial_cash=100000.0,
    )

# Run backtest
engine = BacktestEngine(
    price_data=price_data,
    strategy=strategy_fn,
    initial_cash=100000.0,
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)

results = engine.run()

# Access results
print(f"Total Return: {results['metrics']['total_return']:.2%}")
print(f"Sharpe Ratio: {results['metrics']['sharpe_ratio']:.3f}")
print(f"Max Drawdown: {results['metrics']['max_drawdown']:.2%}")
print(f"Trades: {len(results['trades'])}")
```

## Best Practices

### 1. Use Realistic Date Ranges

- Ensure `start_date` is after you have sufficient `lookback_days` of data
- End date should be at least 1-2 days before today to avoid incomplete data

### 2. Test Multiple Periods

Run backtests over different market conditions:

```bash
# Bull market period
g2 backtest run --symbols AAPL,MSFT --start-date 2023-01-01 --end-date 2023-12-31

# Bear market period
g2 backtest run --symbols AAPL,MSFT --start-date 2022-01-01 --end-date 2022-12-31

# Full cycle
g2 backtest run --symbols AAPL,MSFT --start-date 2020-01-01 --end-date 2024-12-31
```

### 3. Start Small

Use `--limit` to test with a small set of stocks first:

```bash
# Quick test with 10 stocks
g2 backtest run --exchange NASDAQ --limit 10 --start-date 2024-01-01 --end-date 2024-12-31
```

### 4. Compare to Benchmark

Always compare strategy performance to a buy-and-hold benchmark:

```bash
# Run strategy backtest
g2 backtest run --symbols SPY --start-date 2024-01-01 --end-date 2024-12-31 \
  --strategy momentum --json > strategy_results.json

# Compare to buy-and-hold (top_n=1, never rebalance)
g2 backtest run --symbols SPY --start-date 2024-01-01 --end-date 2024-12-31 \
  --top-n 1 --rebalance-days 365 --json > benchmark_results.json
```

### 5. Validate Point-in-Time Correctness

The backtest engine ensures no look-ahead bias:

- Prices on date D are only available to the strategy AFTER market close on date D
- Strategy makes decisions for date D+1 using data through date D
- This matches real-world trading where you decide today what to buy tomorrow

## Troubleshooting

### "No price data found for specified parameters"

**Causes:**

- No stocks match your filters (check `--exchange` and `--symbols`)
- Date range has no data (check database with SQL query above)
- Exchange field not set in stocks table

**Solutions:**

```bash
# Check what stocks exist
psql $DATABASE_URL -c "SELECT DISTINCT symbol, exchange FROM stocks WHERE status = 'Active' LIMIT 20;"

# Try without exchange filter
g2 backtest run --symbols AAPL --start-date 2024-01-01 --end-date 2024-12-31

# Ingest more data
g2 data-update --exchange NASDAQ --limit 50 --timeframe full
```

### Zero Trades Executed

**Causes:**

- Insufficient data to calculate momentum (need `lookback_days` of history)
- All stocks have negative momentum (strategy holds cash)
- Date range too short for rebalancing

**Solutions:**

- Ensure data starts at least `lookback_days` before `start_date`
- Try longer date range or more symbols
- Reduce `rebalance_days` for more frequent trading

### Poor Performance

**This is normal!** Not all strategies work in all market conditions:

- Momentum strategies underperform in choppy/mean-reverting markets
- Transaction costs and slippage reduce returns (not yet modeled)
- Past performance doesn't guarantee future results

**Improvements:**

- Test multiple strategies (Item #12 in NEXT_STEPS.md)
- Adjust parameters (`lookback_days`, `top_n`, `rebalance_days`)
- Add transaction cost modeling (future work)
- Implement better risk management (position sizing, stop losses)

## Creating Custom Strategies

This section covers how to implement and register new trading strategies.

### Strategy Interface

Strategies are Python classes that implement a `generate_signals()` method. No base class inheritance is required (duck typing):

```python
class MyStrategy:
    def __init__(self, param1: int = 10, param2: float = 0.5):
        """Initialize with configurable parameters."""
        self.param1 = param1
        self.param2 = param2

    def generate_signals(
        self,
        current_date: date,
        portfolio: Dict[str, Dict[str, Any]],
        price_data: List[Dict[str, Any]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate trading signals for the current date.

        Args:
            current_date: The current date in the backtest
            portfolio: Current holdings {symbol: {shares, avg_price}}
            price_data: Historical OHLCV data up to current_date
            initial_cash: Starting capital for position sizing

        Returns:
            List of signal dicts, each with:
              - action: "buy" or "sell"
              - symbol: Stock symbol (e.g., "AAPL")
              - shares: Number of shares to trade
              - reason: (optional) Explanation for the signal
        """
        signals = []
        # Your strategy logic here...
        return signals
```

### Step 1: Create the Strategy File

Create a new file in `src/g2/strategies/`:

```python
# src/g2/strategies/my_strategy.py
"""
My Custom Trading Strategy.

Brief description of how the strategy works.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List


class MyCustomStrategy:
    """
    Custom strategy implementation.

    Describe the strategy logic and when it generates signals.
    """

    def __init__(
        self,
        threshold: float = 0.05,
        max_positions: int = 5,
        position_size: float = 0.2,
    ):
        """
        Initialize strategy parameters.

        Args:
            threshold: Signal threshold (default: 0.05)
            max_positions: Maximum concurrent positions (default: 5)
            position_size: Fraction of capital per position (default: 0.2)
        """
        self.threshold = threshold
        self.max_positions = max_positions
        self.position_size = position_size

    def generate_signals(
        self,
        current_date: date,
        portfolio: Dict[str, Dict[str, Any]],
        price_data: List[Dict[str, Any]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """Generate buy/sell signals based on strategy logic."""
        signals = []

        # Get unique symbols from price data
        symbols = sorted(set(row["symbol"] for row in price_data))

        for symbol in symbols:
            # Filter price data for this symbol up to current_date
            symbol_prices = [
                row for row in price_data
                if row["symbol"] == symbol and row["date"] <= current_date
            ]

            if len(symbol_prices) < 2:
                continue

            # Sort by date and get current price
            symbol_prices.sort(key=lambda x: x["date"])
            current_price = symbol_prices[-1]["close"]

            # Example: Calculate a simple metric
            prev_price = symbol_prices[-2]["close"]
            change = (current_price - prev_price) / prev_price

            # Generate buy signal if condition met
            if change > self.threshold and symbol not in portfolio:
                position_value = initial_cash * self.position_size
                shares = int(position_value / current_price)

                if shares > 0 and len(portfolio) < self.max_positions:
                    signals.append({
                        "action": "buy",
                        "symbol": symbol,
                        "shares": shares,
                        "reason": f"signal triggered (change: {change:.2%})",
                    })

            # Generate sell signal if condition met
            elif change < -self.threshold and symbol in portfolio:
                signals.append({
                    "action": "sell",
                    "symbol": symbol,
                    "shares": portfolio[symbol]["shares"],
                    "reason": f"exit signal (change: {change:.2%})",
                })

        return signals
```

### Step 2: Register the Strategy

Add your strategy to `BUILTIN_STRATEGIES` in `src/g2/strategies/dispatcher.py`:

```python
BUILTIN_STRATEGIES: Dict[str, Dict[str, Any]] = {
    # ... existing strategies ...

    "my_strategy": {
        "module_path": "g2.strategies.my_strategy",
        "class_name": "MyCustomStrategy",
        "description": "My custom trading strategy",
        "default_params": {
            "threshold": 0.05,
            "max_positions": 5,
            "position_size": 0.2,
        },
        "tags": ["custom", "example"],
    },
}
```

### Step 3: Seed the Database

Run db-init to register the strategy in the database:

```bash
g2 db-init
```

This calls `seed_builtin_strategies()` which inserts your strategy into `strategy_registry`.

### Step 4: Test Your Strategy

Run a backtest to verify it works:

```bash
# Basic test
g2 backtest run \
  --strategy my_strategy \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --initial-cash 100000

# With custom parameters
g2 backtest run \
  --strategy my_strategy \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --threshold 0.03 \
  --max-positions 10
```

### Creating Strategy Configs

Strategy configs are named, parameterized instances of strategies stored in the database:

```bash
# Create an aggressive version of your strategy
g2 strategy-create-config \
  --name my_strategy_aggressive \
  --strategy my_strategy \
  --params '{"threshold": 0.02, "max_positions": 10}'

# Create a conservative version
g2 strategy-create-config \
  --name my_strategy_conservative \
  --strategy my_strategy \
  --params '{"threshold": 0.10, "max_positions": 3}'

# List all configs
g2 strategy-configs
```

Configs merge parameters: `default_params` (from registry) + `config_params` (overrides).

### Strategy Design Tips

1. **No look-ahead bias**: Only use data up to `current_date`
2. **Handle missing data**: Check for sufficient price history
3. **Position sizing**: Use `initial_cash` and `position_size` for consistent sizing
4. **Idempotent signals**: Same inputs should produce same outputs
5. **Clear reasons**: Include `reason` field for debugging

### Example: Using ML Predictions

You can build strategies that use g2's ML predictions:

```python
class MLPredictionStrategy:
    """Strategy that trades based on quantile predictions."""

    def __init__(self, min_expected_return: float = 0.05, horizon: int = 7):
        self.min_expected_return = min_expected_return
        self.horizon = horizon

    def generate_signals(self, current_date, portfolio, price_data, initial_cash):
        signals = []

        # Query predictions from database (pseudo-code)
        # predictions = get_predictions(current_date, self.horizon)

        # Buy stocks where q50 prediction > threshold
        # for pred in predictions:
        #     if pred["q50"] > self.min_expected_return:
        #         signals.append({"action": "buy", ...})

        return signals
```

### File Locations

| File | Purpose |
|------|---------|
| `src/g2/strategies/*.py` | Strategy implementations |
| `src/g2/strategies/dispatcher.py` | Registry and loading logic |
| `src/g2/strategies/__init__.py` | Package exports |
| `tests/test_strategy_*.py` | Strategy tests |

### Future: Live Trading

Strategies are designed to be reusable for live trading. The `generate_signals()` interface produces broker-agnostic signals that can be routed to:

- **Backtesting** (current) - Simulated execution on historical data
- **Paper trading** (planned) - Simulated execution on live data
- **Live trading** (planned) - Real execution via broker APIs

See [ML_ROADMAP.md](ML_ROADMAP.md#61-live--paper-trading) for the planned live trading architecture.

## Next Steps

- **Try ML-based strategies**: Use `g2 ml predict` to generate predictions and build strategies on quantile forecasts
- **Compare multiple strategies**: Run backtests for different approaches and parameters
- **Production deployment**: See [USER_GUIDE.md](USER_GUIDE.md) for automation workflows

## Related Documentation

- [USER_GUIDE.md](USER_GUIDE.md) - Complete g2 workflow guide
- [ML_QUICKSTART.md](ML_QUICKSTART.md) - Machine learning features
- [ML_ROADMAP.md](ML_ROADMAP.md) - Future backtesting enhancements
- [ARCHITECTURE.md](ARCHITECTURE.md) - System design

## References

- Backtest engine implementation: `src/g2/backtest/engine.py`
- Momentum strategy: `src/g2/strategies/momentum.py`
- Data loader: `src/g2/backtest/data_loader.py`
- Metrics calculation: `src/g2/backtest/metrics.py`
- Tests: `tests/test_backtest_engine.py`, `tests/test_backtest_cli.py`
