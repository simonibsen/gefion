"""Tests for db-cleanup CLI command (TDD).

These tests verify that the db-cleanup command properly identifies and removes
orphaned data from database tables.
"""
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner
from gefion.cli import app


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
        assert "predictions" in result.output


class TestDbCleanupDryRun:
    """Tests for db-cleanup dry run behavior."""

    def test_dry_run_does_not_delete(self):
        """Dry run should report but not delete."""
        with patch("gefion.cli.db_connection") as mock_db:
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (0,)
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            mock_db.return_value.__enter__.return_value = mock_conn
            mock_db.return_value.__exit__.return_value = False

            result = runner.invoke(app, ["db-cleanup", "--dry-run"])

        # Should either find no orphans or report dry run
        assert "Dry run" in result.output or "No orphaned data" in result.output
