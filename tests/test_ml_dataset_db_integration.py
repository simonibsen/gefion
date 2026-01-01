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


def test_discover_feature_names_from_computed_features(db_conn):
    """Test that we can discover feature names from computed_features.

    This is used by dataset-build --export when --features is not specified.
    The query must match what exists in the database.
    """
    with db_conn.cursor() as cur:
        # This query is used by dataset-build to discover features
        cur.execute(
            """
            SELECT DISTINCT fd.name
            FROM computed_features cf
            JOIN feature_definitions fd ON fd.id = cf.feature_id
            ORDER BY fd.name
            LIMIT 10;
            """
        )
        features = [row[0] for row in cur.fetchall()]

    # Just verify the query works - may return empty on fresh DB
    assert isinstance(features, list)


def test_discovered_features_match_computed_features(db_conn):
    """Test that discovered feature names can be used to query back.

    Regression test: feature_names stored in ml_datasets must match
    what can be queried from computed_features for prediction.
    """
    with db_conn.cursor() as cur:
        # Get a symbol and feature that definitely exist together
        cur.execute(
            """
            SELECT s.symbol, fd.name, cf.date
            FROM computed_features cf
            JOIN feature_definitions fd ON fd.id = cf.feature_id
            JOIN stocks s ON s.id = cf.data_id
            LIMIT 1;
            """
        )
        row = cur.fetchone()
        if not row:
            pytest.skip("No computed features in database")

        symbol, feature_name, date = row

        # Now verify we can query back using these values
        # (this is what predict does)
        cur.execute(
            """
            SELECT s.id FROM stocks s WHERE s.symbol = %s;
            """,
            (symbol,),
        )
        data_id = cur.fetchone()[0]

        cur.execute(
            """
            SELECT cf.value
            FROM computed_features cf
            JOIN feature_definitions fd ON fd.id = cf.feature_id
            WHERE cf.data_id = %s
              AND cf.date = %s
              AND fd.name = %s;
            """,
            (data_id, date, feature_name),
        )
        result = cur.fetchone()
        assert result is not None, "Should find feature by name"


def test_upsert_ml_dataset_array_columns(db_conn):
    """Test that upsert_ml_dataset properly handles array columns.

    Regression test: horizons_days (integer[]) and feature_names (text[])
    are PostgreSQL array columns and should be passed as Python lists.
    """
    from g2.ml.store import upsert_ml_dataset

    payload = {
        "name": "test_jsonb",
        "version": "20251230",
        "universe": {"exchange": "NASDAQ", "limit": 5},
        "feature_names": ["indicator_rsi_14", "indicator_macd"],
        "lookback_days": 200,
        "horizons_days": [7, 30, 90],  # This is a list that must be wrapped
        "label_spec": {"type": "forward_return_5class", "thresholds": {}},
        "split_spec": {"type": "walk_forward"},
        "artifact_uri": "test/manifest.json",
        "checksum": "abc123",
    }

    # Should not raise DatatypeMismatch
    dataset_id = upsert_ml_dataset(db_conn, payload)
    assert dataset_id > 0

    # Verify the data was stored correctly
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT horizons_days, feature_names FROM ml_datasets WHERE id = %s",
            (dataset_id,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == [7, 30, 90]  # JSONB comes back as list
        assert row[1] == ["indicator_rsi_14", "indicator_macd"]

        # Cleanup
        cur.execute("DELETE FROM ml_datasets WHERE id = %s", (dataset_id,))
    db_conn.commit()
