"""Tests for g2 ml eval command."""
import pytest
from typer.testing import CliRunner

import g2.cli as cli


def test_ml_eval_requires_model_name():
    """Test that ml eval requires --model-name."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "eval"])
    assert result.exit_code != 0
    assert "model-name" in result.output.lower() or "required" in result.output.lower()


def test_ml_eval_requires_model_version():
    """Test that ml eval requires --model-version."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "eval", "--model-name", "test"])
    assert result.exit_code != 0


def test_ml_eval_requires_eval_period():
    """Test that ml eval requires --start-date and --end-date."""
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "eval", "--model-name", "test", "--model-version", "v1"])
    assert result.exit_code != 0


def test_ml_eval_calculates_quantile_calibration():
    """Test that ml eval calculates quantile calibration metrics."""
    # Should compute how often actual returns fall within predicted quantiles
    # e.g., q10 should have ~10% of actuals below it
    pass  # TODO: Implement with actual DB fixture


def test_ml_eval_calculates_quantile_loss():
    """Test that ml eval calculates pinball loss for quantile predictions."""
    pass  # TODO: Implement with actual DB fixture


def test_ml_eval_stores_performance_metrics():
    """Test that ml eval stores results in model_performance table."""
    # After eval, should have row in model_performance with calibration metrics
    pass  # TODO: Implement with actual DB fixture


def test_ml_eval_creates_run_record():
    """Test that ml eval creates a record in ml_runs table."""
    # Should create ml_runs row with run_type='eval'
    pass  # TODO: Implement with actual DB fixture


def test_ml_eval_handles_missing_outcomes():
    """Test that ml eval handles missing outcome data gracefully."""
    # If predictions exist but no actual returns yet (future dates), should handle
    pass  # TODO: Implement with actual DB fixture
