"""
Database integration tests for ML dataset export.

These tests run against a real database to catch schema mismatches
like missing columns that mock tests can't detect.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os

import pytest


@pytest.fixture
def db_conn():
    """Get real database connection."""
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")

    from g2.cli_helpers import db_connection

    url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    with db_connection(url) as conn:
        yield conn


def test_export_with_exchange_limit_uses_valid_schema(db_conn, tmp_path):
    """Test that exchange+limit resolution uses valid database schema.

    This test catches issues like referencing non-existent columns
    (e.g., stocks.exchange) that mock tests miss.
    """
    from g2.ml.dataset import export_dataset_artifacts

    # Test with exchange+limit - this should not raise SQL errors
    manifest = {
        "universe": {"exchange": "NASDAQ", "limit": 5},
        "horizons_days": [],
        "format": "csv",
    }

    # Should not raise - if stocks.exchange doesn't exist, this would fail
    export_dataset_artifacts(db_conn, manifest=manifest, out_dir=tmp_path)

    # Verify files were created
    assert (tmp_path / "prices.csv").exists()
    assert (tmp_path / "features.csv").exists()


def test_export_with_symbols_uses_valid_schema(db_conn, tmp_path):
    """Test that explicit symbol list uses valid schema."""
    from g2.ml.dataset import export_dataset_artifacts

    # First, get a real symbol from the database
    with db_conn.cursor() as cur:
        cur.execute("SELECT symbol FROM stocks LIMIT 1")
        row = cur.fetchone()
        if not row:
            pytest.skip("No stocks in database")
        symbol = row[0]

    manifest = {
        "universe": {"symbols": [symbol]},
        "horizons_days": [],
        "format": "csv",
    }

    export_dataset_artifacts(db_conn, manifest=manifest, out_dir=tmp_path)

    assert (tmp_path / "prices.csv").exists()
    assert (tmp_path / "features.csv").exists()


def test_stocks_table_schema_documented(db_conn):
    """Document the actual stocks table schema for reference.

    This test ensures we know what columns exist and catches
    any assumptions about columns that don't exist.
    """
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'stocks'
            ORDER BY ordinal_position
        """)
        columns = [row[0] for row in cur.fetchall()]

    # Document expected columns - update if schema changes
    # Note: 'exchange' is NOT in this list (it doesn't exist)
    expected_core_columns = {"id", "symbol"}
    assert expected_core_columns.issubset(set(columns)), (
        f"Missing core columns. Found: {columns}"
    )

    # Explicitly verify exchange does NOT exist (to document this fact)
    assert "exchange" not in columns, (
        "stocks.exchange now exists - update code that works around its absence"
    )
