"""
TDD tests for generic feat-def-import CLI command.

Tests that feat-def-import can load ANY feature type (indicators, derivatives, etc.)
from JSON files without special-case handling.

Requires ENABLE_DB_TESTS=1 to run.
"""
import json
import os
from pathlib import Path
import pytest
import psycopg
import tempfile
import shutil
from typer.testing import CliRunner
from g2 import cli
from g2.config import load_settings
from g2.db import schema


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
def temp_feature_dir():
    """Create temporary directory with feature definition JSON files."""
    temp_dir = tempfile.mkdtemp()

    # Create indicator feature
    with open(Path(temp_dir) / "indicator_rsi_14.json", "w") as f:
        json.dump({
            "name": "indicator_rsi_14",
            "function_name": "indicator",
            "params": {"indicator": "rsi", "period": 14},
            "source_table": "stock_ohlcv",
            "source_column": "close",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True
        }, f)

    # Create derivative feature
    with open(Path(temp_dir) / "derivative_rsi_slope.json", "w") as f:
        json.dump({
            "name": "derivative_rsi_14_slope_5",
            "function_name": "derivative",
            "params": {"source_feature": "indicator_rsi_14", "window": 5},
            "source_table": "computed_features",
            "source_column": "value",
            "store_table": "computed_features",
            "store_column": "value",
            "store_type": "double precision",
            "active": True
        }, f)

    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def db_conn():
    """Create test database connection."""
    db_url = get_db_url()
    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def test_feat_def_import_imports_from_directory(db_conn, temp_feature_dir, monkeypatch):
    """Test that feat-def-import loads feature definitions from directory."""
    schema.create_stocks_table(db_conn)
    schema.create_feature_definitions_table(db_conn)
    monkeypatch.setenv("DATABASE_URL", get_db_url())

    result = runner.invoke(cli.app, ["feat-def-import", "--dir", temp_feature_dir])

    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    assert "Imported 2 definition(s)" in result.stdout

    # Verify in database
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM feature_definitions")
        count = cur.fetchone()[0]
        assert count == 2


def test_feat_def_import_handles_duplicate_definitions(db_conn, temp_feature_dir, monkeypatch):
    """Test that re-importing updates existing definitions."""
    schema.create_stocks_table(db_conn)
    schema.create_feature_definitions_table(db_conn)
    monkeypatch.setenv("DATABASE_URL", get_db_url())

    # Import once
    result1 = runner.invoke(cli.app, ["feat-def-import", "--dir", temp_feature_dir])
    assert result1.exit_code == 0

    # Import again (should update, not error)
    result2 = runner.invoke(cli.app, ["feat-def-import", "--dir", temp_feature_dir])
    assert result2.exit_code == 0

    # Should still have 2 definitions
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM feature_definitions")
        count = cur.fetchone()[0]
        assert count == 2


def test_feat_def_import_filters_by_feature_names(db_conn, temp_feature_dir, monkeypatch):
    """Test that feat-def-import can filter by specific feature names."""
    schema.create_stocks_table(db_conn)
    schema.create_feature_definitions_table(db_conn)
    monkeypatch.setenv("DATABASE_URL", get_db_url())

    # Import only indicator
    result = runner.invoke(cli.app, ["feat-def-import", "--dir", temp_feature_dir, "--features", "indicator_rsi_14"])

    assert result.exit_code == 0
    assert "Imported 1 definition(s)" in result.stdout

    # Verify only one in database
    with db_conn.cursor() as cur:
        cur.execute("SELECT name FROM feature_definitions")
        names = [row[0] for row in cur.fetchall()]
        assert names == ["indicator_rsi_14"]


def test_feat_def_import_works_for_multiple_feature_types(db_conn, temp_feature_dir, monkeypatch):
    """Test that feat-def-import works for ANY feature type without special handling."""
    schema.create_stocks_table(db_conn)
    schema.create_feature_definitions_table(db_conn)
    monkeypatch.setenv("DATABASE_URL", get_db_url())

    result = runner.invoke(cli.app, ["feat-def-import", "--dir", temp_feature_dir])

    assert result.exit_code == 0

    # Verify both indicator and derivative were imported
    with db_conn.cursor() as cur:
        cur.execute("SELECT name, function_name FROM feature_definitions ORDER BY name")
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0] == ("derivative_rsi_14_slope_5", "derivative")
        assert rows[1] == ("indicator_rsi_14", "indicator")
