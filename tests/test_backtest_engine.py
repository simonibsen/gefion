"""
TDD tests for backtesting engine.

These tests will initially fail and drive the implementation of
a simple, point-in-time correct backtesting engine.
"""
import pytest
from datetime import date


def test_portfolio_initialization():
    """Test creating a portfolio with initial cash."""
    from gefion.backtest.portfolio import Portfolio

    portfolio = Portfolio(initial_cash=100000.0)

    assert portfolio.cash == 100000.0
    assert portfolio.equity == 100000.0
    assert len(portfolio.positions) == 0


def test_portfolio_buy_stock():
    """Test buying a stock."""
    from gefion.backtest.portfolio import Portfolio

    portfolio = Portfolio(initial_cash=100000.0)

    # Buy 100 shares of AAPL at $150
    portfolio.buy(symbol="AAPL", shares=100, price=150.0, date=date(2024, 1, 1))

    assert portfolio.cash == 85000.0  # 100000 - (100 * 150)
    assert "AAPL" in portfolio.positions
    assert portfolio.positions["AAPL"]["shares"] == 100
    assert portfolio.positions["AAPL"]["avg_price"] == 150.0


def test_portfolio_sell_stock():
    """Test selling a stock."""
    from gefion.backtest.portfolio import Portfolio

    portfolio = Portfolio(initial_cash=100000.0)
    portfolio.buy(symbol="AAPL", shares=100, price=150.0, date=date(2024, 1, 1))

    # Sell 50 shares at $160
    portfolio.sell(symbol="AAPL", shares=50, price=160.0, date=date(2024, 1, 2))

    assert portfolio.cash == 93000.0  # 85000 + (50 * 160)
    assert portfolio.positions["AAPL"]["shares"] == 50


def test_portfolio_equity_calculation():
    """Test portfolio equity calculation with current prices."""
    from gefion.backtest.portfolio import Portfolio

    portfolio = Portfolio(initial_cash=100000.0)
    portfolio.buy(symbol="AAPL", shares=100, price=150.0, date=date(2024, 1, 1))
    portfolio.buy(symbol="MSFT", shares=50, price=300.0, date=date(2024, 1, 1))

    # Calculate equity with current prices
    current_prices = {"AAPL": 160.0, "MSFT": 320.0}
    equity = portfolio.calculate_equity(current_prices)

    # Cash: 100000 - 15000 - 15000 = 70000
    # AAPL value: 100 * 160 = 16000
    # MSFT value: 50 * 320 = 16000
    # Total equity: 70000 + 16000 + 16000 = 102000
    assert abs(equity - 102000.0) < 0.01


def test_backtest_engine_simple_strategy():
    """Test running a simple backtest with buy-and-hold strategy."""
    from gefion.backtest.engine import BacktestEngine
    from gefion.backtest.portfolio import Portfolio

    # Sample price data for backtesting
    price_data = [
        {"symbol": "AAPL", "date": date(2024, 1, 1), "close": 150.0},
        {"symbol": "AAPL", "date": date(2024, 1, 2), "close": 155.0},
        {"symbol": "AAPL", "date": date(2024, 1, 3), "close": 160.0},
        {"symbol": "MSFT", "date": date(2024, 1, 1), "close": 300.0},
        {"symbol": "MSFT", "date": date(2024, 1, 2), "close": 310.0},
        {"symbol": "MSFT", "date": date(2024, 1, 3), "close": 320.0},
    ]

    # Simple strategy: buy equal amounts on day 1, hold
    def simple_strategy(current_date, portfolio, prices):
        """Buy equal amounts of AAPL and MSFT on first day."""
        if current_date == date(2024, 1, 1):
            return [
                {"action": "buy", "symbol": "AAPL", "shares": 100},
                {"action": "buy", "symbol": "MSFT", "shares": 50},
            ]
        return []

    engine = BacktestEngine(
        price_data=price_data,
        strategy=simple_strategy,
        initial_cash=100000.0,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 3),
    )

    results = engine.run()

    # Verify results structure
    assert "trades" in results
    assert "equity_curve" in results
    assert "metrics" in results

    # Verify trades
    assert len(results["trades"]) == 2  # 2 buy orders

    # Verify equity curve
    assert len(results["equity_curve"]) == 3  # 3 days

    # Verify final equity
    # Starting: 100000
    # Day 1: Buy 100 AAPL @ 150 = -15000, Buy 50 MSFT @ 300 = -15000
    # Cash: 70000
    # Day 3: AAPL = 100 * 160 = 16000, MSFT = 50 * 320 = 16000
    # Final equity: 70000 + 16000 + 16000 = 102000
    final_equity = results["equity_curve"][-1]["equity"]
    assert abs(final_equity - 102000.0) < 0.01


def test_backtest_metrics_calculation():
    """Test calculating backtest performance metrics."""
    from gefion.backtest.metrics import calculate_metrics

    equity_curve = [
        {"date": date(2024, 1, 1), "equity": 100000.0},
        {"date": date(2024, 1, 2), "equity": 101000.0},
        {"date": date(2024, 1, 3), "equity": 102000.0},
        {"date": date(2024, 1, 4), "equity": 99000.0},  # Drawdown
        {"date": date(2024, 1, 5), "equity": 103000.0},
    ]

    metrics = calculate_metrics(equity_curve, initial_capital=100000.0)

    # Total return = (103000 - 100000) / 100000 = 0.03 = 3%
    assert abs(metrics["total_return"] - 0.03) < 0.001

    # Max drawdown = (99000 - 102000) / 102000 = -0.0294 = -2.94%
    assert abs(metrics["max_drawdown"] + 0.0294) < 0.001

    # Verify other metrics exist
    assert "sharpe_ratio" in metrics
    assert "num_trades" in metrics


def test_point_in_time_correctness():
    """Test that strategy only has access to past data (no look-ahead bias)."""
    from gefion.backtest.engine import BacktestEngine

    price_data = [
        {"symbol": "AAPL", "date": date(2024, 1, 1), "close": 150.0},
        {"symbol": "AAPL", "date": date(2024, 1, 2), "close": 160.0},  # Future price
    ]

    accessed_future_data = False

    def cheating_strategy(current_date, portfolio, prices):
        """Strategy that tries to access future prices."""
        nonlocal accessed_future_data
        # On Jan 1, should NOT have access to Jan 2 prices
        if current_date == date(2024, 1, 1):
            if "AAPL" in prices and date(2024, 1, 2) in [
                p["date"] for p in prices.get("AAPL", [])
            ]:
                accessed_future_data = True
        return []

    engine = BacktestEngine(
        price_data=price_data,
        strategy=cheating_strategy,
        initial_cash=100000.0,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
    )

    engine.run()

    # Strategy should NOT have accessed future data
    assert not accessed_future_data, "Strategy accessed future prices (look-ahead bias)"


def test_portfolio_transaction_log():
    """Test that portfolio maintains a transaction log."""
    from gefion.backtest.portfolio import Portfolio

    portfolio = Portfolio(initial_cash=100000.0)
    portfolio.buy(symbol="AAPL", shares=100, price=150.0, date=date(2024, 1, 1))
    portfolio.sell(symbol="AAPL", shares=50, price=160.0, date=date(2024, 1, 2))

    # Verify transaction log
    assert len(portfolio.transactions) == 2

    # First transaction (buy)
    assert portfolio.transactions[0]["action"] == "buy"
    assert portfolio.transactions[0]["symbol"] == "AAPL"
    assert portfolio.transactions[0]["shares"] == 100
    assert portfolio.transactions[0]["price"] == 150.0

    # Second transaction (sell)
    assert portfolio.transactions[1]["action"] == "sell"
    assert portfolio.transactions[1]["symbol"] == "AAPL"
    assert portfolio.transactions[1]["shares"] == 50
    assert portfolio.transactions[1]["price"] == 160.0


class TestBacktestEngineIntegration:
    """Integration tests for BacktestEngine with advanced features."""

    def test_engine_with_costs_and_slippage(self):
        """Engine applies costs and slippage to trades."""
        from gefion.backtest.engine import BacktestEngine
        from gefion.backtest.costs import TransactionCosts
        from gefion.backtest.slippage import SlippageModel

        price_data = [
            {"date": date(2024, 1, 1), "symbol": "AAPL", "close": 100.0},
            {"date": date(2024, 1, 2), "symbol": "AAPL", "close": 105.0},
            {"date": date(2024, 1, 3), "symbol": "AAPL", "close": 110.0},
        ]

        # Strategy that buys on day 1
        def strategy(current_date, portfolio, historical_prices):
            if current_date == date(2024, 1, 1):
                return [{"action": "buy", "symbol": "AAPL", "shares": 10}]
            return []

        costs = TransactionCosts(commission_per_trade=10.0)
        slippage = SlippageModel(fixed_slippage_pct=0.001)

        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy,
            initial_cash=10000.0,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
            costs=costs,
            slippage=slippage,
        )
        results = engine.run()

        # Verify trade executed with slippage
        assert len(results["trades"]) == 1
        trade = results["trades"][0]
        assert trade["price"] > 100.0  # Slippage increased buy price
        assert trade["cost"] == 10.0  # Commission applied

    def test_engine_with_risk_manager(self):
        """Engine applies risk management to positions."""
        from gefion.backtest.engine import BacktestEngine
        from gefion.backtest.risk import RiskManager, RiskLimits

        price_data = [
            {"date": date(2024, 1, 1), "symbol": "AAPL", "close": 100.0},
            {"date": date(2024, 1, 2), "symbol": "AAPL", "close": 92.0},  # 8% drop
            {"date": date(2024, 1, 3), "symbol": "AAPL", "close": 90.0},
        ]

        def strategy(current_date, portfolio, historical_prices):
            if current_date == date(2024, 1, 1):
                return [{"action": "buy", "symbol": "AAPL", "shares": 10}]
            return []

        risk_manager = RiskManager(RiskLimits(stop_loss_pct=0.05))

        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy,
            initial_cash=10000.0,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
            risk_manager=risk_manager,
        )
        results = engine.run()

        # Should have buy on day 1 and stop loss exit on day 2
        assert len(results["trades"]) == 2
        assert results["trades"][0]["action"] == "buy"
        assert results["trades"][1]["action"] == "sell"
        assert results["trades"][1]["reason"] == "stop_loss"

    def test_engine_with_position_sizer(self):
        """Engine uses position sizer for trade sizing."""
        from gefion.backtest.engine import BacktestEngine
        from gefion.backtest.sizing import PositionSizer, SizingMethod

        price_data = [
            {"date": date(2024, 1, 1), "symbol": "AAPL", "close": 100.0},
            {"date": date(2024, 1, 2), "symbol": "AAPL", "close": 105.0},
        ]

        def strategy(current_date, portfolio, historical_prices):
            if current_date == date(2024, 1, 1):
                # Strategy requests arbitrary shares, sizer will override
                return [{"action": "buy", "symbol": "AAPL", "shares": 999}]
            return []

        sizer = PositionSizer(
            method=SizingMethod.FIXED_PERCENT,
            fixed_percent=0.10,  # 10% of portfolio
        )

        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy,
            initial_cash=10000.0,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            position_sizer=sizer,
        )
        results = engine.run()

        # Position sizer should size to 10% of portfolio = $1000 = 10 shares
        assert len(results["trades"]) == 1
        assert results["trades"][0]["shares"] == 10  # 10000 * 0.10 / 100

    def test_engine_backward_compatible(self):
        """Engine works without optional features (backward compatible)."""
        from gefion.backtest.engine import BacktestEngine

        price_data = [
            {"date": date(2024, 1, 1), "symbol": "AAPL", "close": 100.0},
            {"date": date(2024, 1, 2), "symbol": "AAPL", "close": 105.0},
        ]

        def strategy(current_date, portfolio, historical_prices):
            if current_date == date(2024, 1, 1):
                return [{"action": "buy", "symbol": "AAPL", "shares": 10}]
            return []

        # No optional params - should work exactly as before
        engine = BacktestEngine(
            price_data=price_data,
            strategy=strategy,
            initial_cash=10000.0,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
        )
        results = engine.run()

        assert len(results["trades"]) == 1
        assert results["trades"][0]["price"] == 100.0  # No slippage


class TestSellTradesCarryPnl:
    """Engine sell records must carry realized pnl for trade metrics.

    Portfolio computes realized_pnl internally but the engine's trade
    record dropped it, so win_rate/profit_factor were always 0.0.
    """

    def test_sell_trade_includes_pnl(self):
        from datetime import date
        from gefion.backtest.engine import BacktestEngine
        from gefion.backtest.portfolio import Portfolio

        engine = BacktestEngine(
            price_data=[], strategy=lambda *a, **k: [],
            initial_cash=100000.0,
            start_date=date(2026, 1, 1), end_date=date(2026, 3, 1))
        portfolio = Portfolio(initial_cash=100000.0)
        prices = {"AAPL": 100.0}

        buy = engine._execute_signal(
            {"action": "buy", "symbol": "AAPL", "shares": 10},
            portfolio, prices, date(2026, 1, 5))
        assert buy is not None

        sell = engine._execute_signal(
            {"action": "sell", "symbol": "AAPL", "shares": 10},
            portfolio, {"AAPL": 110.0}, date(2026, 2, 5))

        assert sell is not None
        assert sell["pnl"] == pytest.approx((110.0 - 100.0) * 10)
