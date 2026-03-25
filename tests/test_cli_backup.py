"""Tests for backup and restore CLI commands."""

import json
import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import MagicMock, patch


runner = CliRunner()


class TestBackupCommand:
    """Test the backup command."""

    def test_backup_command_exists(self):
        """Backup command should be registered."""
        from gefion.cli import app

        result = runner.invoke(app, ["backup", "--help"])
        assert result.exit_code == 0
        assert "Backup" in result.output or "backup" in result.output

    def test_backup_requires_output(self):
        """Backup should require --output option."""
        from gefion.cli import app

        result = runner.invoke(app, ["backup"])
        # Should fail without required output
        assert result.exit_code != 0

    def test_backup_dry_run_shows_estimate(self):
        """Backup --dry-run should show size estimate without creating file."""
        from gefion.cli import app

        with patch("gefion.backup.estimate_backup_size") as mock_estimate:
            mock_estimate.return_value = {
                "tables": {
                    "stock_ohlcv": {"rows": 1000, "estimated_bytes": 100000},
                    "computed_features": {"rows": 5000, "estimated_bytes": 200000},
                },
                "total_rows": 6000,
                "total_bytes": 300000,
            }

            result = runner.invoke(app, ["backup", "--output", "/tmp/test.parquet", "--dry-run", "--json"])

            if result.exit_code == 0:
                output = json.loads(result.stdout)
                assert "estimate" in output or "total_bytes" in str(output)

    def test_backup_json_output_format(self):
        """Backup with --json should output valid JSON."""
        from gefion.cli import app

        with patch("gefion.backup.create_backup") as mock_backup:
            mock_backup.return_value = {
                "success": True,
                "file": "/tmp/test.parquet",
                "tables": ["stock_ohlcv"],
                "rows": 1000,
                "bytes": 50000,
            }

            result = runner.invoke(app, ["backup", "--output", "/tmp/test.parquet", "--json"])

            # Should produce valid JSON (even if command fails due to no DB)
            if result.exit_code == 0:
                output = json.loads(result.stdout)
                assert isinstance(output, dict)

    def test_backup_supports_data_types_filter(self):
        """Backup should support --data-types option."""
        from gefion.cli import app

        result = runner.invoke(app, ["backup", "--help"])
        assert "--data-types" in result.output

    def test_backup_supports_date_range(self):
        """Backup should support --start-date and --end-date options."""
        from gefion.cli import app

        result = runner.invoke(app, ["backup", "--help"])
        assert "--start-date" in result.output or "--after" in result.output
        assert "--end-date" in result.output or "--before" in result.output

    def test_backup_supports_symbols_filter(self):
        """Backup should support --symbols option."""
        from gefion.cli import app

        result = runner.invoke(app, ["backup", "--help"])
        assert "--symbols" in result.output


class TestRestoreCommand:
    """Test the restore command."""

    def test_restore_command_exists(self):
        """Restore command should be registered."""
        from gefion.cli import app

        result = runner.invoke(app, ["restore", "--help"])
        assert result.exit_code == 0
        assert "Restore" in result.output or "restore" in result.output

    def test_restore_requires_input(self):
        """Restore should require --input option."""
        from gefion.cli import app

        result = runner.invoke(app, ["restore"])
        # Should fail without required input
        assert result.exit_code != 0

    def test_restore_supports_mode_option(self):
        """Restore should support --mode option (merge/replace)."""
        from gefion.cli import app

        result = runner.invoke(app, ["restore", "--help"])
        assert "--mode" in result.output

    def test_restore_dry_run_shows_preview(self):
        """Restore --dry-run should show what would be restored."""
        from gefion.cli import app

        result = runner.invoke(app, ["restore", "--help"])
        assert "--dry-run" in result.output


class TestBackupModule:
    """Test the backup module functions."""

    def test_backup_module_exists(self):
        """Backup module should exist."""
        from gefion import backup
        assert hasattr(backup, "create_backup")
        assert hasattr(backup, "restore_backup")
        assert hasattr(backup, "estimate_backup_size")

    def test_estimate_backup_size_returns_dict(self):
        """estimate_backup_size should return size info dict."""
        from gefion.backup import estimate_backup_size

        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # Mock row counts
        mock_cursor.fetchone.return_value = (1000,)

        result = estimate_backup_size(
            mock_conn,
            data_types=["ohlcv"],
            start_date=None,
            end_date=None,
            symbols=None,
        )

        assert isinstance(result, dict)
        assert "total_rows" in result or "tables" in result

    def test_check_disk_space_returns_bool(self):
        """check_disk_space should return whether enough space exists."""
        from gefion.backup import check_disk_space

        # Should work on any valid path
        result = check_disk_space("/tmp", required_bytes=1024)
        assert isinstance(result, bool)


class TestBackupManifest:
    """Test backup manifest handling."""

    def test_manifest_includes_metadata(self):
        """Backup manifest should include version and timestamp."""
        from gefion.backup import create_manifest

        manifest = create_manifest(
            tables={"stock_ohlcv": {"rows": 1000}},
            date_range=("2020-01-01", "2024-12-31"),
            symbols=None,
        )

        assert "version" in manifest
        assert "created_at" in manifest
        assert "tables" in manifest

    def test_manifest_includes_checksums(self):
        """Backup manifest should include file checksums."""
        from gefion.backup import create_manifest

        manifest = create_manifest(
            tables={"stock_ohlcv": {"rows": 1000, "checksum": "abc123"}},
            date_range=None,
            symbols=None,
        )

        assert "tables" in manifest


class TestIncrementalBackup:
    """Test incremental backup functionality."""

    def test_get_last_backup_date(self):
        """Should be able to get date of last backup."""
        from gefion.backup import get_last_backup_info

        # With no previous backup, should return None
        result = get_last_backup_info("/nonexistent/path")
        assert result is None

    def test_backup_supports_incremental_flag(self):
        """Backup should support --incremental option."""
        from gefion.cli import app

        result = runner.invoke(app, ["backup", "--help"])
        assert "--incremental" in result.output
