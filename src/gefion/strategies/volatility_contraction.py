"""
Volatility Contraction (Bollinger Band Squeeze) Trading Strategy.

Detects periods of low volatility (Bollinger Band squeeze) followed by expansion/breakout.
Enters positions when volatility expands after contraction, exits when volatility contracts again.

Strategy Logic:
1. Calculate Bollinger Bands (SMA ± std_dev × multiplier)
2. Measure band width: (upper_band - lower_band) / middle_band
3. Detect squeeze: band_width < squeeze_threshold (low volatility)
4. Detect expansion: band_width > expansion_threshold after previous squeeze
5. Enter positions when volatility expands from squeeze (breakout starting)
6. Exit positions when volatility contracts again (breakout ending)
7. Equal-weight position sizing within max_positions limit

Parameters:
- bb_period: Bollinger Band moving average period (default: 20)
- bb_std_dev: Number of standard deviations for bands (default: 2.0)
- squeeze_threshold: Band width threshold for squeeze detection (default: 0.05 = 5%)
- expansion_threshold: Band width threshold for expansion detection (default: 0.10 = 10%)
- position_size: Fraction of portfolio per position (default: 0.2 = 20%)
- max_positions: Maximum number of concurrent positions (default: 5)

Example Usage:
    strategy = VolatilityContractionStrategy(
        bb_period=20,
        bb_std_dev=2.0,
        squeeze_threshold=0.05,
        expansion_threshold=0.10,
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
import math


class VolatilityContractionStrategy:
    """
    Volatility contraction strategy (Bollinger Band squeeze).

    Buys on expansion from squeeze and sells on contraction after expansion.
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std_dev: float = 2.0,
        squeeze_threshold: float = 0.05,
        expansion_threshold: float = 0.10,
        position_size: float = 0.2,
        max_positions: int = 5,
    ):
        """
        Initialize volatility contraction strategy.

        Args:
            bb_period: Bollinger Band moving average period (default: 20)
            bb_std_dev: Number of standard deviations for bands (default: 2.0)
            squeeze_threshold: Band width threshold for squeeze (default: 0.05)
            expansion_threshold: Band width threshold for expansion (default: 0.10)
            position_size: Fraction of portfolio per position (default: 0.2)
            max_positions: Maximum concurrent positions (default: 5)
        """
        self.bb_period = bb_period
        self.bb_std_dev = bb_std_dev
        self.squeeze_threshold = squeeze_threshold
        self.expansion_threshold = expansion_threshold
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
        Generate buy/sell signals based on Bollinger Band squeeze and expansion.

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

        # Analyze each symbol for volatility patterns
        expansion_candidates = []
        contraction_candidates = []

        for symbol in symbols:
            symbol_data = [row for row in price_data if row["symbol"] == symbol]
            symbol_data = sorted(symbol_data, key=lambda x: x["date"])

            # Need sufficient history for Bollinger Bands
            min_required = self.bb_period + 1
            if len(symbol_data) < min_required:
                continue

            # Get recent data
            recent_data = symbol_data[-(min_required + 10):]  # Extra for squeeze detection

            # Calculate Bollinger Bands and band width over time
            bb_data = self._calculate_bollinger_bands_series(recent_data)

            if not bb_data:
                continue

            # Check current volatility state
            current_bb = bb_data[-1]
            current_price = current_bb["close"]
            current_width = current_bb["band_width"]

            # Check if there was a recent squeeze (low volatility period)
            recent_squeeze = self._had_recent_squeeze(bb_data)

            # Expansion: volatility expanding from squeeze
            if recent_squeeze and current_width > self.expansion_threshold:
                # Only buy if we don't already hold it
                if symbol not in portfolio:
                    expansion_candidates.append({
                        "symbol": symbol,
                        "price": current_price,
                        "band_width": current_width,
                    })

            # Contraction: volatility contracting (exit signal)
            if current_width < self.squeeze_threshold and symbol in portfolio:
                contraction_candidates.append({
                    "symbol": symbol,
                    "price": current_price,
                    "band_width": current_width,
                    "shares": portfolio[symbol]["shares"],
                })

        # Generate signals
        signals = []

        # Sell signals for contracting volatility
        for candidate in contraction_candidates:
            signals.append({
                "action": "sell",
                "symbol": candidate["symbol"],
                "shares": candidate["shares"],
                "reason": f"volatility contraction (width: {candidate['band_width']:.3f})",
            })

        # Buy signals for expanding volatility
        current_positions = len(portfolio)
        available_slots = self.max_positions - current_positions

        if available_slots > 0:
            # Sort by band width (most expanding first)
            expansion_candidates.sort(key=lambda x: x["band_width"], reverse=True)

            # Buy top N most expanding
            for candidate in expansion_candidates[:available_slots]:
                current_price = candidate["price"]

                # Calculate position size
                position_value = initial_cash * self.position_size
                shares = int(position_value / current_price)

                if shares > 0:
                    signals.append({
                        "action": "buy",
                        "symbol": candidate["symbol"],
                        "shares": shares,
                        "reason": f"volatility expansion (width: {candidate['band_width']:.3f})",
                    })

        return signals

    def _calculate_bollinger_bands_series(
        self, price_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Calculate Bollinger Bands for a series of prices.

        Args:
            price_data: List of price records sorted by date

        Returns:
            List of dicts with close, sma, upper_band, lower_band, band_width
        """
        if len(price_data) < self.bb_period:
            return []

        prices = [row["close"] for row in price_data]
        bb_series = []

        # Calculate Bollinger Bands for each day (starting from bb_period)
        for i in range(self.bb_period - 1, len(prices)):
            window = prices[i - self.bb_period + 1:i + 1]

            # Calculate SMA (middle band)
            sma = sum(window) / len(window)

            # Calculate standard deviation
            variance = sum((x - sma) ** 2 for x in window) / len(window)
            std_dev = math.sqrt(variance)

            # Calculate upper and lower bands
            upper_band = sma + (self.bb_std_dev * std_dev)
            lower_band = sma - (self.bb_std_dev * std_dev)

            # Calculate band width (normalized by SMA)
            band_width = (upper_band - lower_band) / sma if sma > 0 else 0

            bb_series.append({
                "close": prices[i],
                "sma": sma,
                "upper_band": upper_band,
                "lower_band": lower_band,
                "std_dev": std_dev,
                "band_width": band_width,
            })

        return bb_series

    def _had_recent_squeeze(self, bb_data: List[Dict[str, Any]]) -> bool:
        """
        Check if there was a recent squeeze (low volatility period).

        Args:
            bb_data: List of Bollinger Band calculations

        Returns:
            True if recent squeeze detected
        """
        if len(bb_data) < 5:
            return False

        # Look back over last 10 periods (or all available if less)
        lookback = min(10, len(bb_data) - 1)
        recent_widths = [bb_data[i]["band_width"] for i in range(-lookback, 0)]

        # Check if any recent period had squeeze (width < threshold)
        for width in recent_widths:
            if width < self.squeeze_threshold:
                return True

        return False
