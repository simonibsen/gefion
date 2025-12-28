"""
Slippage models for realistic price execution.

Models the difference between expected and actual execution price.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import math


class OrderType(Enum):
    """Order type for execution."""
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class SlippageModel:
    """
    Slippage model for realistic price execution.

    Models adverse price movement during order execution.
    Buy orders execute at higher prices, sell orders at lower prices.

    Components:
    - Fixed slippage: Constant percentage
    - Volume-based: Increases with order size relative to volume
    - Volatility-based: Scales with market volatility

    All components are optional and default to 0 (no slippage).
    """
    # Fixed slippage
    fixed_slippage_pct: float = 0.0          # Always apply this % slippage

    # Volume-based slippage
    volume_slippage_coefficient: float = 0.0  # Slippage increases with order size

    # Volatility-based slippage
    volatility_slippage_coefficient: float = 0.0  # Slippage scales with volatility

    def calculate_execution_price(
        self,
        order_price: float,
        shares: int,
        action: str,  # "buy" or "sell"
        order_type: OrderType = OrderType.MARKET,
        daily_volume: Optional[int] = None,
        volatility: Optional[float] = None,  # Daily return std dev
        limit_price: Optional[float] = None,
    ) -> Optional[float]:
        """
        Calculate actual execution price after slippage.

        For limit orders, returns None if order would not fill.

        Args:
            order_price: Current market price
            shares: Number of shares to trade
            action: "buy" or "sell"
            order_type: MARKET or LIMIT
            daily_volume: Optional daily volume for volume-based slippage
            volatility: Optional volatility for volatility-based slippage
            limit_price: Limit price for limit orders

        Returns:
            Execution price, or None if limit order wouldn't fill
        """
        # Handle limit orders
        if order_type == OrderType.LIMIT and limit_price is not None:
            if action == "buy" and order_price > limit_price:
                return None  # Price moved above limit, no fill
            if action == "sell" and order_price < limit_price:
                return None  # Price moved below limit, no fill
            # Limit orders execute at limit price (best case)
            return limit_price

        # Market order slippage calculation
        slippage_pct = self.fixed_slippage_pct

        # Volume-based component
        if (daily_volume and daily_volume > 0 and
            self.volume_slippage_coefficient > 0 and shares > 0):
            participation = shares / daily_volume
            slippage_pct += math.sqrt(participation) * self.volume_slippage_coefficient

        # Volatility-based component
        if volatility and volatility > 0 and self.volatility_slippage_coefficient > 0:
            slippage_pct += volatility * self.volatility_slippage_coefficient

        # Apply slippage in adverse direction
        if action == "buy":
            execution_price = order_price * (1 + slippage_pct)
        else:
            execution_price = order_price * (1 - slippage_pct)

        return execution_price


# =============================================================================
# Preset Configurations
# =============================================================================

# Zero slippage - exact price execution
ZERO_SLIPPAGE = SlippageModel()

# Realistic slippage for liquid stocks
REALISTIC_SLIPPAGE = SlippageModel(
    fixed_slippage_pct=0.0001,           # 1 bp base slippage
    volume_slippage_coefficient=0.001,    # Increases with order size
    volatility_slippage_coefficient=0.1,  # Scales with volatility
)
