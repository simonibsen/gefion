"""
Portfolio management for backtesting.

Tracks positions, cash, and equity over time.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from g2.backtest.costs import TransactionCosts


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

    def buy(
        self,
        symbol: str,
        shares: int,
        price: float,
        date: date,
        costs: Optional["TransactionCosts"] = None,
        daily_volume: Optional[int] = None,
    ) -> None:
        """
        Buy shares of a stock.

        Args:
            symbol: Stock symbol
            shares: Number of shares to buy
            price: Price per share
            date: Transaction date
            costs: Optional transaction cost model
            daily_volume: Optional daily volume for market impact calculation

        Raises:
            ValueError: If insufficient cash
        """
        base_cost = shares * price

        # Calculate transaction costs if provided
        tx_cost = 0.0
        if costs is not None:
            tx_cost = costs.calculate_cost(shares, price, "buy", daily_volume)

        total_cost = base_cost + tx_cost

        if total_cost > self.cash:
            raise ValueError(
                f"Insufficient cash. Need {total_cost:.2f}, have {self.cash:.2f}"
            )

        # Deduct cash (base cost + transaction costs)
        self.cash -= total_cost

        # Update or create position
        if symbol in self.positions:
            # Update average price for existing position
            existing = self.positions[symbol]
            total_shares = existing["shares"] + shares
            position_cost = (existing["shares"] * existing["avg_price"]) + base_cost
            avg_price = position_cost / total_shares

            self.positions[symbol] = {"shares": total_shares, "avg_price": avg_price}
        else:
            # Create new position
            self.positions[symbol] = {"shares": shares, "avg_price": price}

        # Log transaction
        tx_record = {
            "action": "buy",
            "symbol": symbol,
            "shares": shares,
            "price": price,
            "date": date,
            "value": base_cost,
        }
        if tx_cost > 0:
            tx_record["cost"] = tx_cost
        self.transactions.append(tx_record)

    def sell(
        self,
        symbol: str,
        shares: int,
        price: float,
        date: date,
        costs: Optional["TransactionCosts"] = None,
        daily_volume: Optional[int] = None,
    ) -> None:
        """
        Sell shares of a stock.

        Args:
            symbol: Stock symbol
            shares: Number of shares to sell
            price: Price per share
            date: Transaction date
            costs: Optional transaction cost model
            daily_volume: Optional daily volume for market impact calculation

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

        # Calculate proceeds and transaction costs
        gross_proceeds = shares * price

        tx_cost = 0.0
        if costs is not None:
            tx_cost = costs.calculate_cost(shares, price, "sell", daily_volume)

        net_proceeds = gross_proceeds - tx_cost

        # Add net proceeds to cash
        self.cash += net_proceeds

        # Store avg_price before updating position
        avg_price = position["avg_price"]

        # Update position
        position["shares"] -= shares

        # Remove position if fully sold
        if position["shares"] == 0:
            del self.positions[symbol]

        # Log transaction
        tx_record = {
            "action": "sell",
            "symbol": symbol,
            "shares": shares,
            "price": price,
            "date": date,
            "value": gross_proceeds,
            "realized_pnl": gross_proceeds - (shares * avg_price),
        }
        if tx_cost > 0:
            tx_record["cost"] = tx_cost
        self.transactions.append(tx_record)

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
