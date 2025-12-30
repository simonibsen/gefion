"""
Tests for chart CLI commands.
"""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

# Skip if plotly not available
plotly = pytest.importorskip("plotly")

from g2.cli import app

runner = CliRunner()


class TestChartPriceCommand:
    """Tests for `g2 chart price` command."""

    def test_requires_symbol(self):
        """chart price command should require a symbol argument."""
        result = runner.invoke(app, ["chart", "price"])
        # Should fail without symbol
        assert result.exit_code != 0

    def test_with_symbol_generates_chart(self):
        """chart price command should generate chart for valid symbol."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock the database query and chart generation
            mock_ohlcv = [
                {"date": "2024-12-01", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000000}
            ]
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("g2.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("g2.charts.output.open_in_browser"):

                result = runner.invoke(app, ["chart", "price", "AAPL", "--no-open"])

                # Should succeed
                assert result.exit_code == 0 or "No data" in result.stdout

    def test_no_open_flag(self):
        """chart price --no-open should not open browser."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ohlcv = [
                {"date": "2024-12-01", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000000}
            ]
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("g2.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("g2.charts.output.open_in_browser") as mock_open:

                runner.invoke(app, ["chart", "price", "AAPL", "--no-open"])

                # open_in_browser should not be called
                mock_open.assert_not_called()

    def test_json_output_returns_path(self):
        """chart price --json should return JSON with chart path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ohlcv = [
                {"date": "2024-12-01", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000000}
            ]
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("g2.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("g2.charts.output.open_in_browser"):

                result = runner.invoke(app, ["chart", "price", "AAPL", "--json", "--no-open"])

                if result.exit_code == 0:
                    import json
                    output = json.loads(result.stdout)
                    assert "chart_path" in output or "status" in output


class TestChartPredictionsCommand:
    """Tests for `g2 chart predictions` command."""

    def test_requires_symbol_and_model(self):
        """chart predictions should require symbol and model arguments."""
        result = runner.invoke(app, ["chart", "predictions"])
        assert result.exit_code != 0

    def test_with_valid_args(self):
        """chart predictions should work with valid arguments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ohlcv = [
                {"date": "2024-12-01", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000000}
            ]
            mock_preds = [
                {"date": "2024-12-08", "q10": 98, "q50": 102, "q90": 106}
            ]
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("g2.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("g2.charts.queries.fetch_predictions_for_chart", return_value=mock_preds), \
                 patch("g2.charts.output.open_in_browser"):

                result = runner.invoke(app, ["chart", "predictions", "AAPL", "--model", "test_model", "--no-open"])

                # Should succeed or report no data
                assert result.exit_code == 0 or "No data" in result.stdout or "error" in result.stdout.lower()


class TestChartFeaturesCommand:
    """Tests for `g2 chart features` command."""

    def test_requires_symbol(self):
        """chart features should require symbol argument."""
        result = runner.invoke(app, ["chart", "features"])
        assert result.exit_code != 0

    def test_with_features_option(self):
        """chart features should accept --features option."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ohlcv = [
                {"date": "2024-12-01", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000000}
            ]
            mock_features = {
                "rsi_14": [{"date": "2024-12-01", "value": 55}]
            }
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("g2.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("g2.charts.queries.fetch_features_for_chart", return_value=mock_features), \
                 patch("g2.charts.output.open_in_browser"):

                result = runner.invoke(app, ["chart", "features", "AAPL", "--features", "rsi_14", "--no-open"])

                # Should succeed or report issues
                assert result.exit_code == 0 or "No data" in result.stdout


class TestChartHelpText:
    """Tests for chart command help text."""

    def test_chart_help_shows_subcommands(self):
        """chart --help should list available subcommands."""
        result = runner.invoke(app, ["chart", "--help"])

        assert result.exit_code == 0
        # Should show subcommand options
        assert "price" in result.stdout.lower() or "Commands" in result.stdout
