"""
Portfolio management for backtesting.

Tracks positions, cash, and equity over time.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List


class Portfolio:
    """
    Portfolio for tracking positions and cash in backtesting.

    Maintains:
    - Cash balance
    - Stock positions (symbol -> {shares, avg_price})
    - Transaction log
    - Equity calculations
    """

    def __init__(self, initial_cash: float):
        """
        Initialize portfolio with cash.

        Args:
            initial_cash: Starting cash amount
        """
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.positions: Dict[str, Dict[str, float]] = {}
        self.transactions: List[Dict[str, Any]] = []

    @property
    def equity(self) -> float:
        """
        Current equity (cash + position values at cost basis).

        Note: This uses cost basis. For mark-to-market equity,
        use calculate_equity() with current prices.
        """
        position_value = sum(
            pos["shares"] * pos["avg_price"] for pos in self.positions.values()
        )
        return self.cash + position_value

    def buy(self, symbol: str, shares: int, price: float, date: date) -> None:
        """
        Buy shares of a stock.

        Args:
            symbol: Stock symbol
            shares: Number of shares to buy
            price: Price per share
            date: Transaction date

        Raises:
            ValueError: If insufficient cash
        """
        cost = shares * price

        if cost > self.cash:
            raise ValueError(
                f"Insufficient cash. Need {cost:.2f}, have {self.cash:.2f}"
            )

        # Deduct cash
        self.cash -= cost

        # Update or create position
        if symbol in self.positions:
            # Update average price for existing position
            existing = self.positions[symbol]
            total_shares = existing["shares"] + shares
            total_cost = (existing["shares"] * existing["avg_price"]) + cost
            avg_price = total_cost / total_shares

            self.positions[symbol] = {"shares": total_shares, "avg_price": avg_price}
        else:
            # Create new position
            self.positions[symbol] = {"shares": shares, "avg_price": price}

        # Log transaction
        self.transactions.append(
            {
                "action": "buy",
                "symbol": symbol,
                "shares": shares,
                "price": price,
                "date": date,
                "value": cost,
            }
        )

    def sell(self, symbol: str, shares: int, price: float, date: date) -> None:
        """
        Sell shares of a stock.

        Args:
            symbol: Stock symbol
            shares: Number of shares to sell
            price: Price per share
            date: Transaction date

        Raises:
            ValueError: If insufficient shares
        """
        if symbol not in self.positions:
            raise ValueError(f"No position in {symbol}")

        position = self.positions[symbol]
        if shares > position["shares"]:
            raise ValueError(
                f"Insufficient shares. Want to sell {shares}, have {position['shares']}"
            )

        # Add cash from sale
        proceeds = shares * price
        self.cash += proceeds

        # Update position
        position["shares"] -= shares

        # Remove position if fully sold
        if position["shares"] == 0:
            del self.positions[symbol]

        # Log transaction
        self.transactions.append(
            {
                "action": "sell",
                "symbol": symbol,
                "shares": shares,
                "price": price,
                "date": date,
                "value": proceeds,
                "realized_pnl": proceeds - (shares * position["avg_price"]),
            }
        )

    def calculate_equity(self, current_prices: Dict[str, float]) -> float:
        """
        Calculate mark-to-market equity with current prices.

        Args:
            current_prices: Dict mapping symbol -> current price

        Returns:
            Total equity (cash + position values at current prices)
        """
        position_value = 0.0

        for symbol, position in self.positions.items():
            if symbol in current_prices:
                position_value += position["shares"] * current_prices[symbol]
            else:
                # Use cost basis if no current price available
                position_value += position["shares"] * position["avg_price"]

        return self.cash + position_value

    def get_position(self, symbol: str) -> Dict[str, float]:
        """
        Get position details for a symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with shares and avg_price, or empty dict if no position
        """
        return self.positions.get(symbol, {"shares": 0, "avg_price": 0.0})
