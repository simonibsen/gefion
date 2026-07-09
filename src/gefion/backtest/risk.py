"""
Risk management for backtesting.

Provides stop loss, take profit, position limits, and drawdown controls.
All limits are optional and composable.
"""
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from gefion.backtest.portfolio import Portfolio


class RiskAction(Enum):
    """Actions that risk manager can recommend."""

    HOLD = "hold"  # Keep position, allow trade
    EXIT = "exit"  # Exit position
    BLOCK = "block"  # Block new position


@dataclass
class RiskLimits:
    """
    Risk limit configuration.

    All limits are optional. Set to None to disable.
    """

    # Position-level limits
    stop_loss_pct: Optional[float] = None  # Exit if loss exceeds this %
    take_profit_pct: Optional[float] = None  # Exit if gain exceeds this %

    # Portfolio-level limits
    max_position_pct: Optional[float] = None  # Max % of portfolio per position
    max_positions: Optional[int] = None  # Max number of positions
    max_portfolio_drawdown: Optional[float] = None  # Max drawdown from peak

    # Short-side limits (spec 009)
    max_short_exposure: Optional[float] = None  # Max Σ|short notional| / equity
    max_gross_exposure: Optional[float] = None  # Max Σ|all notional| / equity
    initial_margin: Optional[float] = None      # Reg-T initial (e.g. 0.50)
    maintenance_margin: Optional[float] = None  # Reg-T maintenance (e.g. 0.25)


class RiskManager:
    """
    Risk manager for controlling position and portfolio risk.

    Provides:
    - Position checks (stop loss, take profit)
    - Portfolio checks (max positions, max position size, drawdown)
    - Signal filtering (remove blocked signals)
    - Exit signal generation (for positions hitting limits)
    """

    def __init__(self, limits: RiskLimits):
        """
        Initialize risk manager with limits.

        Args:
            limits: Risk limit configuration
        """
        self.limits = limits

    def check_position(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        shares: int,
    ) -> RiskAction:
        """
        Check if a position should be exited based on price movement.

        Args:
            symbol: Stock symbol
            entry_price: Entry price for position
            current_price: Current market price
            shares: Number of shares

        Returns:
            RiskAction.EXIT if position should be closed, else HOLD
        """
        if entry_price <= 0:
            return RiskAction.HOLD

        pct_change = (current_price - entry_price) / entry_price
        is_short = shares < 0

        if is_short:
            # A short LOSES when the price rises, GAINS when it falls — the
            # mirror of a long.
            if (self.limits.stop_loss_pct is not None
                    and pct_change > self.limits.stop_loss_pct):
                return RiskAction.EXIT
            if (self.limits.take_profit_pct is not None
                    and pct_change < -self.limits.take_profit_pct):
                return RiskAction.EXIT
        else:
            if (self.limits.stop_loss_pct is not None
                    and pct_change < -self.limits.stop_loss_pct):
                return RiskAction.EXIT
            if (self.limits.take_profit_pct is not None
                    and pct_change > self.limits.take_profit_pct):
                return RiskAction.EXIT

        return RiskAction.HOLD

    def check_portfolio(
        self,
        portfolio_value: float,
        current_positions: int,
        proposed_position_value: float,
    ) -> RiskAction:
        """
        Check if a new position is allowed based on portfolio limits.

        Args:
            portfolio_value: Total portfolio value
            current_positions: Number of existing positions
            proposed_position_value: Value of proposed new position

        Returns:
            RiskAction.BLOCK if position should be blocked, else HOLD
        """
        # Check max positions
        if self.limits.max_positions is not None:
            if current_positions >= self.limits.max_positions:
                return RiskAction.BLOCK

        # Check max position size
        if self.limits.max_position_pct is not None and portfolio_value > 0:
            position_pct = proposed_position_value / portfolio_value
            if position_pct > self.limits.max_position_pct:
                return RiskAction.BLOCK

        return RiskAction.HOLD

    def check_drawdown(
        self,
        current_equity: float,
        peak_equity: float,
    ) -> RiskAction:
        """
        Check if portfolio drawdown exceeds limit.

        Args:
            current_equity: Current portfolio equity
            peak_equity: Peak portfolio equity

        Returns:
            RiskAction.BLOCK if drawdown exceeds limit, else HOLD
        """
        if self.limits.max_portfolio_drawdown is None:
            return RiskAction.HOLD

        if peak_equity <= 0:
            return RiskAction.HOLD

        drawdown = (peak_equity - current_equity) / peak_equity

        if drawdown > self.limits.max_portfolio_drawdown:
            return RiskAction.BLOCK

        return RiskAction.HOLD

    def filter_signals(
        self,
        signals: List[Dict[str, Any]],
        portfolio: "Portfolio",
        prices: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """
        Filter signals based on risk rules.

        Removes buy signals that would violate portfolio limits.

        Args:
            signals: List of trading signals
            portfolio: Current portfolio
            prices: Current prices

        Returns:
            Filtered list of signals
        """
        filtered = []
        current_positions = len(portfolio.positions)
        portfolio_value = portfolio.calculate_equity(prices)

        for signal in signals:
            act = signal.get("action")
            symbol = signal.get("symbol")
            shares = signal.get("shares", 0)
            price = prices.get(symbol, 0)
            proposed_value = shares * price

            if act == "short":
                # Short exposure / gross exposure guardrails (spec 009).
                if self._short_blocked(portfolio, prices, portfolio_value,
                                       proposed_value):
                    continue
                filtered.append(signal)
                current_positions += 1
                continue

            if act != "buy":
                # Keep sell/cover signals as-is
                filtered.append(signal)
                continue

            # Check portfolio limits (long)
            action = self.check_portfolio(
                portfolio_value=portfolio_value,
                current_positions=current_positions,
                proposed_position_value=proposed_value,
            )
            if action == RiskAction.BLOCK:
                continue  # Skip blocked signal

            filtered.append(signal)
            current_positions += 1

        return filtered

    def _short_blocked(self, portfolio, prices, portfolio_value,
                       proposed_value: float) -> bool:
        """True if a new short would breach short or gross exposure limits."""
        if portfolio_value <= 0:
            return True
        short_notional = 0.0
        long_notional = 0.0
        for sym, pos in portfolio.positions.items():
            px = prices.get(sym, pos["avg_price"])
            notional = abs(pos["shares"]) * px
            if pos["shares"] < 0:
                short_notional += notional
            else:
                long_notional += notional
        if self.limits.max_short_exposure is not None:
            if (short_notional + proposed_value) / portfolio_value > \
                    self.limits.max_short_exposure:
                return True
        if self.limits.max_gross_exposure is not None:
            gross = short_notional + long_notional + proposed_value
            if gross / portfolio_value > self.limits.max_gross_exposure:
                return True
        return False

    def generate_exit_signals(
        self,
        portfolio: "Portfolio",
        prices: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """
        Generate exit signals for positions that hit risk limits.

        Args:
            portfolio: Current portfolio
            prices: Current prices

        Returns:
            List of sell signals for positions to exit
        """
        exits = []

        for symbol, position in portfolio.positions.items():
            entry_price = position.get("avg_price", 0)
            shares = position.get("shares", 0)
            current_price = prices.get(symbol)

            if current_price is None:
                continue

            action = self.check_position(
                symbol=symbol,
                entry_price=entry_price,
                current_price=current_price,
                shares=shares,
            )

            if action == RiskAction.EXIT:
                pct_change = (current_price - entry_price) / entry_price
                if shares < 0:
                    # Short: cover to close; a loss (price up) is the guardrail
                    # firing, a gain (price down) is a take-profit.
                    reason = "stop_loss" if pct_change > 0 else "take_profit"
                    exits.append({
                        "symbol": symbol,
                        "action": "cover",
                        "shares": -shares,       # positive share count to cover
                        "reason": reason,
                    })
                else:
                    reason = "stop_loss" if pct_change < 0 else "take_profit"
                    exits.append({
                        "symbol": symbol,
                        "action": "sell",
                        "shares": shares,
                        "reason": reason,
                    })

        return exits


# =============================================================================
# Preset Configurations
# =============================================================================

# Conservative risk - tight limits for capital preservation
CONSERVATIVE_RISK = RiskLimits(
    stop_loss_pct=0.05,  # 5% stop loss
    take_profit_pct=0.15,  # 15% take profit
    max_position_pct=0.05,  # 5% max per position
    max_positions=10,  # Max 10 positions
    max_portfolio_drawdown=0.10,  # 10% max drawdown
)

# Aggressive risk - wider limits for growth
AGGRESSIVE_RISK = RiskLimits(
    stop_loss_pct=0.15,  # 15% stop loss
    take_profit_pct=0.50,  # 50% take profit
    max_position_pct=0.20,  # 20% max per position
    max_positions=5,  # Concentrated positions
    max_portfolio_drawdown=0.25,  # 25% max drawdown
)
