"""
TDD tests for backtest compare CLI command.

These tests will initially fail (RED) and drive the implementation of
the CLI command for strategy comparison.
"""
import json
import pytest
from datetime import date, timedelta
from typer.testing import CliRunner

from gefion.cli import app


runner = CliRunner()


def parse_json_output(output: str) -> dict:
    """Parse JSON output, handling pretty-printed multi-line format."""
    # Find the last complete JSON object (starts with '{\n  "_meta"')
    last_json_start = output.rfind('{\n  "_meta"')
    if last_json_start == -1:
        # Fallback: try to parse the whole output
        return json.loads(output)
    return json.loads(output[last_json_start:])


class TestBacktestCompareCommand:
    """Tests for backtest compare CLI command."""

    def test_compare_requires_strategies(self):
        """compare command requires --strategies option."""
        result = runner.invoke(
            app,
            [
                "backtest", "compare",
                "--start-date", "2024-01-01",
                "--end-date", "2024-12-01",
            ],
        )

        # Should fail with missing strategies
        assert result.exit_code != 0

    def test_compare_requires_symbols_or_exchange(self):
        """compare command requires --symbols or --exchange."""
        result = runner.invoke(
            app,
            [
                "backtest", "compare",
                "--strategies", "momentum,mean_reversion",
                "--start-date", "2024-01-01",
                "--end-date", "2024-12-01",
            ],
        )

        # Should fail with missing symbols/exchange
        assert result.exit_code != 0
        assert "symbols" in result.output.lower() or "exchange" in result.output.lower()

    def test_compare_requires_date_range(self):
        """compare command requires start and end dates."""
        result = runner.invoke(
            app,
            [
                "backtest", "compare",
                "--strategies", "momentum,mean_reversion",
                "--symbols", "AAPL,MSFT,GOOGL",
            ],
        )

        # Should fail with missing dates
        assert result.exit_code != 0

    def test_compare_invalid_strategy(self):
        """compare command rejects invalid strategy names."""
        result = runner.invoke(
            app,
            [
                "backtest", "compare",
                "--strategies", "momentum,nonexistent_strategy",
                "--symbols", "AAPL,MSFT,GOOGL",
                "--start-date", "2024-01-01",
                "--end-date", "2024-12-01",
            ],
        )

        # Should fail with unknown strategy
        assert result.exit_code != 0
        assert "unknown" in result.output.lower() or "invalid" in result.output.lower()


class TestBacktestCompareOutput:
    """Tests for compare command output format."""

    def test_compare_json_output_structure(self, monkeypatch):
        """compare command with --json returns structured JSON."""
        import json

        # Mock the comparison function to avoid DB dependency
        mock_results = {
            "momentum": {
                "total_return": 0.123,
                "sharpe_ratio": 1.24,
                "max_drawdown": -0.152,
                "sortino_ratio": 1.56,
                "calmar_ratio": 0.81,
                "win_rate": 0.58,
                "profit_factor": 1.85,
                "total_trades": 42,
            },
            "mean_reversion": {
                "total_return": 0.087,
                "sharpe_ratio": 0.95,
                "max_drawdown": -0.184,
                "sortino_ratio": 1.12,
                "calmar_ratio": 0.47,
                "win_rate": 0.52,
                "profit_factor": 1.42,
                "total_trades": 128,
            },
        }

        def mock_compare(*args, **kwargs):
            return mock_results

        # Monkeypatch the compare function
        from gefion.backtest import comparison
        monkeypatch.setattr(comparison, "compare_strategies", mock_compare)

        # Also mock price data loading
        def mock_load_price_data(*args, **kwargs):
            return _create_sample_price_data()

        from gefion.backtest import data_loader
        monkeypatch.setattr(data_loader, "load_price_data_for_backtest", mock_load_price_data)

        result = runner.invoke(
            app,
            [
                "backtest", "compare",
                "--strategies", "momentum,mean_reversion",
                "--symbols", "AAPL,MSFT,GOOGL",
                "--start-date", "2024-01-01",
                "--end-date", "2024-12-01",
                "--json",
            ],
        )

        assert result.exit_code == 0

        # Parse JSON output
        output = parse_json_output(result.output)

        # Check structure
        assert "comparison" in output
        assert "momentum" in output["comparison"]
        assert "mean_reversion" in output["comparison"]

        # Check metrics present
        assert "total_return" in output["comparison"]["momentum"]
        assert "sharpe_ratio" in output["comparison"]["momentum"]


class TestBacktestCompareMetrics:
    """Tests for metrics in compare output."""

    def test_compare_includes_extended_metrics(self, monkeypatch):
        """compare output includes extended metrics (Sortino, Calmar, etc)."""
        import json

        mock_results = {
            "momentum": {
                "total_return": 0.123,
                "sharpe_ratio": 1.24,
                "max_drawdown": -0.152,
                "sortino_ratio": 1.56,
                "calmar_ratio": 0.81,
                "win_rate": 0.58,
                "profit_factor": 1.85,
                "avg_win_loss_ratio": 1.42,
                "total_trades": 42,
            },
        }

        def mock_compare(*args, **kwargs):
            return mock_results

        from gefion.backtest import comparison
        monkeypatch.setattr(comparison, "compare_strategies", mock_compare)

        def mock_load_price_data(*args, **kwargs):
            return _create_sample_price_data()

        from gefion.backtest import data_loader
        monkeypatch.setattr(data_loader, "load_price_data_for_backtest", mock_load_price_data)

        result = runner.invoke(
            app,
            [
                "backtest", "compare",
                "--strategies", "momentum",
                "--symbols", "AAPL,MSFT,GOOGL",
                "--start-date", "2024-01-01",
                "--end-date", "2024-12-01",
                "--json",
            ],
        )

        assert result.exit_code == 0
        output = parse_json_output(result.output)

        metrics = output["comparison"]["momentum"]

        # Extended metrics
        assert "sortino_ratio" in metrics
        assert "calmar_ratio" in metrics
        assert "win_rate" in metrics
        assert "profit_factor" in metrics


class TestBacktestCompareRanking:
    """Tests for strategy ranking in compare output."""

    def test_compare_includes_ranking(self, monkeypatch):
        """compare output includes strategy ranking by Sharpe ratio."""
        import json

        mock_results = {
            "momentum": {"sharpe_ratio": 1.24, "total_return": 0.10},
            "mean_reversion": {"sharpe_ratio": 0.95, "total_return": 0.08},
            "breakout": {"sharpe_ratio": 1.41, "total_return": 0.15},
        }

        def mock_compare(*args, **kwargs):
            return mock_results

        from gefion.backtest import comparison
        monkeypatch.setattr(comparison, "compare_strategies", mock_compare)

        def mock_load_price_data(*args, **kwargs):
            return _create_sample_price_data()

        from gefion.backtest import data_loader
        monkeypatch.setattr(data_loader, "load_price_data_for_backtest", mock_load_price_data)

        result = runner.invoke(
            app,
            [
                "backtest", "compare",
                "--strategies", "momentum,mean_reversion,breakout",
                "--symbols", "AAPL,MSFT,GOOGL",
                "--start-date", "2024-01-01",
                "--end-date", "2024-12-01",
                "--json",
            ],
        )

        assert result.exit_code == 0
        output = parse_json_output(result.output)

        # Should include ranking
        assert "ranking" in output

        # Breakout should be first (highest Sharpe)
        assert output["ranking"][0]["strategy"] == "breakout"
        assert output["ranking"][1]["strategy"] == "momentum"
        assert output["ranking"][2]["strategy"] == "mean_reversion"


class TestBacktestCompareAllStrategies:
    """Tests for comparing all available strategies."""

    def test_compare_all_strategies_option(self, monkeypatch):
        """compare with --all compares all non-ML strategies (ML strategies skipped without model params)."""
        import json

        # Mock to return all strategies
        def mock_compare(strategies, *args, **kwargs):
            return {name: {"sharpe_ratio": 1.0} for name in strategies}

        from gefion.backtest import comparison
        from gefion.backtest.comparison import AVAILABLE_STRATEGIES
        monkeypatch.setattr(comparison, "compare_strategies", mock_compare)

        def mock_load_price_data(*args, **kwargs):
            return _create_sample_price_data(days=60)

        from gefion.backtest import data_loader
        monkeypatch.setattr(data_loader, "load_price_data_for_backtest", mock_load_price_data)

        result = runner.invoke(
            app,
            [
                "backtest", "compare",
                "--all",
                "--symbols", "AAPL,MSFT,GOOGL",
                "--start-date", "2024-01-01",
                "--end-date", "2024-12-01",
                "--json",
            ],
        )

        assert result.exit_code == 0
        output = parse_json_output(result.output)

        # Should have results for all non-ML strategies (ML strategies skipped without model params)
        ml_strategies = {"ml_signal", "ml_filter"}
        for strategy_name in AVAILABLE_STRATEGIES.keys():
            if strategy_name not in ml_strategies:
                assert strategy_name in output["comparison"]

        # ML strategies should be skipped (not in output) since no model params provided
        for ml_strat in ml_strategies:
            assert ml_strat not in output["comparison"]


# Helper functions

def _create_sample_price_data(days=30):
    """Create sample price data for testing."""
    symbols = ["AAPL", "MSFT", "GOOGL"]
    today = date.today()
    price_data = []

    for symbol in symbols:
        base_price = 100.0 + hash(symbol) % 200

        for i in range(days):
            test_date = today - timedelta(days=days - 1 - i)
            price = base_price * (1 + 0.001 * i)

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
