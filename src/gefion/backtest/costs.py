"""
Transaction cost models for realistic backtesting.

All costs are composable - set any component to 0 to disable it.
"""
from dataclasses import dataclass
from typing import Optional
import math


@dataclass
class TransactionCosts:
    """
    Transaction cost model for realistic backtesting.

    Components:
    - Commission: Fixed per trade + per share
    - Spread: Bid-ask spread as fraction of trade value
    - Market impact: Price impact for larger orders

    All costs are optional and default to 0 (no cost).
    """
    # Commission costs
    commission_per_trade: float = 0.0        # Fixed $ per trade
    commission_per_share: float = 0.0        # $ per share
    commission_min: float = 0.0              # Minimum commission per trade
    commission_max: Optional[float] = None   # Maximum commission per trade

    # Spread costs
    bid_ask_spread_pct: float = 0.0          # Half-spread as fraction (0.001 = 10 bps)

    # Market impact (for large orders)
    market_impact_coefficient: float = 0.0   # sqrt(participation) * coefficient

    def calculate_cost(
        self,
        shares: int,
        price: float,
        action: str,  # "buy" or "sell"
        daily_volume: Optional[int] = None,
    ) -> float:
        """
        Calculate total transaction cost for a trade.

        Args:
            shares: Number of shares to trade
            price: Price per share
            action: "buy" or "sell"
            daily_volume: Optional daily volume for market impact calc

        Returns:
            Total cost in dollars (always positive)
        """
        cost = 0.0

        # Calculate commission
        commission = self.commission_per_trade + (self.commission_per_share * shares)
        commission = max(commission, self.commission_min)
        if self.commission_max is not None:
            commission = min(commission, self.commission_max)
        cost += commission

        # Calculate spread cost (half-spread applied to trade value)
        trade_value = shares * price
        cost += trade_value * self.bid_ask_spread_pct

        # Calculate market impact (sqrt of participation rate)
        if (daily_volume and daily_volume > 0 and
            self.market_impact_coefficient > 0 and shares > 0):
            participation_rate = shares / daily_volume
            impact = (math.sqrt(participation_rate) *
                     self.market_impact_coefficient * trade_value)
            cost += impact

        return cost


# =============================================================================
# Preset Configurations
# =============================================================================

# Zero costs - use for testing or when costs don't matter
ZERO_COSTS = TransactionCosts()

# Retail investor costs (commission-free brokers)
RETAIL_COSTS = TransactionCosts(
    commission_per_trade=0.0,       # Commission-free brokers
    bid_ask_spread_pct=0.0005,      # 5 bps half-spread for liquid stocks
)

# Institutional costs (per-share pricing with market impact)
INSTITUTIONAL_COSTS = TransactionCosts(
    commission_per_share=0.005,     # $0.005/share
    commission_min=1.0,             # Minimum $1 per trade
    bid_ask_spread_pct=0.0002,      # 2 bps for institutional execution
    market_impact_coefficient=0.1,  # Some market impact on larger orders
)
