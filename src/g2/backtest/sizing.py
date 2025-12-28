"""
Position sizing strategies for backtesting.

Supports various methods: fixed dollar, fixed percent, Kelly criterion,
volatility targeting, and risk parity.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SizingMethod(Enum):
    """Position sizing method."""

    FIXED_DOLLAR = "fixed_dollar"  # Fixed dollar amount per position
    FIXED_PERCENT = "fixed_percent"  # Fixed percentage of portfolio
    KELLY = "kelly"  # Kelly criterion
    RISK_PARITY = "risk_parity"  # Equal risk contribution
    VOLATILITY_TARGET = "volatility_target"  # Target volatility


@dataclass
class PositionSizer:
    """
    Position sizing calculator.

    Determines how many shares to buy based on portfolio value,
    price, and sizing method.
    """

    method: SizingMethod = SizingMethod.FIXED_PERCENT
    fixed_dollar_amount: float = 10000.0  # For FIXED_DOLLAR
    fixed_percent: float = 0.10  # For FIXED_PERCENT (10%)
    kelly_fraction: float = 0.25  # Fraction of Kelly to use (quarter Kelly)
    target_volatility: float = 0.15  # For VOLATILITY_TARGET (15%)

    def calculate_shares(
        self,
        portfolio_value: float,
        price: float,
        symbol: str,
        volatility: Optional[float] = None,
        win_rate: Optional[float] = None,
        win_loss_ratio: Optional[float] = None,
        num_assets: Optional[int] = None,
    ) -> int:
        """
        Calculate number of shares to buy.

        Args:
            portfolio_value: Current portfolio value
            price: Price per share
            symbol: Stock symbol
            volatility: Stock volatility (for vol targeting/risk parity)
            win_rate: Historical win rate (for Kelly)
            win_loss_ratio: Average win / average loss (for Kelly)
            num_assets: Number of assets for risk parity

        Returns:
            Number of shares (whole number, rounded down)
        """
        if price <= 0:
            return 0

        if self.method == SizingMethod.FIXED_DOLLAR:
            return self._fixed_dollar_sizing(price)
        elif self.method == SizingMethod.FIXED_PERCENT:
            return self._fixed_percent_sizing(portfolio_value, price)
        elif self.method == SizingMethod.KELLY:
            return self._kelly_sizing(
                portfolio_value, price, win_rate, win_loss_ratio
            )
        elif self.method == SizingMethod.VOLATILITY_TARGET:
            return self._volatility_target_sizing(
                portfolio_value, price, volatility
            )
        elif self.method == SizingMethod.RISK_PARITY:
            return self._risk_parity_sizing(
                portfolio_value, price, volatility, num_assets
            )
        else:
            return 0

    def _fixed_dollar_sizing(self, price: float) -> int:
        """Calculate shares based on fixed dollar amount."""
        return int(self.fixed_dollar_amount / price)

    def _fixed_percent_sizing(
        self, portfolio_value: float, price: float
    ) -> int:
        """Calculate shares based on fixed percentage of portfolio."""
        if portfolio_value <= 0:
            return 0
        position_value = portfolio_value * self.fixed_percent
        return int(position_value / price)

    def _kelly_sizing(
        self,
        portfolio_value: float,
        price: float,
        win_rate: Optional[float],
        win_loss_ratio: Optional[float],
    ) -> int:
        """
        Calculate shares using Kelly criterion.

        Kelly % = (W * R - L) / R
        where W = win rate, L = loss rate (1-W), R = win/loss ratio

        Uses fractional Kelly for safety (default quarter Kelly).
        """
        if win_rate is None or win_loss_ratio is None:
            return 0

        if win_loss_ratio <= 0:
            return 0

        loss_rate = 1 - win_rate

        # Kelly percentage
        kelly_pct = (win_rate * win_loss_ratio - loss_rate) / win_loss_ratio

        if kelly_pct <= 0:
            # Negative edge - don't bet
            return 0

        # Apply fractional Kelly
        position_pct = kelly_pct * self.kelly_fraction
        position_value = portfolio_value * position_pct

        return int(position_value / price)

    def _volatility_target_sizing(
        self,
        portfolio_value: float,
        price: float,
        volatility: Optional[float],
    ) -> int:
        """
        Calculate shares to achieve target portfolio volatility.

        Position weight = target_vol / stock_vol
        """
        if volatility is None or volatility <= 0:
            return 0

        if portfolio_value <= 0:
            return 0

        # Position weight to achieve target vol
        position_weight = self.target_volatility / volatility
        position_value = portfolio_value * position_weight

        return int(position_value / price)

    def _risk_parity_sizing(
        self,
        portfolio_value: float,
        price: float,
        volatility: Optional[float],
        num_assets: Optional[int],
    ) -> int:
        """
        Calculate shares for equal risk contribution.

        Each asset contributes equal volatility to portfolio.
        Risk per asset = 1 / num_assets
        Position weight = (1/num_assets) / stock_vol
        """
        if volatility is None or volatility <= 0:
            return 0

        if portfolio_value <= 0:
            return 0

        n = num_assets if num_assets and num_assets > 0 else 1

        # Target risk per asset
        risk_per_asset = 1.0 / n

        # Position weight for this volatility
        position_weight = risk_per_asset / volatility

        # Cap at 100% to avoid excessive leverage
        position_weight = min(position_weight, 1.0)

        position_value = portfolio_value * position_weight

        return int(position_value / price)
