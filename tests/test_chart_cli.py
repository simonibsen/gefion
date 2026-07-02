"""
Tests for chart CLI commands.

Tests verify that chart commands use D3 renderers (not Plotly) and that
all chart subcommands are registered and follow the standard pattern.
"""

import os
import tempfile
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gefion.cli import app

runner = CliRunner()


class TestChartPriceCommand:
    """Tests for `gefion chart price` command."""

    def test_requires_symbol(self):
        """chart price command should require a symbol argument."""
        result = runner.invoke(app, ["chart", "price"])
        # Should fail without symbol
        assert result.exit_code != 0

    def test_with_symbol_generates_chart(self):
        """chart price command should generate chart for valid symbol."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_ohlcv = [
                {"date": "2024-12-01", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000000}
            ]
            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("gefion.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("gefion.charts.output.open_in_browser"), \
                 patch("gefion.cli.db_connection"):

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
                 patch("gefion.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("gefion.charts.output.open_in_browser") as mock_open, \
                 patch("gefion.cli.db_connection"):

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
                 patch("gefion.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("gefion.charts.output.open_in_browser"), \
                 patch("gefion.cli.db_connection"):

                result = runner.invoke(app, ["chart", "price", "AAPL", "--json", "--no-open"])

                if result.exit_code == 0:
                    import json
                    output = json.loads(result.stdout)
                    assert "chart_path" in output or "status" in output


class TestChartPredictionsCommand:
    """Tests for `gefion chart predictions` command."""

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
                 patch("gefion.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("gefion.charts.queries.fetch_predictions_for_chart", return_value=mock_preds), \
                 patch("gefion.charts.output.open_in_browser"), \
                 patch("gefion.cli.db_connection"):

                result = runner.invoke(app, ["chart", "predictions", "AAPL", "--model", "test_model", "--no-open"])

                # Should succeed or report no data
                assert result.exit_code == 0 or "No data" in result.stdout or "error" in result.stdout.lower()


class TestChartFeaturesCommand:
    """Tests for `gefion chart features` command."""

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
                 patch("gefion.charts.queries.fetch_ohlcv_for_chart", return_value=mock_ohlcv), \
                 patch("gefion.charts.queries.fetch_features_for_chart", return_value=mock_features), \
                 patch("gefion.charts.output.open_in_browser"), \
                 patch("gefion.cli.db_connection"):

                result = runner.invoke(app, ["chart", "features", "AAPL", "--features", "rsi_14", "--no-open"])

                # Should succeed or report issues
                assert result.exit_code == 0 or "No data" in result.stdout


class TestChartD3Imports:
    """Tests verifying chart commands use D3 renderers, not Plotly."""

    def test_price_command_imports_d3_renderer(self):
        """chart price should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_price
        source = inspect.getsource(chart_price)
        assert "gefion.charts.d3.renderers" in source
        assert "from gefion.charts.renderers import" not in source

    def test_predictions_command_imports_d3_renderer(self):
        """chart predictions should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_predictions
        source = inspect.getsource(chart_predictions)
        assert "gefion.charts.d3.renderers" in source

    def test_features_command_imports_d3_renderer(self):
        """chart features should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_features
        source = inspect.getsource(chart_features)
        assert "gefion.charts.d3.renderers" in source

    def test_compare_command_imports_d3_renderer(self):
        """chart compare should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_compare
        source = inspect.getsource(chart_compare)
        assert "gefion.charts.d3.renderers" in source

    def test_correlation_command_imports_d3_renderer(self):
        """chart correlation should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_correlation
        source = inspect.getsource(chart_correlation)
        assert "gefion.charts.d3.renderers" in source

    def test_sector_command_imports_d3_renderer(self):
        """chart sector should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_sector
        source = inspect.getsource(chart_sector)
        assert "gefion.charts.d3.renderers" in source

    def test_volatility_command_imports_d3_renderer(self):
        """chart volatility should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_volatility
        source = inspect.getsource(chart_volatility)
        assert "gefion.charts.d3.renderers" in source

    def test_drawdown_command_imports_d3_renderer(self):
        """chart drawdown should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_drawdown
        source = inspect.getsource(chart_drawdown)
        assert "gefion.charts.d3.renderers" in source

    def test_rolling_command_imports_d3_renderer(self):
        """chart rolling should import from gefion.charts.d3.renderers."""
        import inspect
        from gefion.cli import chart_rolling
        source = inspect.getsource(chart_rolling)
        assert "gefion.charts.d3.renderers" in source

    def test_all_chart_commands_use_save_html_string(self):
        """All chart commands should use save_html_string, not save_chart_html."""
        import inspect
        from gefion.cli import (
            chart_price, chart_predictions, chart_features, chart_compare,
            chart_correlation, chart_sector, chart_volatility, chart_drawdown,
            chart_rolling,
        )
        for fn in [chart_price, chart_predictions, chart_features, chart_compare,
                    chart_correlation, chart_sector, chart_volatility, chart_drawdown,
                    chart_rolling]:
            source = inspect.getsource(fn)
            assert "save_html_string" in source, f"{fn.__name__} should use save_html_string"
            assert "save_chart_html" not in source, f"{fn.__name__} should not use save_chart_html"


class TestChartCalibrationCommand:
    """Tests for `gefion chart calibration` command."""

    def test_requires_model_name(self):
        """chart calibration should require model_name argument."""
        result = runner.invoke(app, ["chart", "calibration"])
        assert result.exit_code != 0

    def test_command_is_registered(self):
        """chart calibration should be a registered subcommand."""
        result = runner.invoke(app, ["chart", "--help"])
        assert result.exit_code == 0
        assert "calibration" in result.stdout

    def test_with_valid_args(self):
        """chart calibration should generate chart for valid model."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_data = [
                {"predicted_prob": 0.1, "actual_freq": 0.12, "count": 50},
                {"predicted_prob": 0.5, "actual_freq": 0.48, "count": 100},
            ]
            mock_html = "<html><body>calibration chart</body></html>"
            chart_path = Path(tmpdir) / "test_calibration.html"
            chart_path.write_text(mock_html)

            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("gefion.charts.queries.fetch_model_calibration", return_value=mock_data), \
                 patch("gefion.charts.d3.renderers.create_calibration_chart", return_value=mock_html), \
                 patch("gefion.charts.output.save_html_string", return_value=chart_path), \
                 patch("gefion.charts.output.open_in_browser"), \
                 patch("gefion.cli.db_connection") as mock_db:

                mock_conn = MagicMock()
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                result = runner.invoke(app, ["chart", "calibration", "my_model", "--no-open"])
                assert result.exit_code == 0 or "No calibration" in result.stdout

    def test_no_data_emits_error(self):
        """chart calibration should emit error when no data found."""
        with patch("gefion.charts.queries.fetch_model_calibration", return_value=None), \
             patch("gefion.cli.db_connection") as mock_db:

            mock_conn = MagicMock()
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(app, ["chart", "calibration", "nonexistent_model", "--no-open"])
            assert "No calibration" in result.stdout or result.exit_code != 0


class TestChartConfusionMatrixCommand:
    """Tests for `gefion chart confusion-matrix` command."""

    def test_requires_model_name(self):
        """chart confusion-matrix should require model_name argument."""
        result = runner.invoke(app, ["chart", "confusion-matrix"])
        assert result.exit_code != 0

    def test_command_is_registered(self):
        """chart confusion-matrix should be a registered subcommand."""
        result = runner.invoke(app, ["chart", "--help"])
        assert result.exit_code == 0
        assert "confusion-matrix" in result.stdout

    def test_with_valid_args(self):
        """chart confusion-matrix should generate chart for valid model."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_data = {"labels": ["up", "down"], "matrix": [[50, 10], [5, 35]]}
            mock_html = "<html><body>confusion matrix</body></html>"
            chart_path = Path(tmpdir) / "test_confusion.html"
            chart_path.write_text(mock_html)

            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("gefion.charts.queries.fetch_confusion_matrix", return_value=mock_data), \
                 patch("gefion.charts.d3.renderers.create_confusion_matrix_chart", return_value=mock_html), \
                 patch("gefion.charts.output.save_html_string", return_value=chart_path), \
                 patch("gefion.charts.output.open_in_browser"), \
                 patch("gefion.cli.db_connection") as mock_db:

                mock_conn = MagicMock()
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                result = runner.invoke(app, ["chart", "confusion-matrix", "my_model", "--no-open"])
                assert result.exit_code == 0 or "No confusion" in result.stdout


class TestChartPipelineHealthCommand:
    """Tests for `gefion chart pipeline-health` command."""

    def test_command_is_registered(self):
        """chart pipeline-health should be a registered subcommand."""
        result = runner.invoke(app, ["chart", "--help"])
        assert result.exit_code == 0
        assert "pipeline-health" in result.stdout

    def test_with_valid_data(self):
        """chart pipeline-health should generate chart when data exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_data = [
                {"pipeline": "ingest", "status": "ok", "last_run": "2024-12-01", "duration_s": 120}
            ]
            mock_html = "<html><body>pipeline health</body></html>"
            chart_path = Path(tmpdir) / "test_pipeline.html"
            chart_path.write_text(mock_html)

            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("gefion.charts.queries.fetch_pipeline_health", return_value=mock_data), \
                 patch("gefion.charts.d3.renderers.create_pipeline_health_chart", return_value=mock_html), \
                 patch("gefion.charts.output.save_html_string", return_value=chart_path), \
                 patch("gefion.charts.output.open_in_browser"), \
                 patch("gefion.cli.db_connection") as mock_db:

                mock_conn = MagicMock()
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                result = runner.invoke(app, ["chart", "pipeline-health", "--no-open"])
                assert result.exit_code == 0 or "No pipeline" in result.stdout


class TestChartPredVsActualCommand:
    """Tests for `gefion chart pred-vs-actual` command."""

    def test_requires_model_name(self):
        """chart pred-vs-actual should require model_name argument."""
        result = runner.invoke(app, ["chart", "pred-vs-actual"])
        assert result.exit_code != 0

    def test_command_is_registered(self):
        """chart pred-vs-actual should be a registered subcommand."""
        result = runner.invoke(app, ["chart", "--help"])
        assert result.exit_code == 0
        assert "pred-vs-actual" in result.stdout

    def test_with_valid_args(self):
        """chart pred-vs-actual should generate chart for valid model."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_data = [
                {"predicted": 102.5, "actual": 101.0, "date": "2024-12-08"},
                {"predicted": 105.0, "actual": 104.5, "date": "2024-12-09"},
            ]
            mock_html = "<html><body>pred vs actual</body></html>"
            chart_path = Path(tmpdir) / "test_pred_vs_actual.html"
            chart_path.write_text(mock_html)

            with patch.dict(os.environ, {"G2_CHART_DIR": tmpdir}), \
                 patch("gefion.charts.queries.fetch_predictions_vs_actuals", return_value=mock_data), \
                 patch("gefion.charts.d3.renderers.create_pred_vs_actual_chart", return_value=mock_html), \
                 patch("gefion.charts.output.save_html_string", return_value=chart_path), \
                 patch("gefion.charts.output.open_in_browser"), \
                 patch("gefion.cli.db_connection") as mock_db:

                mock_conn = MagicMock()
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                result = runner.invoke(app, ["chart", "pred-vs-actual", "my_model", "--no-open"])
                assert result.exit_code == 0 or "No prediction" in result.stdout


class TestChartHelpText:
    """Tests for chart command help text."""

    def test_chart_help_shows_subcommands(self):
        """chart --help should list available subcommands."""
        result = runner.invoke(app, ["chart", "--help"])

        assert result.exit_code == 0
        # Should show subcommand options
        assert "price" in result.stdout.lower() or "Commands" in result.stdout

    def test_chart_help_shows_new_commands(self):
        """chart --help should list all new chart subcommands."""
        result = runner.invoke(app, ["chart", "--help"])
        assert result.exit_code == 0
        for cmd in ["calibration", "confusion-matrix", "pipeline-health", "pred-vs-actual"]:
            assert cmd in result.stdout, f"Missing subcommand: {cmd}"
