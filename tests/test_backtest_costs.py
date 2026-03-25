"""
TDD tests for transaction costs module.

Tests written FIRST before implementation.
"""
import pytest
from datetime import date


class TestTransactionCosts:
    """Test TransactionCosts calculation."""

    def test_zero_costs_returns_zero(self):
        """Zero cost model returns zero for any trade."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts()
        assert costs.calculate_cost(100, 50.0, "buy") == 0.0
        assert costs.calculate_cost(1000, 100.0, "sell") == 0.0

    def test_commission_per_trade(self):
        """Fixed commission per trade is applied."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(commission_per_trade=10.0)
        assert costs.calculate_cost(100, 50.0, "buy") == 10.0
        assert costs.calculate_cost(1, 10.0, "sell") == 10.0

    def test_commission_per_share(self):
        """Per-share commission is calculated correctly."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(commission_per_share=0.01)
        assert costs.calculate_cost(100, 50.0, "buy") == 1.0  # 100 * 0.01
        assert costs.calculate_cost(500, 50.0, "sell") == 5.0  # 500 * 0.01

    def test_commission_combined(self):
        """Per-trade and per-share commissions combine."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(
            commission_per_trade=5.0,
            commission_per_share=0.01
        )
        # 5 + (100 * 0.01) = 6
        assert costs.calculate_cost(100, 50.0, "buy") == 6.0

    def test_commission_min_applied(self):
        """Minimum commission is enforced."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(
            commission_per_trade=1.0,
            commission_min=5.0
        )
        # Base is 1.0, but min is 5.0
        assert costs.calculate_cost(1, 10.0, "buy") == 5.0

    def test_commission_max_applied(self):
        """Maximum commission is enforced."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(
            commission_per_share=1.0,
            commission_max=10.0
        )
        # 100 shares * $1/share = $100, but capped at $10
        assert costs.calculate_cost(100, 50.0, "buy") == 10.0

    def test_bid_ask_spread(self):
        """Bid-ask spread cost is calculated as % of trade value."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(bid_ask_spread_pct=0.001)  # 10 bps
        # Trade value = 100 * 100 = 10000
        # Spread cost = 10000 * 0.001 = 10
        cost = costs.calculate_cost(100, 100.0, "buy")
        assert abs(cost - 10.0) < 0.01

    def test_market_impact_with_volume(self):
        """Market impact scales with sqrt of participation rate."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(market_impact_coefficient=0.1)
        # 1000 shares, 10000 daily volume = 10% participation
        # sqrt(0.1) * 0.1 * 100000 = 0.316 * 0.1 * 100000 = 3162
        cost = costs.calculate_cost(1000, 100.0, "buy", daily_volume=10000)
        assert cost > 0
        # Verify it increases with larger orders
        cost_large = costs.calculate_cost(2000, 100.0, "buy", daily_volume=10000)
        assert cost_large > cost

    def test_market_impact_no_volume(self):
        """Market impact is zero when volume not provided."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(market_impact_coefficient=0.1)
        # No daily_volume provided, impact should be 0
        cost = costs.calculate_cost(1000, 100.0, "buy")
        assert cost == 0.0

    def test_all_costs_combined(self):
        """All cost components combine correctly."""
        from gefion.backtest.costs import TransactionCosts

        costs = TransactionCosts(
            commission_per_trade=5.0,
            commission_per_share=0.01,
            bid_ask_spread_pct=0.0005,  # 5 bps
            market_impact_coefficient=0.05
        )
        # Commission: 5 + (100 * 0.01) = 6
        # Spread: 100 * 100 * 0.0005 = 5
        # Impact: sqrt(100/10000) * 0.05 * 10000 = 0.1 * 0.05 * 10000 = 50
        cost = costs.calculate_cost(100, 100.0, "buy", daily_volume=10000)
        assert cost > 6 + 5  # At least commission + spread


class TestPortfolioCostsIntegration:
    """Test Portfolio integration with transaction costs."""

    def test_buy_with_costs_deducts_from_cash(self):
        """Buying with costs deducts cost from cash."""
        from datetime import date
        from gefion.backtest.portfolio import Portfolio
        from gefion.backtest.costs import TransactionCosts

        portfolio = Portfolio(initial_cash=10000.0)
        costs = TransactionCosts(commission_per_trade=10.0)

        # Buy 100 shares at $50 = $5000 + $10 commission = $5010
        portfolio.buy("AAPL", 100, 50.0, date(2024, 1, 1), costs=costs)

        assert portfolio.cash == 10000.0 - 5000.0 - 10.0  # 4990

    def test_buy_without_costs_unchanged(self):
        """Buying without costs works as before (backward compatible)."""
        from datetime import date
        from gefion.backtest.portfolio import Portfolio

        portfolio = Portfolio(initial_cash=10000.0)
        portfolio.buy("AAPL", 100, 50.0, date(2024, 1, 1))

        assert portfolio.cash == 5000.0  # Just the stock cost

    def test_sell_with_costs_reduces_proceeds(self):
        """Selling with costs reduces net proceeds."""
        from datetime import date
        from gefion.backtest.portfolio import Portfolio
        from gefion.backtest.costs import TransactionCosts

        portfolio = Portfolio(initial_cash=10000.0)
        costs = TransactionCosts(commission_per_trade=10.0)

        # Buy without costs
        portfolio.buy("AAPL", 100, 50.0, date(2024, 1, 1))
        # Sell with costs: $5500 proceeds - $10 commission = $5490 net
        portfolio.sell("AAPL", 100, 55.0, date(2024, 1, 2), costs=costs)

        assert portfolio.cash == 5000.0 + 5500.0 - 10.0  # 10490

    def test_transaction_logs_cost(self):
        """Transaction log includes cost information."""
        from datetime import date
        from gefion.backtest.portfolio import Portfolio
        from gefion.backtest.costs import TransactionCosts

        portfolio = Portfolio(initial_cash=10000.0)
        costs = TransactionCosts(commission_per_trade=10.0)

        portfolio.buy("AAPL", 100, 50.0, date(2024, 1, 1), costs=costs)

        assert len(portfolio.transactions) == 1
        tx = portfolio.transactions[0]
        assert tx["cost"] == 10.0

    def test_insufficient_cash_includes_costs(self):
        """Insufficient cash check includes transaction costs."""
        from datetime import date
        import pytest
        from gefion.backtest.portfolio import Portfolio
        from gefion.backtest.costs import TransactionCosts

        portfolio = Portfolio(initial_cash=5005.0)  # Just enough for shares, not costs
        costs = TransactionCosts(commission_per_trade=10.0)

        # 100 shares * $50 = $5000, + $10 cost = $5010 > $5005
        with pytest.raises(ValueError, match="Insufficient cash"):
            portfolio.buy("AAPL", 100, 50.0, date(2024, 1, 1), costs=costs)


class TestCostPresets:
    """Test preset cost configurations."""

    def test_zero_costs_preset(self):
        """ZERO_COSTS preset has all zero values."""
        from gefion.backtest.costs import ZERO_COSTS

        assert ZERO_COSTS.calculate_cost(1000, 100.0, "buy") == 0.0

    def test_retail_costs_preset(self):
        """RETAIL_COSTS preset exists and is reasonable."""
        from gefion.backtest.costs import RETAIL_COSTS

        # Retail should have low costs
        cost = RETAIL_COSTS.calculate_cost(100, 100.0, "buy")
        # Should be less than 1% of trade value
        assert cost < 100.0

    def test_institutional_costs_preset(self):
        """INSTITUTIONAL_COSTS preset exists and includes market impact."""
        from gefion.backtest.costs import INSTITUTIONAL_COSTS

        # Large order should have market impact
        cost = INSTITUTIONAL_COSTS.calculate_cost(
            10000, 100.0, "buy", daily_volume=100000
        )
        assert cost > 0
