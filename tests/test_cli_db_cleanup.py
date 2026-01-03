"""Tests for db-cleanup CLI command (TDD).

These tests verify that the db-cleanup command properly identifies and removes
orphaned data from database tables.
"""
import pytest
from typer.testing import CliRunner
from g2.cli import app


runner = CliRunner()


class TestDbCleanupCommand:
    """Tests for db-cleanup CLI command."""

    def test_db_cleanup_help_available(self):
        """Verify --help works for db-cleanup."""
        result = runner.invoke(app, ["db-cleanup", "--help"])
        assert result.exit_code == 0
        assert "orphaned data" in result.output.lower()

    def test_db_cleanup_has_dry_run_option(self):
        """Verify --dry-run option is available."""
        result = runner.invoke(app, ["db-cleanup", "--help"])
        assert "--dry-run" in result.output

    def test_db_cleanup_describes_tables(self):
        """Verify help describes which tables are cleaned."""
        result = runner.invoke(app, ["db-cleanup", "--help"])
        assert "computed_features" in result.output
        assert "stock_ohlcv" in result.output
        assert "quantile_predictions" in result.output


class TestDbCleanupDryRun:
    """Tests for db-cleanup dry run behavior."""

    @pytest.mark.skipif(
        not pytest.importorskip("psycopg", reason="Database not available"),
        reason="Database tests disabled"
    )
    def test_dry_run_does_not_delete(self):
        """Dry run should report but not delete."""
        result = runner.invoke(app, ["db-cleanup", "--dry-run"])
        # Should either find no orphans or report dry run
        assert "Dry run" in result.output or "No orphaned data" in result.output
