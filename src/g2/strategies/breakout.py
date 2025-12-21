"""
Breakout Trading Strategy.

Buys on upside breakouts (price breaks above recent range) with volume confirmation,
and sells on downside breakouts (price breaks below recent range).

Strategy Logic:
1. Calculate recent high/low over lookback period for each stock
2. Calculate average volume over lookback period
3. Generate BUY signals when:
   - Current high breaks above recent high
   - Current volume exceeds average volume × volume_threshold
   - Not already holding the stock
4. Generate SELL signals when:
   - Current low breaks below recent low
   - Current volume exceeds average volume × volume_threshold
   - Currently holding the stock
5. Equal-weight position sizing within max_positions limit

Parameters:
- lookback_days: Period for calculating recent high/low (default: 20)
- volume_threshold: Volume multiplier for confirmation (default: 1.5 = 150% of average)
- position_size: Fraction of portfolio per position (default: 0.2 = 20%)
- max_positions: Maximum number of concurrent positions (default: 5)

Example Usage:
    strategy = BreakoutStrategy(
        lookback_days=20,
        volume_threshold=1.5,
        position_size=0.25,
        max_positions=3,
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


class BreakoutStrategy:
    """
    Breakout strategy with volume confirmation.

    Buys on upside breakouts and sells on downside breakouts when volume confirms the move.
    """

    def __init__(
        self,
        lookback_days: int = 20,
        volume_threshold: float = 1.5,
        position_size: float = 0.2,
        max_positions: int = 5,
    ):
        """
        Initialize breakout strategy.

        Args:
            lookback_days: Period for calculating recent high/low (default: 20)
            volume_threshold: Volume multiplier for confirmation (default: 1.5)
            position_size: Fraction of portfolio per position (default: 0.2)
            max_positions: Maximum concurrent positions (default: 5)
        """
        self.lookback_days = lookback_days
        self.volume_threshold = volume_threshold
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
        Generate buy/sell signals based on breakouts with volume confirmation.

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

        # Analyze each symbol for breakouts
        breakouts = {}
        current_prices = {}

        for symbol in symbols:
            symbol_data = [row for row in price_data if row["symbol"] == symbol]
            symbol_data = sorted(symbol_data, key=lambda x: x["date"])

            # Need sufficient history for lookback period
            if len(symbol_data) < self.lookback_days + 1:
                continue

            # Get recent data for analysis
            recent_data = symbol_data[-(self.lookback_days + 1):]

            # Calculate recent range (excluding current day)
            lookback_data = recent_data[:-1]
            recent_high = max(row["high"] for row in lookback_data)
            recent_low = min(row["low"] for row in lookback_data)
            avg_volume = sum(row["volume"] for row in lookback_data) / len(lookback_data)

            # Check current day for breakout
            current_bar = recent_data[-1]
            current_high = current_bar["high"]
            current_low = current_bar["low"]
            current_volume = current_bar["volume"]
            current_close = current_bar["close"]

            # Volume confirmation required
            volume_confirmed = current_volume > (avg_volume * self.volume_threshold)

            # Detect breakouts
            if current_high > recent_high and volume_confirmed:
                breakouts[symbol] = "upside"
                current_prices[symbol] = current_close
            elif current_low < recent_low and volume_confirmed:
                breakouts[symbol] = "downside"
                current_prices[symbol] = current_close

        # Generate signals
        signals = []

        # Sell signals for downside breakouts
        for symbol, position in portfolio.items():
            if symbol in breakouts and breakouts[symbol] == "downside":
                signals.append({
                    "action": "sell",
                    "symbol": symbol,
                    "shares": position["shares"],
                    "reason": "downside breakout",
                })

        # Buy signals for upside breakouts
        current_positions = len(portfolio)
        available_slots = self.max_positions - current_positions

        if available_slots > 0:
            # Find upside breakouts not currently held
            upside_breakouts = [
                symbol for symbol, direction in breakouts.items()
                if direction == "upside" and symbol not in portfolio
            ]

            # Buy top N stocks (limit by available slots)
            for symbol in upside_breakouts[:available_slots]:
                current_price = current_prices[symbol]

                # Calculate position size
                position_value = initial_cash * self.position_size
                shares = int(position_value / current_price)

                if shares > 0:
                    signals.append({
                        "action": "buy",
                        "symbol": symbol,
                        "shares": shares,
                        "reason": "upside breakout",
                    })

        return signals
