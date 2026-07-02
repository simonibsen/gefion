"""
TDD tests for extended backtest metrics.

These tests will initially fail (RED) and drive the implementation of
additional performance metrics for strategy comparison.
"""
import pytest
import math
from datetime import date


class TestSortinoRatio:
    """Tests for Sortino ratio calculation."""

    def test_sortino_ratio_basic(self):
        """Sortino ratio uses only downside deviation."""
        from gefion.backtest.metrics import calculate_sortino_ratio

        # Equity curve with mixed returns
        equity_curve = [
            {"date": date(2024, 1, 1), "equity": 100000.0},
            {"date": date(2024, 1, 2), "equity": 101000.0},  # +1%
            {"date": date(2024, 1, 3), "equity": 100000.0},  # -0.99%
            {"date": date(2024, 1, 4), "equity": 102000.0},  # +2%
            {"date": date(2024, 1, 5), "equity": 101000.0},  # -0.98%
        ]

        sortino = calculate_sortino_ratio(equity_curve)

        # Should be a positive number since overall trend is positive
        assert sortino > 0
        # Sortino should be higher than Sharpe when upside volatility is high
        # (because Sortino ignores upside volatility)

    def test_sortino_ratio_no_downside(self):
        """Sortino ratio with no negative returns."""
        from gefion.backtest.metrics import calculate_sortino_ratio

        # All positive returns
        equity_curve = [
            {"date": date(2024, 1, 1), "equity": 100000.0},
            {"date": date(2024, 1, 2), "equity": 101000.0},
            {"date": date(2024, 1, 3), "equity": 102000.0},
            {"date": date(2024, 1, 4), "equity": 103000.0},
        ]

        sortino = calculate_sortino_ratio(equity_curve)

        # With no downside, should return 0 (undefined)
        assert sortino == 0.0

    def test_sortino_ratio_empty_curve(self):
        """Sortino ratio handles empty equity curve."""
        from gefion.backtest.metrics import calculate_sortino_ratio

        assert calculate_sortino_ratio([]) == 0.0

    def test_sortino_ratio_single_point(self):
        """Sortino ratio handles single data point."""
        from gefion.backtest.metrics import calculate_sortino_ratio

        equity_curve = [{"date": date(2024, 1, 1), "equity": 100000.0}]
        assert calculate_sortino_ratio(equity_curve) == 0.0


class TestCalmarRatio:
    """Tests for Calmar ratio calculation."""

    def test_calmar_ratio_basic(self):
        """Calmar ratio is annualized return / max drawdown."""
        from gefion.backtest.metrics import calculate_calmar_ratio

        # Equity curve: 20% total return over ~1 year with 10% max drawdown
        equity_curve = [
            {"date": date(2024, 1, 1), "equity": 100000.0},
            {"date": date(2024, 6, 1), "equity": 110000.0},
            {"date": date(2024, 7, 1), "equity": 99000.0},  # 10% drawdown from peak
            {"date": date(2024, 12, 31), "equity": 120000.0},
        ]

        calmar = calculate_calmar_ratio(equity_curve, days=365)

        # Annualized return ~20%, max drawdown ~10%
        # Calmar should be approximately 2.0
        assert calmar > 0
        assert 1.5 < calmar < 2.5  # Approximate range

    def test_calmar_ratio_no_drawdown(self):
        """Calmar ratio with no drawdown."""
        from gefion.backtest.metrics import calculate_calmar_ratio

        # Monotonically increasing equity
        equity_curve = [
            {"date": date(2024, 1, 1), "equity": 100000.0},
            {"date": date(2024, 6, 1), "equity": 110000.0},
            {"date": date(2024, 12, 31), "equity": 120000.0},
        ]

        calmar = calculate_calmar_ratio(equity_curve, days=365)

        # No drawdown means undefined/infinite - return 0
        assert calmar == 0.0

    def test_calmar_ratio_empty_curve(self):
        """Calmar ratio handles empty equity curve."""
        from gefion.backtest.metrics import calculate_calmar_ratio

        assert calculate_calmar_ratio([]) == 0.0


class TestTradeMetrics:
    """Tests for trade-based metrics (win rate, profit factor, avg win/loss)."""

    def test_win_rate_basic(self):
        """Win rate is percentage of profitable trades."""
        from gefion.backtest.metrics import calculate_trade_metrics

        trades = [
            {"symbol": "AAPL", "pnl": 100.0},   # Win
            {"symbol": "MSFT", "pnl": -50.0},  # Loss
            {"symbol": "GOOGL", "pnl": 75.0},  # Win
            {"symbol": "NVDA", "pnl": -25.0},  # Loss
            {"symbol": "TSLA", "pnl": 200.0},  # Win
        ]

        metrics = calculate_trade_metrics(trades)

        # 3 wins out of 5 trades = 60%
        assert abs(metrics["win_rate"] - 0.60) < 0.01

    def test_profit_factor_basic(self):
        """Profit factor is gross profit / gross loss."""
        from gefion.backtest.metrics import calculate_trade_metrics

        trades = [
            {"symbol": "AAPL", "pnl": 100.0},
            {"symbol": "MSFT", "pnl": -50.0},
            {"symbol": "GOOGL", "pnl": 200.0},
        ]

        metrics = calculate_trade_metrics(trades)

        # Gross profit = 300, gross loss = 50
        # Profit factor = 300 / 50 = 6.0
        assert abs(metrics["profit_factor"] - 6.0) < 0.01

    def test_avg_win_loss_ratio(self):
        """Average win/loss ratio."""
        from gefion.backtest.metrics import calculate_trade_metrics

        trades = [
            {"symbol": "AAPL", "pnl": 100.0},   # Win
            {"symbol": "MSFT", "pnl": 200.0},   # Win
            {"symbol": "GOOGL", "pnl": -50.0},  # Loss
            {"symbol": "NVDA", "pnl": -50.0},   # Loss
        ]

        metrics = calculate_trade_metrics(trades)

        # Avg win = 150, avg loss = 50
        # Ratio = 150 / 50 = 3.0
        assert abs(metrics["avg_win_loss_ratio"] - 3.0) < 0.01

    def test_trade_metrics_no_trades(self):
        """Trade metrics handle empty trade list."""
        from gefion.backtest.metrics import calculate_trade_metrics

        metrics = calculate_trade_metrics([])

        assert metrics["win_rate"] == 0.0
        assert metrics["profit_factor"] == 0.0
        assert metrics["avg_win_loss_ratio"] == 0.0
        assert metrics["total_trades"] == 0

    def test_trade_metrics_all_wins(self):
        """Trade metrics with all winning trades."""
        from gefion.backtest.metrics import calculate_trade_metrics

        trades = [
            {"symbol": "AAPL", "pnl": 100.0},
            {"symbol": "MSFT", "pnl": 50.0},
        ]

        metrics = calculate_trade_metrics(trades)

        assert metrics["win_rate"] == 1.0
        assert metrics["profit_factor"] == 0.0  # No losses, undefined
        assert metrics["avg_win_loss_ratio"] == 0.0  # No losses, undefined

    def test_trade_metrics_all_losses(self):
        """Trade metrics with all losing trades."""
        from gefion.backtest.metrics import calculate_trade_metrics

        trades = [
            {"symbol": "AAPL", "pnl": -100.0},
            {"symbol": "MSFT", "pnl": -50.0},
        ]

        metrics = calculate_trade_metrics(trades)

        assert metrics["win_rate"] == 0.0
        assert metrics["profit_factor"] == 0.0  # No wins
        assert metrics["avg_win_loss_ratio"] == 0.0  # No wins


class TestExtendedMetricsIntegration:
    """Integration tests for extended metrics in calculate_metrics."""

    def test_calculate_metrics_includes_extended(self):
        """calculate_metrics returns all extended metrics."""
        from gefion.backtest.metrics import calculate_metrics_extended

        equity_curve = [
            {"date": date(2024, 1, 1), "equity": 100000.0},
            {"date": date(2024, 1, 2), "equity": 101000.0},
            {"date": date(2024, 1, 3), "equity": 99000.0},
            {"date": date(2024, 1, 4), "equity": 102000.0},
        ]

        trades = [
            {"symbol": "AAPL", "pnl": 1000.0},
            {"symbol": "MSFT", "pnl": -1000.0},
            {"symbol": "GOOGL", "pnl": 2000.0},
        ]

        metrics = calculate_metrics_extended(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=100000.0,
        )

        # Original metrics
        assert "total_return" in metrics
        assert "max_drawdown" in metrics
        assert "sharpe_ratio" in metrics

        # Extended metrics
        assert "sortino_ratio" in metrics
        assert "calmar_ratio" in metrics
        assert "win_rate" in metrics
        assert "profit_factor" in metrics
        assert "avg_win_loss_ratio" in metrics
        assert "total_trades" in metrics


class TestWinRateUsesClosedTrades:
    """win_rate must be computed over closed (pnl-bearing) trades only.

    Buys cannot 'win' — counting them in the denominator dilutes the metric,
    and before the engine attached pnl to sells it was always exactly 0.0.
    """

    def test_win_rate_over_closed_trades_only(self):
        from gefion.backtest.metrics import calculate_trade_metrics

        trades = [
            {"action": "buy", "symbol": "A", "shares": 10, "price": 100},
            {"action": "buy", "symbol": "B", "shares": 10, "price": 100},
            {"action": "sell", "symbol": "A", "shares": 10, "price": 110, "pnl": 100.0},
            {"action": "sell", "symbol": "B", "shares": 10, "price": 95, "pnl": -50.0},
        ]
        m = calculate_trade_metrics(trades)

        assert m["win_rate"] == 0.5  # 1 of 2 closed trades, not 1 of 4
        assert m["total_trades"] == 4
        assert m["profit_factor"] == 2.0  # 100 / 50

    def test_no_closed_trades_reports_zero(self):
        from gefion.backtest.metrics import calculate_trade_metrics

        trades = [{"action": "buy", "symbol": "A", "shares": 10, "price": 100}]
        m = calculate_trade_metrics(trades)

        assert m["win_rate"] == 0.0


class TestCliDoesNotInjectPnlIntoAllTrades:
    """The CLI must pass engine trades through untouched.

    Its old compensation hack stamped pnl=0 onto every trade (buys
    included), defeating closed-trade detection: a 2-for-2 winning run
    reported win_rate 0.5 and profit_factor 0.0.
    """

    def test_cli_hack_removed(self):
        import pathlib
        src = pathlib.Path("src/gefion/cli.py").read_text()
        assert "trades_with_pnl" not in src
