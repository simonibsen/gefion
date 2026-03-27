"""Tests for g2 ml predict command."""
import pytest
from typer.testing import CliRunner

import gefion.cli as cli


def test_ml_predict_requires_model_name():
    """Test that ml predict requires --model-name."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "predict"])
    assert result.exit_code != 0
    assert "model-name" in result.output.lower() or "required" in result.output.lower()


def test_ml_predict_requires_model_version():
    """Test that ml predict requires --model-version."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "predict", "--model-name", "test"])
    assert result.exit_code != 0
    assert "model-version" in result.output.lower() or "required" in result.output.lower()


def test_ml_predict_accepts_symbols_or_exchange():
    """Test that ml predict accepts either --symbols or --exchange."""
    runner = CliRunner()
    # Should fail if neither is provided
    result = runner.invoke(
        cli.app,
        ["ml", "predict", "--model-name", "test", "--model-version", "v1", "--prediction-date", "2024-01-01"],
    )
    assert result.exit_code != 0


def test_ml_predict_stores_quantile_predictions():
    """Test that ml predict stores predictions in unified predictions table."""
    # After predict, should have rows in predictions with prediction_type='quantile'
    pass  # TODO: Implement with actual DB fixture


def test_ml_predict_stores_trend_predictions():
    """Test that ml predict stores trend classifications in unified predictions table."""
    # After predict, should have rows in predictions with prediction_type='trend_class'
    pass  # TODO: Implement with actual DB fixture


def test_ml_predict_creates_run_record():
    """Test that ml predict creates a record in ml_runs table."""
    # Should create ml_runs row with run_type='predict'
    pass  # TODO: Implement with actual DB fixture


def test_ml_predict_handles_missing_features():
    """Test that ml predict handles missing features gracefully."""
    # If features don't exist for a symbol/date, should skip or warn
    pass  # TODO: Implement with actual DB fixture


class TestPredictDateRange:
    """Tests for date range prediction functionality."""

    def test_accepts_start_and_end_date(self):
        """predict command should accept --start-date and --end-date options."""
        runner = CliRunner()
        result = runner.invoke(cli.app, ["ml", "predict", "--help"])
        assert result.exit_code == 0
        assert "--start-date" in result.output
        assert "--end-date" in result.output

    def _create_mock_db(self, monkeypatch):
        """Create mock database connection that passes model/dataset/symbol lookups."""
        # Shared state across cursor instances
        state = {"last_query": ""}

        class DummyCursor:
            def execute(self, query, *args):
                state["last_query"] = str(query)

            def fetchone(self):
                q = state["last_query"]
                if "ml_models" in q:
                    # Model: (id, dataset_id, artifact_uri, algorithm)
                    return (1, 1, "models/test", "xgboost")
                elif "ml_datasets" in q:
                    # get_ml_dataset uses WHERE name = %s AND version = %s
                    # CLI's direct query uses WHERE id = %s
                    if "WHERE id" in q:
                        # Direct CLI query: (name, version, feature_names, horizons_days)
                        return ("test_ds", "v1", ["feature1"], [7, 30])
                    else:
                        # get_ml_dataset call with empty name/version - return None
                        return None
                return None

            def fetchall(self):
                q = state["last_query"]
                if "stocks" in q:
                    return [(1, "AAPL")]
                return []

            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        class DummyConn:
            def cursor(self):
                return DummyCursor()
            def commit(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyConn())

    def test_requires_both_start_and_end_date(self, monkeypatch):
        """Should error if only one of start/end date is provided."""
        self._create_mock_db(monkeypatch)
        runner = CliRunner()

        # Only start-date
        result = runner.invoke(
            cli.app,
            ["ml", "predict", "--model-name", "test", "--model-version", "v1",
             "--start-date", "2025-01-01", "--symbols", "AAPL"],
        )
        assert result.exit_code != 0
        assert "both" in result.output.lower()

        # Only end-date
        result = runner.invoke(
            cli.app,
            ["ml", "predict", "--model-name", "test", "--model-version", "v1",
             "--end-date", "2025-01-31", "--symbols", "AAPL"],
        )
        assert result.exit_code != 0
        assert "both" in result.output.lower()

    def test_cannot_mix_prediction_date_with_range(self, monkeypatch):
        """Should error if --prediction-date is used with --start-date/--end-date."""
        self._create_mock_db(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            ["ml", "predict", "--model-name", "test", "--model-version", "v1",
             "--prediction-date", "2025-01-15",
             "--start-date", "2025-01-01", "--end-date", "2025-01-31",
             "--symbols", "AAPL"],
        )
        assert result.exit_code != 0
        assert "cannot" in result.output.lower()

    def test_start_date_must_be_before_end_date(self, monkeypatch):
        """Should error if start-date is after end-date."""
        self._create_mock_db(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            ["ml", "predict", "--model-name", "test", "--model-version", "v1",
             "--start-date", "2025-01-31", "--end-date", "2025-01-01",
             "--symbols", "AAPL"],
        )
        assert result.exit_code != 0
        assert "before" in result.output.lower()


class TestPredictListTypeFilter:
    """Tests for predict-list --type option."""

    def test_predict_list_has_type_option(self):
        """predict-list should accept a --type option for filtering prediction_type."""
        runner = CliRunner()
        result = runner.invoke(cli.app, ["ml", "predict-list", "--help"])
        assert result.exit_code == 0
        assert "--type" in result.output

    def test_predict_list_type_option_described(self):
        """--type option should mention prediction type in its help text."""
        runner = CliRunner()
        result = runner.invoke(cli.app, ["ml", "predict-list", "--help"])
        assert result.exit_code == 0
        assert "prediction" in result.output.lower()
