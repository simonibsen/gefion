"""
TDD tests for position sizing module.

Tests written FIRST before implementation.
"""
import pytest
from datetime import date


class TestSizingMethod:
    """Test SizingMethod enum."""

    def test_sizing_methods_exist(self):
        """All sizing methods are available."""
        from gefion.backtest.sizing import SizingMethod

        assert SizingMethod.FIXED_DOLLAR is not None
        assert SizingMethod.FIXED_PERCENT is not None
        assert SizingMethod.KELLY is not None
        assert SizingMethod.RISK_PARITY is not None
        assert SizingMethod.VOLATILITY_TARGET is not None


class TestPositionSizerFixedDollar:
    """Test fixed dollar position sizing."""

    def test_fixed_dollar_calculates_shares(self):
        """Fixed dollar sizing returns correct share count."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.FIXED_DOLLAR,
            fixed_dollar_amount=10000.0,
        )
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=50.0,
            symbol="AAPL",
        )
        # 10000 / 50 = 200 shares
        assert shares == 200

    def test_fixed_dollar_rounds_down(self):
        """Fixed dollar sizing rounds down to whole shares."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.FIXED_DOLLAR,
            fixed_dollar_amount=10000.0,
        )
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=75.0,
            symbol="AAPL",
        )
        # 10000 / 75 = 133.33 -> 133 shares
        assert shares == 133


class TestPositionSizerFixedPercent:
    """Test fixed percent position sizing."""

    def test_fixed_percent_calculates_shares(self):
        """Fixed percent sizing returns correct share count."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.FIXED_PERCENT,
            fixed_percent=0.10,  # 10% of portfolio
        )
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=50.0,
            symbol="AAPL",
        )
        # 100000 * 0.10 / 50 = 200 shares
        assert shares == 200

    def test_fixed_percent_with_different_portfolio(self):
        """Fixed percent adjusts with portfolio size."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.FIXED_PERCENT,
            fixed_percent=0.05,  # 5% of portfolio
        )
        shares = sizer.calculate_shares(
            portfolio_value=50000.0,
            price=25.0,
            symbol="AAPL",
        )
        # 50000 * 0.05 / 25 = 100 shares
        assert shares == 100


class TestPositionSizerKelly:
    """Test Kelly criterion position sizing."""

    def test_kelly_sizing_with_win_rate(self):
        """Kelly sizing uses win rate and win/loss ratio."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.KELLY,
            kelly_fraction=0.25,  # Quarter Kelly for safety
        )
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=100.0,
            symbol="AAPL",
            win_rate=0.55,  # 55% win rate
            win_loss_ratio=1.5,  # Win 1.5x what we lose
        )
        # Kelly% = (0.55 * 1.5 - 0.45) / 1.5 = 0.375
        # Position = 100000 * 0.375 * 0.25 / 100 = 93.75 -> 93
        assert shares > 0
        assert shares <= 100  # Reasonable for this portfolio

    def test_kelly_negative_edge_returns_zero(self):
        """Kelly returns zero shares for negative edge strategies."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.KELLY,
            kelly_fraction=0.25,
        )
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=100.0,
            symbol="AAPL",
            win_rate=0.40,  # 40% win rate
            win_loss_ratio=1.0,  # Break even wins
        )
        # Kelly% = (0.4 * 1.0 - 0.6) / 1.0 = -0.2 (negative edge)
        assert shares == 0


class TestPositionSizerVolatilityTarget:
    """Test volatility target position sizing."""

    def test_volatility_target_sizing(self):
        """Volatility targeting adjusts size for target volatility."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.VOLATILITY_TARGET,
            target_volatility=0.15,  # Target 15% portfolio vol
        )
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=100.0,
            symbol="AAPL",
            volatility=0.30,  # Stock vol is 30%
        )
        # Position weight = 0.15 / 0.30 = 0.50 (50% of portfolio)
        # Shares = 100000 * 0.50 / 100 = 500
        assert shares == 500

    def test_volatility_target_low_vol_stock(self):
        """Low volatility stocks get larger positions."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.VOLATILITY_TARGET,
            target_volatility=0.15,
        )
        low_vol_shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=100.0,
            symbol="JNJ",
            volatility=0.15,  # Low vol = 15%
        )
        high_vol_shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=100.0,
            symbol="TSLA",
            volatility=0.45,  # High vol = 45%
        )
        # Low vol stock should get more shares
        assert low_vol_shares > high_vol_shares


class TestPositionSizerRiskParity:
    """Test risk parity position sizing."""

    def test_risk_parity_equal_risk(self):
        """Risk parity equalizes risk contribution."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(
            method=SizingMethod.RISK_PARITY,
        )
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=100.0,
            symbol="AAPL",
            volatility=0.25,
            num_assets=5,  # Targeting 5 equal-risk positions
        )
        # Risk per asset = 1/5 = 20%
        # Position weight for 25% vol stock = 0.20 / 0.25 = 0.80
        # But capped to reasonable amount
        assert shares > 0


class TestPositionSizerDefaults:
    """Test default PositionSizer behavior."""

    def test_default_method_is_fixed_percent(self):
        """Default sizing method is fixed percent."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer()
        assert sizer.method == SizingMethod.FIXED_PERCENT

    def test_default_values(self):
        """Default values are reasonable."""
        from gefion.backtest.sizing import PositionSizer

        sizer = PositionSizer()
        assert sizer.fixed_dollar_amount == 10000.0
        assert sizer.fixed_percent == 0.10
        assert sizer.kelly_fraction == 0.25
        assert sizer.target_volatility == 0.15


class TestPositionSizerEdgeCases:
    """Test edge cases in position sizing."""

    def test_zero_price_returns_zero(self):
        """Zero price returns zero shares."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(method=SizingMethod.FIXED_DOLLAR)
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=0.0,
            symbol="AAPL",
        )
        assert shares == 0

    def test_zero_portfolio_returns_zero(self):
        """Zero portfolio value returns zero shares."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(method=SizingMethod.FIXED_PERCENT)
        shares = sizer.calculate_shares(
            portfolio_value=0.0,
            price=100.0,
            symbol="AAPL",
        )
        assert shares == 0

    def test_missing_volatility_for_vol_target(self):
        """Missing volatility for vol targeting returns zero."""
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        sizer = PositionSizer(method=SizingMethod.VOLATILITY_TARGET)
        shares = sizer.calculate_shares(
            portfolio_value=100000.0,
            price=100.0,
            symbol="AAPL",
            # No volatility provided
        )
        assert shares == 0
