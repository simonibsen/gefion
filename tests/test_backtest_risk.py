"""
TDD tests for risk management module.

Tests written FIRST before implementation.
"""
import pytest
from datetime import date


class TestRiskLimits:
    """Test RiskLimits configuration."""

    def test_default_limits_are_none(self):
        """All limits default to None (no limits)."""
        from g2.backtest.risk import RiskLimits

        limits = RiskLimits()
        assert limits.stop_loss_pct is None
        assert limits.take_profit_pct is None
        assert limits.max_position_pct is None
        assert limits.max_positions is None
        assert limits.max_portfolio_drawdown is None

    def test_limits_can_be_set(self):
        """Limits can be configured."""
        from g2.backtest.risk import RiskLimits

        limits = RiskLimits(
            stop_loss_pct=0.05,
            take_profit_pct=0.20,
            max_position_pct=0.10,
            max_positions=10,
            max_portfolio_drawdown=0.15,
        )
        assert limits.stop_loss_pct == 0.05
        assert limits.take_profit_pct == 0.20
        assert limits.max_position_pct == 0.10
        assert limits.max_positions == 10
        assert limits.max_portfolio_drawdown == 0.15


class TestRiskAction:
    """Test RiskAction enum."""

    def test_risk_actions_exist(self):
        """RiskAction has expected values."""
        from g2.backtest.risk import RiskAction

        assert RiskAction.HOLD is not None
        assert RiskAction.EXIT is not None
        assert RiskAction.BLOCK is not None


class TestRiskManagerPositionChecks:
    """Test RiskManager position-level risk checks."""

    def test_no_limits_returns_hold(self):
        """Position with no limits configured returns HOLD."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        manager = RiskManager(RiskLimits())
        action = manager.check_position(
            symbol="AAPL",
            entry_price=100.0,
            current_price=90.0,  # 10% loss
            shares=100,
        )
        assert action == RiskAction.HOLD

    def test_stop_loss_triggers_exit(self):
        """Position below stop loss triggers EXIT."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(stop_loss_pct=0.05)  # 5% stop loss
        manager = RiskManager(limits)

        action = manager.check_position(
            symbol="AAPL",
            entry_price=100.0,
            current_price=94.0,  # 6% loss > 5% stop loss
            shares=100,
        )
        assert action == RiskAction.EXIT

    def test_stop_loss_not_triggered(self):
        """Position above stop loss returns HOLD."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(stop_loss_pct=0.05)
        manager = RiskManager(limits)

        action = manager.check_position(
            symbol="AAPL",
            entry_price=100.0,
            current_price=96.0,  # 4% loss < 5% stop loss
            shares=100,
        )
        assert action == RiskAction.HOLD

    def test_take_profit_triggers_exit(self):
        """Position above take profit triggers EXIT."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(take_profit_pct=0.20)  # 20% take profit
        manager = RiskManager(limits)

        action = manager.check_position(
            symbol="AAPL",
            entry_price=100.0,
            current_price=125.0,  # 25% gain > 20% take profit
            shares=100,
        )
        assert action == RiskAction.EXIT

    def test_take_profit_not_triggered(self):
        """Position below take profit returns HOLD."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(take_profit_pct=0.20)
        manager = RiskManager(limits)

        action = manager.check_position(
            symbol="AAPL",
            entry_price=100.0,
            current_price=115.0,  # 15% gain < 20% take profit
            shares=100,
        )
        assert action == RiskAction.HOLD


class TestRiskManagerPortfolioChecks:
    """Test RiskManager portfolio-level risk checks."""

    def test_max_positions_blocks_new(self):
        """At max positions, new positions are blocked."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(max_positions=2)
        manager = RiskManager(limits)

        action = manager.check_portfolio(
            portfolio_value=100000.0,
            current_positions=2,  # Already at max
            proposed_position_value=5000.0,
        )
        assert action == RiskAction.BLOCK

    def test_below_max_positions_allows_new(self):
        """Below max positions, new positions are allowed."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(max_positions=5)
        manager = RiskManager(limits)

        action = manager.check_portfolio(
            portfolio_value=100000.0,
            current_positions=3,  # Below max
            proposed_position_value=5000.0,
        )
        assert action == RiskAction.HOLD

    def test_max_position_pct_blocks_large_position(self):
        """Position exceeding max percent of portfolio is blocked."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(max_position_pct=0.10)  # Max 10% per position
        manager = RiskManager(limits)

        action = manager.check_portfolio(
            portfolio_value=100000.0,
            current_positions=0,
            proposed_position_value=15000.0,  # 15% > 10% max
        )
        assert action == RiskAction.BLOCK

    def test_max_position_pct_allows_small_position(self):
        """Position within max percent is allowed."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(max_position_pct=0.10)
        manager = RiskManager(limits)

        action = manager.check_portfolio(
            portfolio_value=100000.0,
            current_positions=0,
            proposed_position_value=8000.0,  # 8% < 10% max
        )
        assert action == RiskAction.HOLD


class TestRiskManagerDrawdownCheck:
    """Test portfolio drawdown monitoring."""

    def test_drawdown_exceeds_limit(self):
        """Blocks new positions when portfolio drawdown exceeds limit."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(max_portfolio_drawdown=0.10)  # 10% max drawdown
        manager = RiskManager(limits)

        action = manager.check_drawdown(
            current_equity=85000.0,
            peak_equity=100000.0,  # 15% drawdown > 10% max
        )
        assert action == RiskAction.BLOCK

    def test_drawdown_within_limit(self):
        """Allows new positions when drawdown is within limit."""
        from g2.backtest.risk import RiskManager, RiskLimits, RiskAction

        limits = RiskLimits(max_portfolio_drawdown=0.10)
        manager = RiskManager(limits)

        action = manager.check_drawdown(
            current_equity=92000.0,
            peak_equity=100000.0,  # 8% drawdown < 10% max
        )
        assert action == RiskAction.HOLD


class TestRiskManagerSignalFiltering:
    """Test signal filtering through risk manager."""

    def test_filter_signals_removes_blocked(self):
        """Filter signals removes blocked signals based on risk rules."""
        from g2.backtest.risk import RiskManager, RiskLimits
        from g2.backtest.portfolio import Portfolio

        limits = RiskLimits(max_positions=2)
        manager = RiskManager(limits)

        portfolio = Portfolio(initial_cash=100000.0)
        # Already have 2 positions
        portfolio.positions = {
            "AAPL": {"shares": 100, "avg_price": 150.0},
            "MSFT": {"shares": 50, "avg_price": 300.0},
        }

        signals = [
            {"symbol": "GOOGL", "action": "buy", "shares": 10},  # Should be blocked
        ]
        prices = {"GOOGL": 2500.0}

        filtered = manager.filter_signals(signals, portfolio, prices)
        assert len(filtered) == 0  # Signal removed due to max positions

    def test_filter_signals_keeps_allowed(self):
        """Filter signals keeps allowed signals."""
        from g2.backtest.risk import RiskManager, RiskLimits
        from g2.backtest.portfolio import Portfolio

        limits = RiskLimits(max_positions=5)
        manager = RiskManager(limits)

        portfolio = Portfolio(initial_cash=100000.0)
        portfolio.positions = {
            "AAPL": {"shares": 100, "avg_price": 150.0},
        }

        signals = [
            {"symbol": "GOOGL", "action": "buy", "shares": 10},  # Should be allowed
        ]
        prices = {"GOOGL": 2500.0}

        filtered = manager.filter_signals(signals, portfolio, prices)
        assert len(filtered) == 1


class TestRiskManagerExitSignals:
    """Test automatic exit signal generation."""

    def test_generates_stop_loss_exit(self):
        """Generates exit signals for positions hitting stop loss."""
        from g2.backtest.risk import RiskManager, RiskLimits
        from g2.backtest.portfolio import Portfolio

        limits = RiskLimits(stop_loss_pct=0.05)
        manager = RiskManager(limits)

        portfolio = Portfolio(initial_cash=100000.0)
        portfolio.positions = {
            "AAPL": {"shares": 100, "avg_price": 100.0},  # Entry at 100
        }
        prices = {"AAPL": 94.0}  # Current at 94, 6% loss

        exits = manager.generate_exit_signals(portfolio, prices)
        assert len(exits) == 1
        assert exits[0]["symbol"] == "AAPL"
        assert exits[0]["action"] == "sell"
        assert exits[0]["reason"] == "stop_loss"

    def test_generates_take_profit_exit(self):
        """Generates exit signals for positions hitting take profit."""
        from g2.backtest.risk import RiskManager, RiskLimits
        from g2.backtest.portfolio import Portfolio

        limits = RiskLimits(take_profit_pct=0.20)
        manager = RiskManager(limits)

        portfolio = Portfolio(initial_cash=100000.0)
        portfolio.positions = {
            "AAPL": {"shares": 100, "avg_price": 100.0},  # Entry at 100
        }
        prices = {"AAPL": 125.0}  # Current at 125, 25% gain

        exits = manager.generate_exit_signals(portfolio, prices)
        assert len(exits) == 1
        assert exits[0]["symbol"] == "AAPL"
        assert exits[0]["action"] == "sell"
        assert exits[0]["reason"] == "take_profit"

    def test_no_exits_for_healthy_positions(self):
        """No exit signals for positions within limits."""
        from g2.backtest.risk import RiskManager, RiskLimits
        from g2.backtest.portfolio import Portfolio

        limits = RiskLimits(stop_loss_pct=0.05, take_profit_pct=0.20)
        manager = RiskManager(limits)

        portfolio = Portfolio(initial_cash=100000.0)
        portfolio.positions = {
            "AAPL": {"shares": 100, "avg_price": 100.0},
        }
        prices = {"AAPL": 105.0}  # 5% gain, within limits

        exits = manager.generate_exit_signals(portfolio, prices)
        assert len(exits) == 0


class TestRiskPresets:
    """Test preset risk configurations."""

    def test_conservative_risk_preset(self):
        """CONSERVATIVE_RISK preset exists with tight limits."""
        from g2.backtest.risk import CONSERVATIVE_RISK

        assert CONSERVATIVE_RISK.stop_loss_pct is not None
        assert CONSERVATIVE_RISK.take_profit_pct is not None
        assert CONSERVATIVE_RISK.max_position_pct is not None

    def test_aggressive_risk_preset(self):
        """AGGRESSIVE_RISK preset exists with looser limits."""
        from g2.backtest.risk import AGGRESSIVE_RISK

        # Aggressive should have higher limits
        assert AGGRESSIVE_RISK.stop_loss_pct is not None
        # Aggressive allows larger positions
        assert AGGRESSIVE_RISK.max_position_pct > 0.1
