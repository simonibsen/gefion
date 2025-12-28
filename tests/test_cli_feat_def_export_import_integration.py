"""
Integration tests for feat-def-export and feat-def-import CLI commands.

These tests require a running database with ENABLE_DB_TESTS=1.
"""
import json
import os
import psycopg
import pytest
from pathlib import Path
from typer.testing import CliRunner
from g2 import cli
from g2.config import load_settings
from g2.db import schema
from g2.db.ingest import ensure_feature_definitions, ensure_store_targets


# Skip all tests in this module if ENABLE_DB_TESTS is not set
pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run."
)


def get_db_url():
    """Get database URL from environment or settings."""
    settings = load_settings()
    return os.environ.get("DATABASE_URL", settings.database_url)


runner = CliRunner()


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = get_db_url()
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        # Ensure table exists before cleanup
        schema.create_feature_definitions_table(conn)
        # Clean up before tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM feature_definitions
                WHERE name LIKE 'cli_test_%'
            """)
        yield conn
        # Clean up after tests
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM feature_definitions
                WHERE name LIKE 'cli_test_%'
            """)


@pytest.fixture
def setup_test_data(db_conn):
    """Insert test feature definitions into database."""
    schema.create_feature_definitions_table(db_conn)
    schema.create_computed_features_table(db_conn)

    test_definitions = [
        {
            "name": "cli_test_feature1",
            "function_name": "indicator",
            "params": {"indicator": "rsi", "period": 14},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "cli_test_feature1",
            "store_type": "float",
            "active": True,
        },
        {
            "name": "cli_test_feature2",
            "function_name": "indicator",
            "params": {"indicator": "sma", "period": 20},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "cli_test_feature2",
            "store_type": "float",
            "active": True,
        },
    ]

    ensure_feature_definitions(db_conn, test_definitions)
    ensure_store_targets(db_conn, test_definitions)

    return test_definitions


def test_export_with_default_directory(setup_test_data, tmp_path, monkeypatch):
    """Test that feat-def-export uses default 'feature-definitions' directory."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["feat-def-export"])

    assert result.exit_code == 0
    assert "Exported" in result.stdout

    # Check default directory was created
    default_dir = tmp_path / "feature-definitions"
    assert default_dir.exists()

    # Check files exist (no version in filename)
    assert (default_dir / "cli_test_feature1.json").exists()
    assert (default_dir / "cli_test_feature2.json").exists()


def test_export_with_custom_directory(setup_test_data, tmp_path):
    """Test that feat-def-export works with custom directory."""
    export_dir = tmp_path / "custom-export"

    result = runner.invoke(cli.app, ["feat-def-export", "--dir", str(export_dir)])

    assert result.exit_code == 0
    assert "Exported" in result.stdout
    assert export_dir.exists()
    assert (export_dir / "cli_test_feature1.json").exists()


def test_export_filtered_features(setup_test_data, tmp_path, monkeypatch):
    """Test exporting specific features only."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["feat-def-export", "--features", "cli_test_feature1"])

    assert result.exit_code == 0
    assert "Exported 1" in result.stdout

    default_dir = tmp_path / "feature-definitions"
    assert (default_dir / "cli_test_feature1.json").exists()
    assert not (default_dir / "cli_test_feature2.json").exists()


def test_import_with_default_directory(db_conn, tmp_path, monkeypatch):
    """Test that feat-def-import uses default 'feature-definitions' directory."""
    monkeypatch.chdir(tmp_path)

    schema.create_feature_definitions_table(db_conn)
    schema.create_computed_features_table(db_conn)

    # Create default directory with test files
    feat_dir = tmp_path / "feature-definitions"
    feat_dir.mkdir()

    test_def = {
        "name": "cli_test_imported",
        "function_name": "indicator",
        "params": {"indicator": "macd"},
        "source_table": "stock_ohlcv",
        "source_column": "close",
        "store_table": "computed_features",
        "store_column": "cli_test_imported",
        "store_type": "float",
        "active": True,
    }

    (feat_dir / "cli_test_imported.json").write_text(json.dumps(test_def, indent=2))

    result = runner.invoke(cli.app, ["feat-def-import"])

    assert result.exit_code == 0
    assert "Imported 1" in result.stdout

    # Verify in database
    with db_conn.cursor() as cur:
        cur.execute("SELECT name FROM feature_definitions WHERE name = %s", ("cli_test_imported",))
        assert cur.fetchone() is not None


def test_import_with_custom_directory(db_conn, tmp_path):
    """Test feat-def-import with custom directory."""
    schema.create_feature_definitions_table(db_conn)
    schema.create_computed_features_table(db_conn)

    import_dir = tmp_path / "custom-import"
    import_dir.mkdir()

    test_def = {
        "name": "cli_test_custom_imported",
        "function_name": "indicator",
        "params": {"indicator": "bbands"},
        "source_table": "stock_ohlcv",
        "source_column": "close",
        "store_table": "computed_features",
        "store_column": "cli_test_custom_imported",
        "store_type": "float",
        "active": True,
    }

    (import_dir / "cli_test_custom_imported.json").write_text(json.dumps(test_def, indent=2))

    result = runner.invoke(cli.app, ["feat-def-import", "--dir", str(import_dir)])

    assert result.exit_code == 0
    assert "Imported 1" in result.stdout


def test_import_filtered_features(db_conn, tmp_path, monkeypatch):
    """Test importing specific features only."""
    monkeypatch.chdir(tmp_path)

    schema.create_feature_definitions_table(db_conn)
    schema.create_computed_features_table(db_conn)

    feat_dir = tmp_path / "feature-definitions"
    feat_dir.mkdir()

    def1 = {
        "name": "cli_test_import1",
        "function_name": "indicator",
        "params": {"indicator": "rsi", "period": 14},
        "source_table": "stock_ohlcv",
        "source_column": "close",
        "store_table": "computed_features",
        "store_column": "cli_test_import1",
        "store_type": "float",
        "active": True,
    }

    def2 = {
        "name": "cli_test_import2",
        "function_name": "indicator",
        "params": {"indicator": "sma", "period": 20},
        "source_table": "stock_ohlcv",
        "source_column": "close",
        "store_table": "computed_features",
        "store_column": "cli_test_import2",
        "store_type": "float",
        "active": True,
    }

    (feat_dir / "cli_test_import1.json").write_text(json.dumps(def1, indent=2))
    (feat_dir / "cli_test_import2.json").write_text(json.dumps(def2, indent=2))

    # Import only def1
    result = runner.invoke(cli.app, ["feat-def-import", "--features", "cli_test_import1"])

    assert result.exit_code == 0
    assert "Imported 1" in result.stdout

    # Verify only def1 is in database
    with db_conn.cursor() as cur:
        cur.execute("SELECT name FROM feature_definitions WHERE name LIKE 'cli_test_import%' ORDER BY name")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "cli_test_import1"


def test_import_is_idempotent_via_cli(db_conn, tmp_path, monkeypatch):
    """Test that running import twice doesn't cause errors."""
    monkeypatch.chdir(tmp_path)

    schema.create_feature_definitions_table(db_conn)
    schema.create_computed_features_table(db_conn)

    feat_dir = tmp_path / "feature-definitions"
    feat_dir.mkdir()

    test_def = {
        "name": "cli_test_idempotent",
        "function_name": "indicator",
        "params": {"indicator": "ema", "period": 12},
        "source_table": "stock_ohlcv",
        "source_column": "close",
        "store_table": "computed_features",
        "store_column": "cli_test_idempotent",
        "store_type": "float",
        "active": True,
    }

    (feat_dir / "cli_test_idempotent.json").write_text(json.dumps(test_def, indent=2))

    # First import
    result1 = runner.invoke(cli.app, ["feat-def-import"])
    assert result1.exit_code == 0
    assert "Imported 1" in result1.stdout

    # Second import - should succeed
    result2 = runner.invoke(cli.app, ["feat-def-import"])
    assert result2.exit_code == 0
    assert "Imported 1" in result2.stdout

    # Should still only have 1 definition
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM feature_definitions WHERE name = %s", ("cli_test_idempotent",))
        count = cur.fetchone()[0]
        assert count == 1


def test_export_import_roundtrip(setup_test_data, db_conn, tmp_path, monkeypatch):
    """Test that exporting and then importing preserves all data."""
    monkeypatch.chdir(tmp_path)

    # Export only test features
    result_export = runner.invoke(cli.app, ["feat-def-export", "--features", "cli_test_feature1,cli_test_feature2"])
    assert result_export.exit_code == 0

    # Clear database
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'cli_test_%'")

    # Verify it's empty
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM feature_definitions WHERE name LIKE 'cli_test_%'")
        assert cur.fetchone()[0] == 0

    # Import
    result_import = runner.invoke(cli.app, ["feat-def-import"])
    assert result_import.exit_code == 0
    assert "Imported 2" in result_import.stdout

    # Verify data is back
    with db_conn.cursor() as cur:
        cur.execute("SELECT name, function_name, active FROM feature_definitions WHERE name LIKE 'cli_test_%' ORDER BY name")
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "cli_test_feature1"
        assert rows[0][1] == "indicator"
        assert rows[0][2] is True
        assert rows[1][0] == "cli_test_feature2"


def test_import_from_nonexistent_directory(tmp_path, monkeypatch):
    """Test import gracefully handles nonexistent directory."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["feat-def-import", "--dir", "nonexistent"])

    assert result.exit_code == 0
    assert "No definitions found" in result.stdout
