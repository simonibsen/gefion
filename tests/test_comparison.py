"""
TDD tests for strategy comparison framework.

These tests will initially fail (RED) and drive the implementation of
the strategy comparison functionality.
"""
import pytest
from datetime import date, timedelta
from typing import Dict, Any, List


class TestCompareStrategies:
    """Tests for compare_strategies function."""

    def test_compare_strategies_returns_dict(self):
        """compare_strategies returns dict with strategy names as keys."""
        from gefion.backtest.comparison import compare_strategies

        # Create minimal price data
        price_data = _create_sample_price_data()

        result = compare_strategies(
            strategies=["momentum", "mean_reversion"],
            price_data=price_data,
            initial_capital=100000.0,
        )

        assert isinstance(result, dict)
        assert "momentum" in result
        assert "mean_reversion" in result

    def test_compare_strategies_returns_metrics(self):
        """Each strategy result contains expected metrics."""
        from gefion.backtest.comparison import compare_strategies

        price_data = _create_sample_price_data()

        result = compare_strategies(
            strategies=["momentum"],
            price_data=price_data,
            initial_capital=100000.0,
        )

        metrics = result["momentum"]

        # Check for base metrics
        assert "total_return" in metrics
        assert "max_drawdown" in metrics
        assert "sharpe_ratio" in metrics

        # Check for extended metrics
        assert "sortino_ratio" in metrics
        assert "calmar_ratio" in metrics
        assert "win_rate" in metrics
        assert "profit_factor" in metrics
        assert "total_trades" in metrics

    def test_compare_strategies_all_strategies(self):
        """Can compare all available strategies."""
        from gefion.backtest.comparison import compare_strategies, AVAILABLE_STRATEGIES

        price_data = _create_sample_price_data(days=60)

        result = compare_strategies(
            strategies=list(AVAILABLE_STRATEGIES.keys()),
            price_data=price_data,
            initial_capital=100000.0,
        )

        # All strategies should have results
        for strategy_name in AVAILABLE_STRATEGIES.keys():
            assert strategy_name in result
            assert "total_return" in result[strategy_name]

    def test_compare_strategies_invalid_strategy(self):
        """Invalid strategy name raises ValueError."""
        from gefion.backtest.comparison import compare_strategies

        price_data = _create_sample_price_data()

        with pytest.raises(ValueError, match="Unknown strategy"):
            compare_strategies(
                strategies=["nonexistent_strategy"],
                price_data=price_data,
                initial_capital=100000.0,
            )


class TestRankStrategies:
    """Tests for rank_strategies function."""

    def test_rank_strategies_by_sharpe(self):
        """rank_strategies orders strategies by Sharpe ratio (descending)."""
        from gefion.backtest.comparison import rank_strategies

        comparison = {
            "strategy_a": {"sharpe_ratio": 1.5, "total_return": 0.10},
            "strategy_b": {"sharpe_ratio": 2.0, "total_return": 0.08},
            "strategy_c": {"sharpe_ratio": 0.8, "total_return": 0.15},
        }

        ranked = rank_strategies(comparison, metric="sharpe_ratio")

        # Should be ordered by Sharpe (highest first)
        assert ranked[0][0] == "strategy_b"  # Sharpe 2.0
        assert ranked[1][0] == "strategy_a"  # Sharpe 1.5
        assert ranked[2][0] == "strategy_c"  # Sharpe 0.8

    def test_rank_strategies_by_return(self):
        """rank_strategies can order by total_return."""
        from gefion.backtest.comparison import rank_strategies

        comparison = {
            "strategy_a": {"sharpe_ratio": 1.5, "total_return": 0.10},
            "strategy_b": {"sharpe_ratio": 2.0, "total_return": 0.08},
            "strategy_c": {"sharpe_ratio": 0.8, "total_return": 0.15},
        }

        ranked = rank_strategies(comparison, metric="total_return")

        # Should be ordered by return (highest first)
        assert ranked[0][0] == "strategy_c"  # Return 15%
        assert ranked[1][0] == "strategy_a"  # Return 10%
        assert ranked[2][0] == "strategy_b"  # Return 8%

    def test_rank_strategies_ascending(self):
        """rank_strategies can order ascending (for drawdown)."""
        from gefion.backtest.comparison import rank_strategies

        comparison = {
            "strategy_a": {"max_drawdown": -0.15},
            "strategy_b": {"max_drawdown": -0.05},
            "strategy_c": {"max_drawdown": -0.25},
        }

        # For drawdown, smaller (less negative) is better
        ranked = rank_strategies(comparison, metric="max_drawdown", ascending=False)

        # Should order with least negative first (closest to 0)
        assert ranked[0][0] == "strategy_b"  # -5% best
        assert ranked[1][0] == "strategy_a"  # -15%
        assert ranked[2][0] == "strategy_c"  # -25% worst


class TestGetAvailableStrategies:
    """Tests for listing available strategies."""

    def test_available_strategies_dict(self):
        """AVAILABLE_STRATEGIES is a dict of strategy names to classes."""
        from gefion.backtest.comparison import AVAILABLE_STRATEGIES

        assert isinstance(AVAILABLE_STRATEGIES, dict)
        assert len(AVAILABLE_STRATEGIES) >= 6  # We have 7 strategies

        # Check known strategies
        assert "momentum" in AVAILABLE_STRATEGIES
        assert "mean_reversion" in AVAILABLE_STRATEGIES
        assert "ma_crossover" in AVAILABLE_STRATEGIES
        assert "breakout" in AVAILABLE_STRATEGIES
        assert "pairs_trading" in AVAILABLE_STRATEGIES
        assert "rsi_divergence" in AVAILABLE_STRATEGIES
        assert "volatility_contraction" in AVAILABLE_STRATEGIES


class TestFormatComparisonTable:
    """Tests for formatting comparison results."""

    def test_format_comparison_returns_rows(self):
        """format_comparison_table returns list of rows for table display."""
        from gefion.backtest.comparison import format_comparison_table

        comparison = {
            "momentum": {
                "total_return": 0.123,
                "max_drawdown": -0.152,
                "sharpe_ratio": 1.24,
                "win_rate": 0.58,
                "total_trades": 42,
            },
            "mean_reversion": {
                "total_return": 0.087,
                "max_drawdown": -0.184,
                "sharpe_ratio": 0.95,
                "win_rate": 0.52,
                "total_trades": 128,
            },
        }

        rows = format_comparison_table(comparison)

        assert len(rows) == 2
        # Each row should have strategy name and formatted metrics
        assert rows[0]["strategy"] in ["momentum", "mean_reversion"]
        assert "return_pct" in rows[0]
        assert "sharpe" in rows[0]


# Helper functions for test data

def _create_sample_price_data(
    symbols: List[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """Create sample price data for testing."""
    if symbols is None:
        symbols = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA"]

    today = date.today()
    price_data = []

    for symbol in symbols:
        base_price = 100.0 + hash(symbol) % 200  # Different base per symbol

        for i in range(days):
            test_date = today - timedelta(days=days - 1 - i)
            # Simple price movement with some volatility
            price = base_price * (1 + 0.001 * i + 0.02 * (hash(f"{symbol}{i}") % 10 - 5))

            price_data.append({
                "symbol": symbol,
                "date": test_date,
                "open": price * 0.99,
                "high": price * 1.02,
                "low": price * 0.98,
                "close": price,
                "volume": 1000000,
            })

    return price_data
