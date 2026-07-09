"""
Pairs Trading Strategy.

Statistical arbitrage strategy that trades cointegrated pairs of stocks.
Enters long-short positions when spread deviates from mean, exits when it reverts.

Strategy Logic:
1. Identify cointegrated pairs (stocks that move together long-term)
2. Calculate spread (difference between normalized prices)
3. Compute z-score of spread (how many std devs from mean)
4. Enter positions when |z-score| > entry_zscore threshold
   - If z-score > threshold: spread is high → short stock1, long stock2
   - If z-score < -threshold: spread is low → long stock1, short stock2
5. Exit positions when |z-score| < exit_zscore threshold

Parameters:
- lookback_days: Period for cointegration test and spread stats (default: 60)
- entry_zscore: Z-score threshold for entering pairs trade (default: 2.0)
- exit_zscore: Z-score threshold for exiting pairs trade (default: 0.5)
- position_size: Fraction of portfolio per pair (default: 0.2 = 20%)
- max_pairs: Maximum number of concurrent pairs (default: 3)

Example Usage:
    strategy = PairsTradingStrategy(
        lookback_days=60,
        entry_zscore=2.0,
        exit_zscore=0.5,
        position_size=0.15,
        max_pairs=3,
    )

    signals = strategy.generate_signals(
        current_date=date(2024, 12, 1),
        portfolio={"AAPL": {"shares": -100, "avg_price": 150.0}, "MSFT": {"shares": 50, "avg_price": 300.0}},
        price_data=price_data,
        initial_cash=100000.0,
    )
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Tuple
import math


class PairsTradingStrategy:
    """
    Pairs trading strategy using spread mean reversion.

    Trades cointegrated pairs when spread deviates significantly from historical mean.
    """

    def __init__(
        self,
        lookback_days: int = 60,
        entry_zscore: float = 2.0,
        exit_zscore: float = 0.5,
        position_size: float = 0.2,
        max_pairs: int = 3,
        mode: str = "long_only",
    ):
        """
        Initialize pairs trading strategy.

        Args:
            lookback_days: Period for cointegration test and spread statistics (default: 60)
            entry_zscore: Z-score threshold for entering pairs trade (default: 2.0)
            exit_zscore: Z-score threshold for exiting pairs trade (default: 0.5)
            position_size: Fraction of portfolio per pair (default: 0.2)
            max_pairs: Maximum concurrent pairs (default: 3)
        """
        self.lookback_days = lookback_days
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.position_size = position_size
        self.max_pairs = max_pairs
        # spec 009: in long_short the short leg is a real short (short/cover);
        # in long_only it falls back to the legacy sell-to-open (held-only).
        self.mode = mode

    def generate_signals(
        self,
        current_date: date,
        portfolio: Dict[str, Dict[str, Any]],
        price_data: List[Dict[str, Any]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate buy/sell signals for pairs trading.

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

        # Need at least 2 symbols to form pairs
        if len(symbols) < 2:
            return []

        # Organize data by symbol
        symbol_data = {}
        for symbol in symbols:
            data = [row for row in price_data if row["symbol"] == symbol]
            data = sorted(data, key=lambda x: x["date"])

            # Need sufficient history
            if len(data) < self.lookback_days + 1:
                continue

            symbol_data[symbol] = data[-(self.lookback_days + 1):]

        # Find cointegrated pairs and calculate spreads
        pairs_info = self._find_tradeable_pairs(symbol_data)

        if not pairs_info:
            return []

        # Generate signals
        signals = []

        # Check existing pair positions for exit signals
        current_pairs = self._get_current_pairs(portfolio)
        for pair_key, (symbol1, symbol2) in current_pairs.items():
            # Find spread info for this pair
            pair_spread = None
            for info in pairs_info:
                if (info["symbol1"] == symbol1 and info["symbol2"] == symbol2) or \
                   (info["symbol1"] == symbol2 and info["symbol2"] == symbol1):
                    pair_spread = info
                    break

            if pair_spread and abs(pair_spread["zscore"]) < self.exit_zscore:
                # Exit: close both legs
                if symbol1 in portfolio:
                    shares = portfolio[symbol1]["shares"]
                    if shares != 0:
                        signals.append({
                            "action": "sell" if shares > 0 else "cover",
                            "symbol": symbol1,
                            "shares": abs(shares),
                            "reason": f"exit pair (z-score: {pair_spread['zscore']:.2f})",
                        })

                if symbol2 in portfolio:
                    shares = portfolio[symbol2]["shares"]
                    if shares != 0:
                        signals.append({
                            "action": "sell" if shares > 0 else "cover",
                            "symbol": symbol2,
                            "shares": abs(shares),
                            "reason": f"exit pair (z-score: {pair_spread['zscore']:.2f})",
                        })

        # Entry signals for new pairs
        current_pair_count = len(current_pairs)
        available_slots = self.max_pairs - current_pair_count

        if available_slots > 0:
            # Sort by absolute z-score (most extreme first)
            pairs_info.sort(key=lambda x: abs(x["zscore"]), reverse=True)

            # Enter new pairs
            for info in pairs_info[:available_slots]:
                if abs(info["zscore"]) < self.entry_zscore:
                    continue

                # Check if we're already trading this pair
                pair_key = tuple(sorted([info["symbol1"], info["symbol2"]]))
                if pair_key in current_pairs:
                    continue

                # Calculate position sizes for both legs
                symbol1 = info["symbol1"]
                symbol2 = info["symbol2"]
                price1 = info["price1"]
                price2 = info["price2"]
                hedge_ratio = info["hedge_ratio"]

                # Allocate position_size of capital to this pair
                pair_capital = initial_cash * self.position_size

                # Determine direction based on z-score
                # Positive z-score: spread is high → short symbol1, long symbol2
                # Negative z-score: spread is low → long symbol1, short symbol2
                if info["zscore"] > 0:
                    # Spread is high: short overvalued (symbol1), long undervalued (symbol2)
                    # Size positions to be market-neutral using hedge ratio
                    shares2 = int((pair_capital / 2) / price2)
                    shares1 = int(shares2 * hedge_ratio)

                    if shares1 > 0 and shares2 > 0:
                        # Short symbol1 (a real short in long_short; legacy
                        # sell-to-open in long_only)
                        signals.append({
                            "action": "short" if self.mode == "long_short" else "sell",
                            "symbol": symbol1,
                            "shares": shares1,
                            "reason": f"enter pair short (z-score: {info['zscore']:.2f})",
                        })
                        # Long symbol2
                        signals.append({
                            "action": "buy",
                            "symbol": symbol2,
                            "shares": shares2,
                            "reason": f"enter pair long (z-score: {info['zscore']:.2f})",
                        })
                else:
                    # Spread is low: long undervalued (symbol1), short overvalued (symbol2)
                    shares1 = int((pair_capital / 2) / price1)
                    shares2 = int(shares1 * hedge_ratio)

                    if shares1 > 0 and shares2 > 0:
                        # Long symbol1
                        signals.append({
                            "action": "buy",
                            "symbol": symbol1,
                            "shares": shares1,
                            "reason": f"enter pair long (z-score: {info['zscore']:.2f})",
                        })
                        # Short symbol2 (real short in long_short)
                        signals.append({
                            "action": "short" if self.mode == "long_short" else "sell",
                            "symbol": symbol2,
                            "shares": shares2,
                            "reason": f"enter pair short (z-score: {info['zscore']:.2f})",
                        })

        return signals

    def _find_tradeable_pairs(
        self, symbol_data: Dict[str, List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """
        Find cointegrated pairs and calculate current spread z-scores.

        Args:
            symbol_data: Price data organized by symbol

        Returns:
            List of pair info dicts with spread statistics
        """
        pairs_info = []
        symbols = list(symbol_data.keys())

        # Check all possible pairs
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                symbol1 = symbols[i]
                symbol2 = symbols[j]

                data1 = symbol_data[symbol1]
                data2 = symbol_data[symbol2]

                # Both must have same dates
                if len(data1) != len(data2):
                    continue

                # Extract prices
                prices1 = [row["close"] for row in data1]
                prices2 = [row["close"] for row in data2]

                # Test for cointegration (simplified: use correlation)
                correlation = self._calculate_correlation(prices1, prices2)

                # Require high correlation (> 0.7) as proxy for cointegration
                if abs(correlation) < 0.7:
                    continue

                # Calculate hedge ratio (slope of linear relationship)
                hedge_ratio = self._calculate_hedge_ratio(prices1, prices2)

                # Calculate spread
                spreads = []
                for p1, p2 in zip(prices1, prices2):
                    spread = p1 - (hedge_ratio * p2)
                    spreads.append(spread)

                # Calculate spread statistics
                mean_spread = sum(spreads) / len(spreads)
                std_spread = self._calculate_std(spreads, mean_spread)

                if std_spread == 0:
                    continue

                # Current spread and z-score
                current_spread = spreads[-1]
                zscore = (current_spread - mean_spread) / std_spread

                pairs_info.append({
                    "symbol1": symbol1,
                    "symbol2": symbol2,
                    "hedge_ratio": hedge_ratio,
                    "correlation": correlation,
                    "zscore": zscore,
                    "spread": current_spread,
                    "price1": prices1[-1],
                    "price2": prices2[-1],
                })

        return pairs_info

    def _get_current_pairs(
        self, portfolio: Dict[str, Dict[str, Any]]
    ) -> Dict[Tuple[str, str], Tuple[str, str]]:
        """
        Identify which pairs are currently being traded.

        Args:
            portfolio: Current holdings

        Returns:
            Dict mapping pair_key to (symbol1, symbol2)
        """
        pairs = {}

        # Look for symbols held in opposite directions (long + short)
        symbols = list(portfolio.keys())

        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                symbol1 = symbols[i]
                symbol2 = symbols[j]
                shares1 = portfolio[symbol1]["shares"]
                shares2 = portfolio[symbol2]["shares"]

                # Check if opposite positions (one long, one short)
                if (shares1 > 0 and shares2 < 0) or (shares1 < 0 and shares2 > 0):
                    pair_key = tuple(sorted([symbol1, symbol2]))
                    pairs[pair_key] = (symbol1, symbol2)

        return pairs

    def _calculate_correlation(self, x: List[float], y: List[float]) -> float:
        """Calculate Pearson correlation coefficient."""
        if len(x) != len(y) or len(x) == 0:
            return 0.0

        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        sum_sq_x = sum((x[i] - mean_x) ** 2 for i in range(n))
        sum_sq_y = sum((y[i] - mean_y) ** 2 for i in range(n))

        if sum_sq_x == 0 or sum_sq_y == 0:
            return 0.0

        denominator = math.sqrt(sum_sq_x * sum_sq_y)
        return numerator / denominator if denominator != 0 else 0.0

    def _calculate_hedge_ratio(self, y: List[float], x: List[float]) -> float:
        """
        Calculate hedge ratio (slope) for linear relationship y = beta * x.

        Args:
            y: Dependent variable (e.g., stock1 prices)
            x: Independent variable (e.g., stock2 prices)

        Returns:
            Hedge ratio (beta coefficient)
        """
        if len(x) != len(y) or len(x) == 0:
            return 1.0

        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        denominator = sum((x[i] - mean_x) ** 2 for i in range(n))

        return numerator / denominator if denominator != 0 else 1.0

    def _calculate_std(self, values: List[float], mean: float) -> float:
        """Calculate standard deviation."""
        if len(values) == 0:
            return 0.0

        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)
