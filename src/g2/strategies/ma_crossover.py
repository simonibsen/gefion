"""
Moving Average Crossover Trading Strategy.

Buys on golden cross (fast MA crosses above slow MA) and sells on death cross (fast MA crosses below slow MA).
Uses two simple moving averages (SMA) of different periods to identify trend changes.

Strategy Logic:
1. Calculate fast MA (e.g., 50-day) and slow MA (e.g., 200-day) for all stocks
2. Detect golden cross: fast MA crosses above slow MA → BUY signal
3. Detect death cross: fast MA crosses below slow MA → SELL signal
4. Equal-weight position sizing within max_positions limit
5. No signals when MAs are already aligned (no crossover)

Parameters:
- fast_period: Lookback period for fast MA (default: 50)
- slow_period: Lookback period for slow MA (default: 200)
- position_size: Fraction of portfolio per position (default: 0.2 = 20%)
- max_positions: Maximum number of concurrent positions (default: 5)

Example Usage:
    strategy = MovingAverageCrossoverStrategy(
        fast_period=50,
        slow_period=200,
        position_size=0.15,
        max_positions=10,
    )

    signals = strategy.generate_signals(
        current_date=date(2024, 12, 1),
        portfolio={"AAPL": {"shares": 100, "avg_price": 150.0}},
        price_data=price_data,
        initial_cash=100000.0,
    )
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List


class MovingAverageCrossoverStrategy:
    """
    Moving average crossover strategy using fast and slow SMAs.

    Buys on golden cross and sells on death cross.
    """

    def __init__(
        self,
        fast_period: int = 50,
        slow_period: int = 200,
        position_size: float = 0.2,
        max_positions: int = 5,
    ):
        """
        Initialize moving average crossover strategy.

        Args:
            fast_period: Lookback period for fast MA (default: 50)
            slow_period: Lookback period for slow MA (default: 200)
            position_size: Fraction of portfolio per position (default: 0.2)
            max_positions: Maximum concurrent positions (default: 5)
        """
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.position_size = position_size
        self.max_positions = max_positions

    def generate_signals(
        self,
        current_date: date,
        portfolio: Dict[str, Dict[str, Any]],
        price_data: List[Dict[str, Any]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate buy/sell signals based on MA crossovers.

        Args:
            current_date: Current date for signal generation
            portfolio: Current holdings {symbol: {shares, avg_price}}
            price_data: Historical price data
            initial_cash: Available cash for new positions

        Returns:
            List of trading signals with action, symbol, shares
        """
        if not price_data:
            return []

        # Get unique symbols
        symbols = sorted(set(row["symbol"] for row in price_data))

        # Calculate MAs and detect crossovers for each symbol
        crossovers = {}  # symbol -> "golden" or "death"
        current_prices = {}

        for symbol in symbols:
            symbol_data = [row for row in price_data if row["symbol"] == symbol]
            symbol_data = sorted(symbol_data, key=lambda x: x["date"])

            # Need sufficient history for slow MA
            if len(symbol_data) < self.slow_period + 1:
                continue

            # Get recent price history
            recent_data = symbol_data[-(self.slow_period + 1):]

            # Calculate current and previous MAs
            current_fast_ma = self._calculate_sma(recent_data[-self.fast_period:])
            current_slow_ma = self._calculate_sma(recent_data[-self.slow_period:])

            # Previous MAs (one day back)
            prev_fast_ma = self._calculate_sma(recent_data[-self.fast_period-1:-1])
            prev_slow_ma = self._calculate_sma(recent_data[-self.slow_period-1:-1])

            if all(ma is not None for ma in [current_fast_ma, current_slow_ma, prev_fast_ma, prev_slow_ma]):
                # Detect golden cross: fast was below, now above
                if prev_fast_ma <= prev_slow_ma and current_fast_ma > current_slow_ma:
                    crossovers[symbol] = "golden"
                    current_prices[symbol] = recent_data[-1]["close"]

                # Detect death cross: fast was above, now below
                elif prev_fast_ma >= prev_slow_ma and current_fast_ma < current_slow_ma:
                    crossovers[symbol] = "death"
                    current_prices[symbol] = recent_data[-1]["close"]

        # Generate signals
        signals = []

        # Sell signals for death crosses
        for symbol, position in portfolio.items():
            if symbol in crossovers and crossovers[symbol] == "death":
                # Sell entire position
                signals.append({
                    "action": "sell",
                    "symbol": symbol,
                    "shares": position["shares"],
                    "reason": f"death cross (fast MA < slow MA)",
                })

        # Buy signals for golden crosses
        current_positions = len(portfolio)
        available_slots = self.max_positions - current_positions

        if available_slots > 0:
            # Find golden cross stocks not currently held
            golden_crosses = []
            for symbol, crossover_type in crossovers.items():
                if symbol not in portfolio and crossover_type == "golden":
                    golden_crosses.append(symbol)

            # Buy up to available_slots stocks
            for symbol in golden_crosses[:available_slots]:
                current_price = current_prices[symbol]

                # Calculate position size
                position_value = initial_cash * self.position_size
                shares = int(position_value / current_price)

                if shares > 0:
                    signals.append({
                        "action": "buy",
                        "symbol": symbol,
                        "shares": shares,
                        "reason": f"golden cross (fast MA > slow MA)",
                    })

        return signals

    def _calculate_sma(self, price_data: List[Dict[str, Any]]) -> float | None:
        """
        Calculate Simple Moving Average (SMA).

        Args:
            price_data: List of price records sorted by date

        Returns:
            SMA value or None if insufficient data
        """
        if not price_data:
            return None

        closes = [row["close"] for row in price_data]
        return sum(closes) / len(closes)
