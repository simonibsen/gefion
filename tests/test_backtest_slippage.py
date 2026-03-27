"""
TDD tests for slippage module.

Tests written FIRST before implementation.
"""
import pytest
from datetime import date


class TestSlippageModel:
    """Test SlippageModel execution price calculation."""

    def test_zero_slippage_returns_order_price(self):
        """Zero slippage model returns exact order price."""
        from gefion.backtest.slippage import SlippageModel

        slippage = SlippageModel()
        price = slippage.calculate_execution_price(100.0, 100, "buy")
        assert price == 100.0

    def test_fixed_slippage_increases_buy_price(self):
        """Buy orders execute at higher price due to slippage."""
        from gefion.backtest.slippage import SlippageModel

        slippage = SlippageModel(fixed_slippage_pct=0.001)  # 10 bps
        price = slippage.calculate_execution_price(100.0, 100, "buy")
        assert price > 100.0
        assert abs(price - 100.1) < 0.01

    def test_fixed_slippage_decreases_sell_price(self):
        """Sell orders execute at lower price due to slippage."""
        from gefion.backtest.slippage import SlippageModel

        slippage = SlippageModel(fixed_slippage_pct=0.001)  # 10 bps
        price = slippage.calculate_execution_price(100.0, 100, "sell")
        assert price < 100.0
        assert abs(price - 99.9) < 0.01

    def test_volume_based_slippage_larger_orders(self):
        """Larger orders have more slippage."""
        from gefion.backtest.slippage import SlippageModel

        slippage = SlippageModel(volume_slippage_coefficient=0.01)

        small_order_price = slippage.calculate_execution_price(
            100.0, 100, "buy", daily_volume=10000
        )
        large_order_price = slippage.calculate_execution_price(
            100.0, 1000, "buy", daily_volume=10000
        )

        # Both should be higher than 100 (buy slippage)
        assert small_order_price > 100.0
        assert large_order_price > 100.0
        # Large order should have more slippage
        assert large_order_price > small_order_price

    def test_volume_slippage_no_volume_provided(self):
        """Volume slippage is zero when volume not provided."""
        from gefion.backtest.slippage import SlippageModel

        slippage = SlippageModel(volume_slippage_coefficient=0.01)
        price = slippage.calculate_execution_price(100.0, 100, "buy")
        # Without volume, should just return base price
        assert price == 100.0

    def test_volatility_based_slippage(self):
        """Higher volatility causes more slippage."""
        from gefion.backtest.slippage import SlippageModel

        slippage = SlippageModel(volatility_slippage_coefficient=0.5)

        low_vol_price = slippage.calculate_execution_price(
            100.0, 100, "buy", volatility=0.01
        )
        high_vol_price = slippage.calculate_execution_price(
            100.0, 100, "buy", volatility=0.05
        )

        assert low_vol_price > 100.0
        assert high_vol_price > 100.0
        assert high_vol_price > low_vol_price

    def test_combined_slippage(self):
        """All slippage components combine."""
        from gefion.backtest.slippage import SlippageModel

        slippage = SlippageModel(
            fixed_slippage_pct=0.001,
            volume_slippage_coefficient=0.01,
            volatility_slippage_coefficient=0.5
        )

        price = slippage.calculate_execution_price(
            100.0, 100, "buy",
            daily_volume=10000,
            volatility=0.02
        )

        # Should be higher than just fixed slippage
        fixed_only = SlippageModel(fixed_slippage_pct=0.001)
        fixed_price = fixed_only.calculate_execution_price(100.0, 100, "buy")

        assert price > fixed_price


class TestLimitOrders:
    """Test limit order behavior."""

    def test_limit_order_fills_at_limit(self):
        """Limit order fills at limit price when conditions met."""
        from gefion.backtest.slippage import SlippageModel, OrderType

        slippage = SlippageModel()
        price = slippage.calculate_execution_price(
            99.0, 100, "buy",
            order_type=OrderType.LIMIT,
            limit_price=100.0
        )
        # Order price 99 < limit 100, should fill at limit
        assert price == 100.0

    def test_limit_buy_no_fill_above_limit(self):
        """Buy limit order doesn't fill when price above limit."""
        from gefion.backtest.slippage import SlippageModel, OrderType

        slippage = SlippageModel()
        price = slippage.calculate_execution_price(
            101.0, 100, "buy",
            order_type=OrderType.LIMIT,
            limit_price=100.0
        )
        # Order price 101 > limit 100, should not fill
        assert price is None

    def test_limit_sell_no_fill_below_limit(self):
        """Sell limit order doesn't fill when price below limit."""
        from gefion.backtest.slippage import SlippageModel, OrderType

        slippage = SlippageModel()
        price = slippage.calculate_execution_price(
            99.0, 100, "sell",
            order_type=OrderType.LIMIT,
            limit_price=100.0
        )
        # Order price 99 < limit 100, should not fill
        assert price is None


class TestSlippagePresets:
    """Test preset slippage configurations."""

    def test_zero_slippage_preset(self):
        """ZERO_SLIPPAGE preset has no slippage."""
        from gefion.backtest.slippage import ZERO_SLIPPAGE

        price = ZERO_SLIPPAGE.calculate_execution_price(100.0, 1000, "buy")
        assert price == 100.0

    def test_realistic_slippage_preset(self):
        """REALISTIC_SLIPPAGE preset exists and applies slippage."""
        from gefion.backtest.slippage import REALISTIC_SLIPPAGE

        price = REALISTIC_SLIPPAGE.calculate_execution_price(
            100.0, 1000, "buy",
            daily_volume=10000,
            volatility=0.02
        )
        assert price > 100.0
