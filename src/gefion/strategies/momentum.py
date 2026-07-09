"""
Momentum trading strategy.

Buys stocks with highest momentum (price appreciation) and rebalances periodically.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional


def calculate_momentum(
    price_history: List[Dict[str, Any]], lookback_days: int
) -> Optional[float]:
    """
    Calculate momentum as percent return over lookback period.

    Args:
        price_history: List of price records with date and close
        lookback_days: Number of days to look back

    Returns:
        Momentum as decimal (e.g., 0.20 for 20% gain), or None if insufficient data
    """
    if not price_history or len(price_history) < 2:
        return None

    # Sort by date
    sorted_prices = sorted(price_history, key=lambda x: x["date"])

    # Use available data up to lookback_days
    # If we have less than lookback_days, use what we have (min 2 data points)
    window_size = min(len(sorted_prices), lookback_days)

    if window_size < 2:
        return None

    # Get first and last prices in lookback window
    start_idx = max(0, len(sorted_prices) - window_size)
    start_price = sorted_prices[start_idx]["close"]
    end_price = sorted_prices[-1]["close"]

    if start_price == 0:
        return None

    # Calculate percent return
    momentum = (end_price - start_price) / start_price

    return momentum


def rank_stocks_by_momentum(
    stock_momentums: Dict[str, float], top_n: int
) -> List[str]:
    """
    Rank stocks by momentum and return top N.

    Args:
        stock_momentums: Dict mapping symbol -> momentum value
        top_n: Number of top stocks to return

    Returns:
        List of symbols, sorted by momentum (highest first)
    """
    # Sort by momentum (descending)
    sorted_stocks = sorted(
        stock_momentums.items(),
        key=lambda x: x[1],
        reverse=True
    )

    # Return top N symbols
    return [symbol for symbol, _ in sorted_stocks[:top_n]]


class MomentumStrategy:
    """
    Simple momentum trading strategy.

    Buys top N stocks by momentum and rebalances periodically.

    Strategy:
    1. Calculate momentum for all stocks over lookback period
    2. Select top N stocks with highest momentum
    3. Allocate capital equally across selected stocks
    4. Rebalance every rebalance_days
    """

    def __init__(
        self,
        lookback_days: int = 20,
        top_n: int = 5,
        rebalance_days: int = 5,
        allocation_pct: float = 0.90,
        mode: str = "long_only",
    ):
        """
        Initialize momentum strategy.

        Args:
            lookback_days: Days to calculate momentum over
            top_n: Number of top momentum stocks to hold
            rebalance_days: Days between rebalancing
            allocation_pct: Percentage of capital to allocate (0-1)
            mode: 'long_only' (default) or 'long_short' — in long_short the
                  bottom-N by momentum (the losers) are shorted (spec 009)
        """
        self.lookback_days = lookback_days
        self.top_n = top_n
        self.rebalance_days = rebalance_days
        self.allocation_pct = allocation_pct
        self.mode = mode

        # Track last rebalance date
        self.last_rebalance_date: Optional[date] = None

    def generate_signals(
        self,
        current_date: date,
        portfolio: Any,
        price_data: Dict[str, List[Dict[str, Any]]],
        initial_cash: float,
    ) -> List[Dict[str, Any]]:
        """
        Generate buy/sell signals for current date.

        Args:
            current_date: Current date in backtest
            portfolio: Portfolio object with positions and cash
            price_data: Dict mapping symbol -> list of historical prices
            initial_cash: Initial capital

        Returns:
            List of signal dicts with {action, symbol, shares}
        """
        # Check if we should rebalance
        if not self._should_rebalance(current_date):
            return []

        # Calculate momentum for all stocks
        stock_momentums = {}
        stock_losers = {}                 # negative momentum (long_short only)
        current_prices = {}

        for symbol, history in price_data.items():
            # Get prices up to current date
            relevant_history = [
                p for p in history
                if p["date"] <= current_date
            ]

            if not relevant_history:
                continue

            # Get current price
            current_price = relevant_history[-1]["close"]
            current_prices[symbol] = current_price

            # Calculate momentum
            momentum = calculate_momentum(relevant_history, self.lookback_days)

            if momentum is not None:
                if momentum > 0:
                    stock_momentums[symbol] = momentum
                elif momentum < 0:
                    stock_losers[symbol] = momentum

        if not stock_momentums and not (
                self.mode == "long_short" and stock_losers):
            return []

        signals = []
        total_allocation = initial_cash * self.allocation_pct

        # Buy the winners — long_only sizing UNCHANGED (per-winner allocation).
        top_stocks = rank_stocks_by_momentum(stock_momentums, self.top_n) \
            if stock_momentums else []
        if top_stocks:
            position_size = total_allocation / len(top_stocks)
            for symbol in top_stocks:
                price = current_prices.get(symbol)
                if price:
                    shares = int(position_size / price)
                    if shares > 0:
                        signals.append({"action": "buy", "symbol": symbol,
                                        "shares": shares})

        # Short the losers (long_short only) — sized by top_n so shorts are
        # funded even with no winners; never affects the long_only path.
        if self.mode == "long_short" and stock_losers:
            short_size = total_allocation / self.top_n
            worst = sorted(stock_losers.items(), key=lambda kv: kv[1])[:self.top_n]
            for symbol, _mom in worst:
                price = current_prices.get(symbol)
                if price:
                    shares = int(short_size / price)
                    if shares > 0:
                        signals.append({"action": "short", "symbol": symbol,
                                        "shares": shares})

        # Update last rebalance date
        self.last_rebalance_date = current_date

        return signals

    def _should_rebalance(self, current_date: date) -> bool:
        """
        Check if we should rebalance on current date.

        Args:
            current_date: Current date

        Returns:
            True if should rebalance, False otherwise
        """
        # First time - always rebalance
        if self.last_rebalance_date is None:
            return True

        # Check if enough days have passed
        days_since_rebalance = (current_date - self.last_rebalance_date).days

        return days_since_rebalance >= self.rebalance_days
