"""CLI tests for g2 ml calibrate command.

TDD: Written BEFORE the CLI command implementation.
"""
from typer.testing import CliRunner

import gefion.cli as cli

runner = CliRunner()


class TestCalibrateCLI:
    """Tests for g2 ml calibrate CLI command."""

    def test_calibrate_command_exists(self):
        """ml calibrate --help should return exit code 0."""
        result = runner.invoke(cli.app, ["ml", "calibrate", "--help"])
        assert result.exit_code == 0
        assert "calibrate" in result.output.lower() or "calibration" in result.output.lower()

    def test_calibrate_requires_model_name(self):
        """Should fail when --model-name is missing."""
        result = runner.invoke(cli.app, ["ml", "calibrate"])
        assert result.exit_code != 0
        assert "model-name" in result.output.lower() or "required" in result.output.lower()

    def test_calibrate_requires_date_range(self):
        """Should fail when --start-date or --end-date is missing."""
        result = runner.invoke(cli.app, [
            "ml", "calibrate",
            "--model-name", "test",
            "--model-version", "v1",
        ])
        assert result.exit_code != 0
        assert "start-date" in result.output.lower() or "required" in result.output.lower()
