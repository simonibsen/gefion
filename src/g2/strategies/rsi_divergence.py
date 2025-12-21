"""
RSI Divergence Trading Strategy.

Detects divergence between price action and RSI indicator to identify potential reversals.
Bullish divergence: price makes lower lows while RSI makes higher lows (reversal up).
Bearish divergence: price makes higher highs while RSI makes lower highs (reversal down).

Strategy Logic:
1. Calculate RSI for each stock
2. Find recent peaks (highs) and troughs (lows) in both price and RSI
3. Detect divergences:
   - Bullish: price trough lower than previous, RSI trough higher → buy signal
   - Bearish: price peak higher than previous, RSI peak lower → sell signal
4. Generate signals only in extreme RSI zones (oversold for bullish, overbought for bearish)
5. Equal-weight position sizing within max_positions limit

Parameters:
- rsi_period: Period for RSI calculation (default: 14)
- divergence_lookback: Days to look back for peak/trough detection (default: 10)
- rsi_oversold: RSI threshold for oversold (default: 30)
- rsi_overbought: RSI threshold for overbought (default: 70)
- position_size: Fraction of portfolio per position (default: 0.2 = 20%)
- max_positions: Maximum number of concurrent positions (default: 5)

Example Usage:
    strategy = RSIDivergenceStrategy(
        rsi_period=14,
        divergence_lookback=10,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        position_size=0.25,
        max_positions=5,
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
from typing import Any, Dict, List, Tuple


class RSIDivergenceStrategy:
    """
    RSI divergence strategy for reversal detection.

    Buys on bullish divergence (oversold) and sells on bearish divergence (overbought).
    """

    def __init__(
        self,
        rsi_period: int = 14,
        divergence_lookback: int = 10,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        position_size: float = 0.2,
        max_positions: int = 5,
    ):
        """
        Initialize RSI divergence strategy.

        Args:
            rsi_period: Period for RSI calculation (default: 14)
            divergence_lookback: Days to look back for divergence detection (default: 10)
            rsi_oversold: RSI oversold threshold (default: 30)
            rsi_overbought: RSI overbought threshold (default: 70)
            position_size: Fraction of portfolio per position (default: 0.2)
            max_positions: Maximum concurrent positions (default: 5)
        """
        self.rsi_period = rsi_period
        self.divergence_lookback = divergence_lookback
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
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
        Generate buy/sell signals based on RSI divergence.

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

        # Analyze each symbol for divergences
        divergences = []

        for symbol in symbols:
            symbol_data = [row for row in price_data if row["symbol"] == symbol]
            symbol_data = sorted(symbol_data, key=lambda x: x["date"])

            # Need sufficient history for RSI + divergence detection
            min_required = self.rsi_period + self.divergence_lookback + 1
            if len(symbol_data) < min_required:
                continue

            # Get recent data
            recent_data = symbol_data[-(min_required):]

            # Calculate RSI
            rsi_values = self._calculate_rsi_series(recent_data)
            if not rsi_values:
                continue

            # Extract prices and dates
            prices = [row["close"] for row in recent_data[self.rsi_period:]]
            dates = [row["date"] for row in recent_data[self.rsi_period:]]

            # Detect divergences
            bullish_div = self._detect_bullish_divergence(prices, rsi_values)
            bearish_div = self._detect_bearish_divergence(prices, rsi_values)

            current_rsi = rsi_values[-1]
            current_price = prices[-1]

            # Bullish divergence: buy if RSI is oversold
            if bullish_div and current_rsi < self.rsi_oversold and symbol not in portfolio:
                divergences.append({
                    "symbol": symbol,
                    "type": "bullish",
                    "rsi": current_rsi,
                    "price": current_price,
                })

            # Bearish divergence: sell if RSI is overbought and we hold it
            if bearish_div and current_rsi > self.rsi_overbought and symbol in portfolio:
                divergences.append({
                    "symbol": symbol,
                    "type": "bearish",
                    "rsi": current_rsi,
                    "price": current_price,
                })

        # Generate signals
        signals = []

        # Sell signals for bearish divergences
        for div in divergences:
            if div["type"] == "bearish" and div["symbol"] in portfolio:
                signals.append({
                    "action": "sell",
                    "symbol": div["symbol"],
                    "shares": portfolio[div["symbol"]]["shares"],
                    "reason": f"bearish divergence (RSI: {div['rsi']:.1f})",
                })

        # Buy signals for bullish divergences
        current_positions = len(portfolio)
        available_slots = self.max_positions - current_positions

        if available_slots > 0:
            # Find bullish divergences not currently held
            bullish = [div for div in divergences if div["type"] == "bullish" and div["symbol"] not in portfolio]

            # Sort by RSI (most oversold first)
            bullish.sort(key=lambda x: x["rsi"])

            # Buy top N most oversold with divergence
            for div in bullish[:available_slots]:
                current_price = div["price"]

                # Calculate position size
                position_value = initial_cash * self.position_size
                shares = int(position_value / current_price)

                if shares > 0:
                    signals.append({
                        "action": "buy",
                        "symbol": div["symbol"],
                        "shares": shares,
                        "reason": f"bullish divergence (RSI: {div['rsi']:.1f})",
                    })

        return signals

    def _calculate_rsi_series(self, price_data: List[Dict[str, Any]]) -> List[float]:
        """
        Calculate RSI values for a series of prices.

        Args:
            price_data: List of price records sorted by date

        Returns:
            List of RSI values (one per day after rsi_period)
        """
        if len(price_data) < self.rsi_period + 1:
            return []

        prices = [row["close"] for row in price_data]
        rsi_values = []

        # Calculate RSI for each day (starting from rsi_period)
        for i in range(self.rsi_period, len(prices)):
            window = prices[i - self.rsi_period:i + 1]
            rsi = self._calculate_rsi(window)
            if rsi is not None:
                rsi_values.append(rsi)

        return rsi_values

    def _calculate_rsi(self, prices: List[float]) -> float | None:
        """
        Calculate RSI for a price window.

        Args:
            prices: List of prices (length = rsi_period + 1)

        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(prices) < self.rsi_period + 1:
            return None

        # Calculate price changes
        changes = []
        for i in range(1, len(prices)):
            change = prices[i] - prices[i - 1]
            changes.append(change)

        # Separate gains and losses
        gains = [max(0, change) for change in changes]
        losses = [abs(min(0, change)) for change in changes]

        # Calculate average gain and loss over period
        avg_gain = sum(gains) / self.rsi_period
        avg_loss = sum(losses) / self.rsi_period

        # Avoid division by zero
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        return rsi

    def _detect_bullish_divergence(self, prices: List[float], rsi_values: List[float]) -> bool:
        """
        Detect bullish divergence (price lower low, RSI higher low).

        Args:
            prices: Recent price series
            rsi_values: Corresponding RSI values

        Returns:
            True if bullish divergence detected
        """
        if len(prices) < self.divergence_lookback or len(rsi_values) < self.divergence_lookback:
            return False

        # Find troughs (local minima) in recent data
        price_troughs = self._find_troughs(prices[-self.divergence_lookback:])
        rsi_troughs = self._find_troughs(rsi_values[-self.divergence_lookback:])

        # Need at least 2 troughs to compare
        if len(price_troughs) < 2 or len(rsi_troughs) < 2:
            return False

        # Check if most recent price trough is lower than previous (lower low)
        recent_price_trough = price_troughs[-1]
        prev_price_trough = price_troughs[-2]

        # Check if most recent RSI trough is higher than previous (higher low)
        recent_rsi_trough = rsi_troughs[-1]
        prev_rsi_trough = rsi_troughs[-2]

        # Bullish divergence: price makes lower low, RSI makes higher low
        if recent_price_trough < prev_price_trough and recent_rsi_trough > prev_rsi_trough:
            return True

        return False

    def _detect_bearish_divergence(self, prices: List[float], rsi_values: List[float]) -> bool:
        """
        Detect bearish divergence (price higher high, RSI lower high).

        Args:
            prices: Recent price series
            rsi_values: Corresponding RSI values

        Returns:
            True if bearish divergence detected
        """
        if len(prices) < self.divergence_lookback or len(rsi_values) < self.divergence_lookback:
            return False

        # Find peaks (local maxima) in recent data
        price_peaks = self._find_peaks(prices[-self.divergence_lookback:])
        rsi_peaks = self._find_peaks(rsi_values[-self.divergence_lookback:])

        # Need at least 2 peaks to compare
        if len(price_peaks) < 2 or len(rsi_peaks) < 2:
            return False

        # Check if most recent price peak is higher than previous (higher high)
        recent_price_peak = price_peaks[-1]
        prev_price_peak = price_peaks[-2]

        # Check if most recent RSI peak is lower than previous (lower high)
        recent_rsi_peak = rsi_peaks[-1]
        prev_rsi_peak = rsi_peaks[-2]

        # Bearish divergence: price makes higher high, RSI makes lower high
        if recent_price_peak > prev_price_peak and recent_rsi_peak < prev_rsi_peak:
            return True

        return False

    def _find_peaks(self, values: List[float]) -> List[float]:
        """
        Find local peaks (maxima) in a series.

        Args:
            values: Series of values

        Returns:
            List of peak values
        """
        if len(values) < 3:
            return []

        peaks = []
        for i in range(1, len(values) - 1):
            # Peak: value higher than neighbors
            if values[i] > values[i - 1] and values[i] > values[i + 1]:
                peaks.append(values[i])

        return peaks

    def _find_troughs(self, values: List[float]) -> List[float]:
        """
        Find local troughs (minima) in a series.

        Args:
            values: Series of values

        Returns:
            List of trough values
        """
        if len(values) < 3:
            return []

        troughs = []
        for i in range(1, len(values) - 1):
            # Trough: value lower than neighbors
            if values[i] < values[i - 1] and values[i] < values[i + 1]:
                troughs.append(values[i])

        return troughs
