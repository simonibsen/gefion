"""Tests for ml predict-list and predict-inspect commands."""

import pytest
from typer.testing import CliRunner

from gefion.cli import app


runner = CliRunner()


class TestPredictListCommand:
    """Test ml predict-list CLI command."""

    def test_command_exists(self):
        """predict-list command should be registered."""
        result = runner.invoke(app, ["ml", "predict-list", "--help"])
        assert result.exit_code == 0
        assert "--model-name" in result.output
        assert "--symbol" in result.output
        assert "--date" in result.output

    def test_accepts_json_output(self):
        """Command should accept --json flag."""
        result = runner.invoke(app, ["ml", "predict-list", "--help"])
        assert "--json" in result.output

    def test_lists_predictions(self, monkeypatch):
        """Should list predictions from database."""
        from datetime import date

        class DummyCursor:
            def execute(self, *args):
                pass

            def fetchall(self):
                # Return sample predictions from unified predictions table
                # Columns: name, version, symbol, prediction_date, horizon_days, prediction_type, prediction_values
                return [
                    ("quantile", "v1", "AAPL", date(2025, 12, 3), 7, "quantile", {"q10": 0.01, "q50": 0.02, "q90": 0.03}),
                    ("quantile", "v1", "AAPL", date(2025, 12, 3), 30, "quantile", {"q10": 0.02, "q50": 0.04, "q90": 0.06}),
                ]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class DummyConn:
            def cursor(self):
                return DummyCursor()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        from gefion import cli
        monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyConn())
        monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

        result = runner.invoke(app, ["ml", "predict-list", "--json"])

        assert result.exit_code == 0
        assert "AAPL" in result.output
        assert "quantile" in result.output


class TestPredictInspectCommand:
    """Test ml predict-inspect CLI command."""

    def test_command_exists(self):
        """predict-inspect command should be registered."""
        result = runner.invoke(app, ["ml", "predict-inspect", "--help"])
        assert result.exit_code == 0
        assert "--symbol" in result.output
        assert "--model-name" in result.output
        assert "--date" in result.output

    def test_requires_symbol(self):
        """Command should require --symbol option."""
        result = runner.invoke(app, ["ml", "predict-inspect"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_accepts_json_output(self):
        """Command should accept --json flag."""
        result = runner.invoke(app, ["ml", "predict-inspect", "--help"])
        assert "--json" in result.output

    def test_shows_symbol_not_found(self, monkeypatch):
        """Should report when symbol not found."""
        class DummyCursor:
            def execute(self, *args):
                pass

            def fetchone(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class DummyConn:
            def cursor(self):
                return DummyCursor()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        from gefion import cli
        monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyConn())
        monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

        result = runner.invoke(app, [
            "ml", "predict-inspect",
            "--symbol", "INVALID",
        ])

        assert "not found" in result.output.lower()

    def test_inspects_predictions(self, monkeypatch):
        """Should show predictions for a symbol."""
        from datetime import date, datetime

        class DummyCursor:
            def __init__(self):
                self._call_count = 0

            def execute(self, *args):
                self._call_count += 1

            def fetchone(self):
                if self._call_count == 1:
                    # Stock ID lookup
                    return (123,)
                elif "stock_ohlcv" in str(self._call_count):
                    # Price lookup
                    return (date(2025, 12, 3), 150.0, 150.0)
                return None

            def fetchall(self):
                # Predictions
                return [
                    ("quantile", "v1", date(2025, 12, 3), 7, 0.01, 0.02, 0.03, datetime.now()),
                    ("quantile", "v1", date(2025, 12, 3), 30, 0.02, 0.04, 0.06, datetime.now()),
                ]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class DummyConn:
            def cursor(self):
                return DummyCursor()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        from gefion import cli
        monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyConn())
        monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

        result = runner.invoke(app, [
            "ml", "predict-inspect",
            "--symbol", "AAPL",
            "--json",
        ])

        assert result.exit_code == 0
        assert "AAPL" in result.output
