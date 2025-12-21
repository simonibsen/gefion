"""
Test chunk range safety checks to prevent 'chunk not found' errors.

This tests the critical bug fix where insert_computed_features would fail with
"chunk not found" errors when trying to insert data outside the available chunk
date range in TimescaleDB hypertables.
"""
from datetime import date
from unittest.mock import MagicMock, patch
import warnings
import pytest

from g2.utils.timescale import (
    get_chunk_date_range,
    filter_rows_by_chunk_range,
    validate_and_filter_insert_data
)


def test_get_chunk_date_range():
    """
    Test that get_chunk_date_range correctly queries TimescaleDB metadata.
    """
    # Create a mock connection and cursor
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (date(2008, 1, 12), date(2025, 12, 31))
    mock_cur.__enter__ = lambda self: self
    mock_cur.__exit__ = lambda self, *args: None

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    min_date, max_date = get_chunk_date_range(mock_conn, "computed_features")

    assert min_date == date(2008, 1, 12)
    assert max_date == date(2025, 12, 31)

    # Verify the SQL was executed correctly
    mock_cur.execute.assert_called_once()
    call_args = mock_cur.execute.call_args
    assert "timescaledb_information.chunks" in call_args[0][0]
    assert call_args[0][1] == ("computed_features",)


def test_get_chunk_date_range_no_chunks():
    """
    Test get_chunk_date_range when no chunks exist.
    """
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (None, None)
    mock_cur.__enter__ = lambda self: self
    mock_cur.__exit__ = lambda self, *args: None

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    min_date, max_date = get_chunk_date_range(mock_conn, "computed_features")

    assert min_date is None
    assert max_date is None


def test_filter_rows_by_chunk_range_filters_old_dates():
    """
    Test that filter_rows_by_chunk_range filters out dates before chunk range.
    """
    rows = [
        {"date": date(1999, 11, 1), "value": 100},
        {"date": date(2000, 1, 1), "value": 200},
        {"date": date(2020, 1, 1), "value": 300},
    ]

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        filtered, skipped = filter_rows_by_chunk_range(
            rows, "date", date(2008, 1, 12), date(2025, 12, 31), warn_on_skip=True
        )

        # Should filter out the 1999 and 2000 dates
        assert len(filtered) == 1
        assert filtered[0]["date"] == date(2020, 1, 1)
        assert skipped == 2

        # Should emit a warning
        assert len(w) == 1
        assert "Skipped 2 rows" in str(w[0].message)
        assert "1999-11-01" in str(w[0].message)
        assert "2000-01-01" in str(w[0].message)


def test_filter_rows_by_chunk_range_filters_future_dates():
    """
    Test that filter_rows_by_chunk_range filters out dates after chunk range.
    """
    rows = [
        {"date": date(2020, 1, 1), "value": 100},
        {"date": date(2026, 1, 1), "value": 200},
        {"date": date(2027, 1, 1), "value": 300},
    ]

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        filtered, skipped = filter_rows_by_chunk_range(
            rows, "date", date(2008, 1, 12), date(2025, 12, 31), warn_on_skip=True
        )

        # Should filter out the 2026 and 2027 dates
        assert len(filtered) == 1
        assert filtered[0]["date"] == date(2020, 1, 1)
        assert skipped == 2

        # Should emit a warning
        assert len(w) == 1
        assert "Skipped 2 rows" in str(w[0].message)


def test_filter_rows_by_chunk_range_no_filtering_when_no_range():
    """
    Test that all rows pass through when chunk range is None.
    """
    rows = [
        {"date": date(1999, 11, 1), "value": 100},
        {"date": date(2020, 1, 1), "value": 200},
        {"date": date(2027, 1, 1), "value": 300},
    ]

    filtered, skipped = filter_rows_by_chunk_range(
        rows, "date", None, None, warn_on_skip=False
    )

    # All rows should pass through
    assert len(filtered) == 3
    assert skipped == 0


def test_filter_rows_by_chunk_range_handles_string_dates():
    """
    Test that filter_rows_by_chunk_range can parse ISO string dates.
    """
    rows = [
        {"date": "1999-11-01", "value": 100},
        {"date": "2020-01-01", "value": 200},
    ]

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        filtered, skipped = filter_rows_by_chunk_range(
            rows, "date", date(2008, 1, 12), date(2025, 12, 31), warn_on_skip=False
        )

        # Should filter out the 1999 date
        assert len(filtered) == 1
        assert filtered[0]["date"] == "2020-01-01"
        assert skipped == 1


def test_validate_and_filter_insert_data():
    """
    Test the convenience function that combines chunk detection and filtering.
    """
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (date(2008, 1, 12), date(2025, 12, 31))
    mock_cur.__enter__ = lambda self: self
    mock_cur.__exit__ = lambda self, *args: None

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    rows = [
        {"date": date(1999, 11, 1), "value": 100},
        {"date": date(2020, 1, 1), "value": 200},
        {"date": date(2021, 1, 1), "value": 300},
    ]

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        filtered, stats = validate_and_filter_insert_data(
            mock_conn, "computed_features", rows, warn_on_skip=False
        )

    assert stats["total_rows"] == 3
    assert stats["filtered_rows"] == 2
    assert stats["skipped_rows"] == 1
    assert stats["chunk_min"] == date(2008, 1, 12)
    assert stats["chunk_max"] == date(2025, 12, 31)
    assert len(filtered) == 2


def test_insert_computed_features_filters_outside_chunk_range(db_conn_for_tests):
    """
    Integration test: insert_computed_features should filter data outside chunk range.

    This test requires a real database connection with TimescaleDB.
    """
    from g2.db.ingest import insert_computed_features
    from g2.db import schema

    # Skip if no database connection available
    if not db_conn_for_tests:
        pytest.skip("No database connection available")

    conn = db_conn_for_tests

    # Ensure tables exist
    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)

    # Create test stock and feature
    from g2.db.ingest import upsert_stock, ensure_feature_definitions

    stock_id = upsert_stock(conn, "TEST_CHUNK")
    feature_map = ensure_feature_definitions(
        conn,
        [
            {
                "name": "test_chunk_feature",
                "function_name": "test",
                "params": {},
                "source_table": "stock_ohlcv",
                "source_column": "close",
                "store_table": "computed_features",
                "store_column": "value",
                "store_type": "double precision",
                "active": True,
            }
        ],
    )

    # Get chunk range
    from g2.utils.timescale import get_chunk_date_range

    min_date, max_date = get_chunk_date_range(conn, "computed_features")

    if not min_date or not max_date:
        pytest.skip("No chunks exist in computed_features")

    # Create test data with dates both inside and outside chunk range
    rows = [
        # Date before chunk range (should be filtered)
        {"date": min_date.replace(year=min_date.year - 10), "test_fx": 100.0},
        # Date within chunk range (should be inserted)
        {"date": min_date.replace(year=min_date.year + 1), "test_fx": 200.0},
        # Date within chunk range (should be inserted)
        {"date": max_date.replace(day=max_date.day - 1), "test_fx": 300.0},
    ]

    feature_map_for_insert = {"test_fx": feature_map["test_chunk_feature"]}

    # Insert with warnings captured
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        inserted = insert_computed_features(
            conn, stock_id, rows, feature_map_for_insert, batch_size=100
        )

        # Should have inserted only 2 rows (filtered out the old date)
        assert inserted == 2

        # Should have emitted a warning about skipped rows
        warning_messages = [str(warning.message) for warning in w]
        skipped_warnings = [msg for msg in warning_messages if "Skipped" in msg]
        assert len(skipped_warnings) >= 1
        assert "outside chunk range" in skipped_warnings[0]


@pytest.fixture
def db_conn_for_tests():
    """
    Fixture that provides a database connection for tests.

    Yields None if no database is available, causing tests to be skipped.
    """
    import os

    db_url = os.environ.get("G2_TEST_DB_URL")
    if not db_url:
        yield None
        return

    try:
        import psycopg

        conn = psycopg.connect(db_url)
        conn.autocommit = True
        yield conn
        conn.close()
    except Exception:
        yield None
