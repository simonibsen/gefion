"""
Mean Reversion Trading Strategy.

Buys oversold stocks (RSI < threshold) and sells overbought stocks (RSI > threshold).
Uses Relative Strength Index (RSI) to identify extreme price movements that may revert.

Strategy Logic:
1. Calculate RSI for all stocks using recent price history
2. Generate BUY signals for stocks with RSI below oversold threshold (default: 30)
3. Generate SELL signals for held stocks with RSI above overbought threshold (default: 70)
4. Equal-weight position sizing within max_positions limit
5. No signals for stocks with neutral RSI (between thresholds)

Parameters:
- rsi_oversold: RSI threshold for buy signals (default: 30)
- rsi_overbought: RSI threshold for sell signals (default: 70)
- rsi_period: Lookback period for RSI calculation (default: 14)
- position_size: Fraction of portfolio per position (default: 0.2 = 20%)
- max_positions: Maximum number of concurrent positions (default: 5)

Example Usage:
    strategy = MeanReversionStrategy(
        rsi_oversold=25,
        rsi_overbought=75,
        rsi_period=14,
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


class MeanReversionStrategy:
    """
    Mean reversion strategy using RSI indicator.

    Buys oversold stocks and sells overbought stocks based on RSI thresholds.
    """

    def __init__(
        self,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        rsi_period: int = 14,
        position_size: float = 0.2,
        max_positions: int = 5,
        mode: str = "long_only",
    ):
        """
        Initialize mean reversion strategy.

        Args:
            rsi_oversold: RSI threshold for buy signals (default: 30)
            rsi_overbought: RSI threshold for sell signals (default: 70)
            rsi_period: Lookback period for RSI calculation (default: 14)
            position_size: Fraction of portfolio per position (default: 0.2)
            max_positions: Maximum concurrent positions (default: 5)
        """
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.rsi_period = rsi_period
        self.position_size = position_size
        self.max_positions = max_positions
        self.mode = mode

    def generate_signals(
        self,
        current_date: date,
        portfolio: Dict[str, Dict[str, Any]],
        price_data: List[Dict[str, Any]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate buy/sell signals based on RSI levels.

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

        # Normalize to Dict[str, List[Dict]] format
        if isinstance(price_data, dict):
            price_by_symbol = price_data
        else:
            from collections import defaultdict
            price_by_symbol = defaultdict(list)
            for row in price_data:
                price_by_symbol[row["symbol"]].append(row)
            price_by_symbol = dict(price_by_symbol)

        # Get unique symbols
        symbols = sorted(price_by_symbol.keys())

        # Calculate RSI for each symbol
        rsi_values = {}
        current_prices = {}

        for symbol in symbols:
            symbol_data = sorted(price_by_symbol[symbol], key=lambda x: x["date"])

            # Need sufficient history for RSI
            if len(symbol_data) < self.rsi_period + 1:
                continue

            # Get recent price history
            recent_data = symbol_data[-(self.rsi_period + 1):]

            # Calculate RSI
            rsi = self._calculate_rsi(recent_data)
            if rsi is not None:
                rsi_values[symbol] = rsi
                current_prices[symbol] = recent_data[-1]["close"]

        # Generate signals
        signals = []

        # Get positions dict (handle both Portfolio object and dict)
        if hasattr(portfolio, 'positions'):
            positions = portfolio.positions
        else:
            positions = portfolio

        # Exit overbought long positions
        for symbol, position in positions.items():
            if (symbol in rsi_values and rsi_values[symbol] > self.rsi_overbought
                    and position["shares"] > 0):
                signals.append({
                    "action": "sell",
                    "symbol": symbol,
                    "shares": position["shares"],
                    "reason": f"overbought (RSI: {rsi_values[symbol]:.1f})",
                })

        # Short the overbought (long_short mode only): overbought names not
        # already held. In long_only these are simply not traded (flatten).
        if self.mode == "long_short":
            held = set(positions.keys())
            for symbol, rsi in rsi_values.items():
                if rsi > self.rsi_overbought and symbol not in held:
                    shares = int((initial_cash * self.position_size)
                                 / current_prices[symbol])
                    if shares > 0:
                        signals.append({
                            "action": "short",
                            "symbol": symbol,
                            "shares": shares,
                            "reason": f"overbought short (RSI: {rsi:.1f})",
                        })

        # Buy signals for oversold stocks
        current_positions = len(positions)
        available_slots = self.max_positions - current_positions

        if available_slots > 0:
            # Find oversold stocks not currently held
            oversold = []
            for symbol, rsi in rsi_values.items():
                if symbol not in positions and rsi < self.rsi_oversold:
                    oversold.append((symbol, rsi))

            # Sort by RSI (most oversold first)
            oversold.sort(key=lambda x: x[1])

            # Buy top N most oversold stocks
            for symbol, rsi in oversold[:available_slots]:
                current_price = current_prices[symbol]

                # Calculate position size
                position_value = initial_cash * self.position_size
                shares = int(position_value / current_price)

                if shares > 0:
                    signals.append({
                        "action": "buy",
                        "symbol": symbol,
                        "shares": shares,
                        "reason": f"oversold (RSI: {rsi:.1f})",
                    })

        return signals

    def _calculate_rsi(self, price_data: List[Dict[str, Any]]) -> float | None:
        """
        Calculate Relative Strength Index (RSI).

        Args:
            price_data: List of price records sorted by date

        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(price_data) < self.rsi_period + 1:
            return None

        # Calculate price changes
        changes = []
        for i in range(1, len(price_data)):
            change = price_data[i]["close"] - price_data[i - 1]["close"]
            changes.append(change)

        # Separate gains and losses
        gains = [max(0, change) for change in changes]
        losses = [abs(min(0, change)) for change in changes]

        # Calculate average gain and loss over period
        avg_gain = sum(gains[-self.rsi_period:]) / self.rsi_period
        avg_loss = sum(losses[-self.rsi_period:]) / self.rsi_period

        # Avoid division by zero
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        return rsi
