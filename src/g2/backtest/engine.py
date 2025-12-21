"""
Backtesting engine for strategy validation.

Provides point-in-time correct backtesting with no look-ahead bias.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Callable, Dict, List

from g2.backtest.portfolio import Portfolio


class BacktestEngine:
    """
    Simple backtesting engine with point-in-time correctness.

    Ensures strategy only has access to past data (no look-ahead bias).
    """

    def __init__(
        self,
        price_data: List[Dict[str, Any]],
        strategy: Callable,
        initial_cash: float,
        start_date: date,
        end_date: date,
    ):
        """
        Initialize backtesting engine.

        Args:
            price_data: List of price records with symbol, date, close
            strategy: Callable(date, portfolio, prices) -> List[signals]
                      Returns list of dicts with {action, symbol, shares}
            initial_cash: Starting cash amount
            start_date: Start date for backtest
            end_date: End date for backtest
        """
        self.price_data = price_data
        self.strategy = strategy
        self.initial_cash = initial_cash
        self.start_date = start_date
        self.end_date = end_date

        # Pre-process price data into efficient lookups
        self._prices_by_date = self._index_prices_by_date()
        self._trading_dates = sorted(
            [d for d in self._prices_by_date.keys() if start_date <= d <= end_date]
        )

    def _index_prices_by_date(self) -> Dict[date, Dict[str, float]]:
        """
        Index price data by date for efficient lookups.

        Returns:
            Dict mapping date -> {symbol: price}
        """
        prices_by_date = defaultdict(dict)

        for row in self.price_data:
            row_date = row["date"]
            symbol = row["symbol"]
            close = float(row["close"])
            prices_by_date[row_date][symbol] = close

        return dict(prices_by_date)

    def _get_historical_prices(self, current_date: date) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get historical prices up to (and including) current date.

        This ensures point-in-time correctness - strategy only sees past data.

        Args:
            current_date: Current date in backtest

        Returns:
            Dict mapping symbol -> list of historical price records
        """
        historical = defaultdict(list)

        for row in self.price_data:
            if row["date"] <= current_date:
                historical[row["symbol"]].append(row)

        return dict(historical)

    def run(self) -> Dict[str, Any]:
        """
        Run backtest and return results.

        Returns:
            Dict with:
                - trades: List of executed trades
                - equity_curve: List of {date, equity} points
                - metrics: Performance metrics
        """
        portfolio = Portfolio(initial_cash=self.initial_cash)
        trades = []
        equity_curve = []

        for current_date in self._trading_dates:
            # Get current prices for this date
            current_prices = self._prices_by_date.get(current_date, {})

            # Get historical data (point-in-time correct)
            historical_prices = self._get_historical_prices(current_date)

            # Call strategy to get signals
            signals = self.strategy(current_date, portfolio, historical_prices)

            # Execute signals
            for signal in signals:
                action = signal["action"]
                symbol = signal["symbol"]
                shares = signal["shares"]

                # Get price for this symbol on current date
                if symbol not in current_prices:
                    # Skip if no price data available
                    continue

                price = current_prices[symbol]

                try:
                    if action == "buy":
                        portfolio.buy(
                            symbol=symbol, shares=shares, price=price, date=current_date
                        )
                        trades.append(
                            {
                                "date": current_date,
                                "action": "buy",
                                "symbol": symbol,
                                "shares": shares,
                                "price": price,
                            }
                        )
                    elif action == "sell":
                        portfolio.sell(
                            symbol=symbol, shares=shares, price=price, date=current_date
                        )
                        trades.append(
                            {
                                "date": current_date,
                                "action": "sell",
                                "symbol": symbol,
                                "shares": shares,
                                "price": price,
                            }
                        )
                except ValueError as e:
                    # Log but don't fail - insufficient cash/shares is expected
                    pass

            # Record equity for this date
            equity = portfolio.calculate_equity(current_prices)
            equity_curve.append({"date": current_date, "equity": equity})

        # Calculate metrics
        from g2.backtest.metrics import calculate_metrics

        metrics = calculate_metrics(equity_curve, initial_capital=self.initial_cash)

        return {"trades": trades, "equity_curve": equity_curve, "metrics": metrics}
