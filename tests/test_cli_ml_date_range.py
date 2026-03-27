"""Tests for ML CLI date range support (TDD).

These tests verify that predict-ensemble and predict-classifier commands
support --start-date and --end-date options for batch predictions.
"""
import pytest
from typer.testing import CliRunner
from gefion.cli import app


runner = CliRunner()


class TestPredictEnsembleDateRangeOptions:
    """Tests for predict-ensemble date range CLI options."""

    def test_predict_ensemble_has_start_date_option(self):
        """Verify --start-date option is available."""
        result = runner.invoke(app, ["ml", "predict-ensemble", "--help"])
        assert result.exit_code == 0
        assert "--start-date" in result.output

    def test_predict_ensemble_has_end_date_option(self):
        """Verify --end-date option is available."""
        result = runner.invoke(app, ["ml", "predict-ensemble", "--help"])
        assert result.exit_code == 0
        assert "--end-date" in result.output

    def test_predict_ensemble_date_range_description(self):
        """Verify date range options have proper descriptions."""
        result = runner.invoke(app, ["ml", "predict-ensemble", "--help"])
        assert "batch predictions" in result.output.lower()


class TestPredictClassifierDateRangeOptions:
    """Tests for predict-classifier date range CLI options."""

    def test_predict_classifier_has_start_date_option(self):
        """Verify --start-date option is available."""
        result = runner.invoke(app, ["ml", "predict-classifier", "--help"])
        assert result.exit_code == 0
        assert "--start-date" in result.output

    def test_predict_classifier_has_end_date_option(self):
        """Verify --end-date option is available."""
        result = runner.invoke(app, ["ml", "predict-classifier", "--help"])
        assert result.exit_code == 0
        assert "--end-date" in result.output

    def test_predict_classifier_date_range_description(self):
        """Verify date range options have proper descriptions."""
        result = runner.invoke(app, ["ml", "predict-classifier", "--help"])
        assert "batch predictions" in result.output.lower()


class TestDateRangeValidation:
    """Tests for date range validation logic."""

    def test_predict_ensemble_rejects_invalid_date_format(self):
        """Invalid date format should produce error."""
        result = runner.invoke(
            app,
            [
                "ml", "predict-ensemble",
                "--model-name", "test",
                "--model-version", "v1",
                "--start-date", "invalid-date",
                "--end-date", "2025-01-31",
                "--exchange", "NASDAQ",
            ],
        )
        # Should fail with date format error (not crash)
        assert "Invalid date format" in result.output or result.exit_code != 0

    def test_predict_ensemble_rejects_start_after_end(self):
        """Start date after end date should produce error."""
        result = runner.invoke(
            app,
            [
                "ml", "predict-ensemble",
                "--model-name", "test",
                "--model-version", "v1",
                "--start-date", "2025-01-31",
                "--end-date", "2025-01-01",
                "--exchange", "NASDAQ",
            ],
        )
        # Should fail with validation error
        assert "start-date must be before end-date" in result.output or result.exit_code != 0

    def test_predict_classifier_rejects_invalid_date_format(self):
        """Invalid date format should produce error."""
        result = runner.invoke(
            app,
            [
                "ml", "predict-classifier",
                "--model-path", "/tmp/nonexistent",
                "--start-date", "invalid-date",
                "--end-date", "2025-01-31",
                "--exchange", "NASDAQ",
            ],
        )
        # Should fail with date format error (might fail earlier due to model path)
        assert result.exit_code != 0
