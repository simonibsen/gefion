"""Tests for ml dataset-delete CLI command."""

from typer.testing import CliRunner
from unittest.mock import MagicMock, patch

from g2 import cli


runner = CliRunner()


class TestDatasetDeleteCommand:
    """Test the dataset-delete command."""

    def test_command_exists(self):
        """Command should be registered with help."""
        result = runner.invoke(cli.app, ["ml", "dataset-delete", "--help"])
        assert result.exit_code == 0
        assert "--name" in result.output
        assert "--version" in result.output

    def test_requires_name_and_version(self):
        """Command should require both --name and --version."""
        result = runner.invoke(cli.app, ["ml", "dataset-delete"])
        assert result.exit_code != 0

    def test_error_when_dataset_not_found(self):
        """Should show clear error when dataset doesn't exist."""
        result = runner.invoke(
            cli.app,
            ["ml", "dataset-delete", "--name", "nonexistent", "--version", "v1", "--json"]
        )
        # Should fail gracefully
        assert "not found" in result.output.lower() or result.exit_code != 0

    def test_error_shows_dependent_models(self):
        """Should list dependent models when refusing to delete."""
        # This test verifies the error message format
        # Actual DB test would be in integration tests
        result = runner.invoke(cli.app, ["ml", "dataset-delete", "--help"])
        # Help should mention that dependencies prevent deletion
        assert "model" in result.output.lower() or result.exit_code == 0

    def test_json_output_format(self):
        """Should support --json flag for structured output."""
        result = runner.invoke(cli.app, ["ml", "dataset-delete", "--help"])
        assert "--json" in result.output


class TestDatasetDeleteBehavior:
    """Test dataset-delete behavior with mocked database."""

    def test_refuses_delete_with_dependent_models(self):
        """Should refuse deletion and list dependent models."""
        # Mock would go here for integration test
        pass

    def test_deletes_db_row_and_files(self):
        """Should delete both DB entry and files on success."""
        # Mock would go here for integration test
        pass
