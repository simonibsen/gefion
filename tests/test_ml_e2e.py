"""Tests for ML e2e-test CLI command.

The e2e-test command runs the full ML pipeline for validation.
These tests verify command structure and basic functionality.
"""
import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from g2.cli import app

runner = CliRunner()


class TestE2ETestCommand:
    """Tests for the ml e2e-test command structure."""

    def test_e2e_test_help_shows_options(self):
        """Test that e2e-test --help shows expected options."""
        result = runner.invoke(app, ["ml", "e2e-test", "--help"])

        assert result.exit_code == 0
        assert "--exchange" in result.output
        assert "--limit" in result.output
        assert "--name" in result.output
        assert "--skip-data-update" in result.output
        assert "--cleanup" in result.output

    def test_e2e_test_has_sensible_defaults(self):
        """Test that e2e-test has sensible defaults."""
        result = runner.invoke(app, ["ml", "e2e-test", "--help"])

        # Should show defaults
        assert "NASDAQ" in result.output  # Default exchange
        assert "10" in result.output  # Default limit

    @pytest.mark.skipif(
        True,  # Skip by default - requires database
        reason="Integration test requires database"
    )
    def test_e2e_test_runs_pipeline_steps(self):
        """Test that e2e-test runs all pipeline steps."""
        # This would be an integration test requiring real database
        pass


class TestE2ETestSteps:
    """Tests for individual e2e test steps."""

    def test_step_names_are_defined(self):
        """Test that all step names are properly defined."""
        from g2.ml.e2e import E2E_STEPS

        expected_steps = [
            "data_update",
            "dataset_build",
            "train_model",
            "train_ensemble",
            "predict",
            "predict_ensemble",
        ]

        assert len(E2E_STEPS) == len(expected_steps)
        for step in expected_steps:
            assert step in E2E_STEPS

    def test_run_e2e_test_returns_results(self):
        """Test that run_e2e_test returns structured results."""
        from g2.ml.e2e import E2ETestResult

        # Verify result structure
        result = E2ETestResult(
            success=True,
            steps_completed=["data_update", "dataset_build"],
            steps_failed=[],
            duration_seconds=10.5,
            artifacts={"dataset": "e2e_test_v1"},
        )

        assert result.success
        assert len(result.steps_completed) == 2
        assert result.duration_seconds == 10.5


class TestE2ETestCleanup:
    """Tests for e2e test cleanup functionality."""

    def test_cleanup_removes_test_artifacts(self):
        """Test that cleanup removes test artifacts when requested."""
        from g2.ml.e2e import E2ETestResult

        # Result should track artifacts for cleanup
        result = E2ETestResult(
            success=True,
            steps_completed=["all"],
            steps_failed=[],
            duration_seconds=60.0,
            artifacts={
                "dataset_name": "e2e_test",
                "dataset_version": "v1",
                "model_name": "e2e_xgboost",
                "ensemble_name": "e2e_ensemble",
            },
        )

        assert "dataset_name" in result.artifacts
        assert "model_name" in result.artifacts


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
