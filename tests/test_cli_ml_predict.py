"""Tests for g2 ml predict command."""
import pytest
from typer.testing import CliRunner

import g2.cli as cli


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
    """Test that ml predict stores predictions in quantile_predictions table."""
    # After predict, should have rows in quantile_predictions
    pass  # TODO: Implement with actual DB fixture


def test_ml_predict_stores_trend_predictions():
    """Test that ml predict stores trend classifications if model supports it."""
    # After predict, should have rows in trend_class_predictions
    pass  # TODO: Implement with actual DB fixture


def test_ml_predict_creates_run_record():
    """Test that ml predict creates a record in ml_runs table."""
    # Should create ml_runs row with run_type='predict'
    pass  # TODO: Implement with actual DB fixture


def test_ml_predict_handles_missing_features():
    """Test that ml predict handles missing features gracefully."""
    # If features don't exist for a symbol/date, should skip or warn
    pass  # TODO: Implement with actual DB fixture
