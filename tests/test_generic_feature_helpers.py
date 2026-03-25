"""
Test generic feature helper functions (TDD for refactoring indicator-specific code).

Tests for:
- latest_feature_date() - generic version of latest_indicator_date()
- filter_symbols_needing_features() - generic version of filter_symbols_needing_indicators()
"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch


def test_latest_feature_date_with_indicator_function():
    """Test latest_feature_date() works for function_name='indicator'."""
    from gefion.db.ingest import latest_feature_date

    # Mock connection
    conn = MagicMock()
    cursor_mock = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    # Mock result: latest date for indicator features
    cursor_mock.fetchone.return_value = (date(2024, 1, 15),)

    result = latest_feature_date(conn, data_id=10, function_name='indicator')

    assert result == date(2024, 1, 15)
    cursor_mock.execute.assert_called_once()


def test_latest_feature_date_with_derivative_function():
    """Test latest_feature_date() works for function_name='derivative'."""
    from gefion.db.ingest import latest_feature_date

    conn = MagicMock()
    cursor_mock = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    cursor_mock.fetchone.return_value = (date(2024, 1, 20),)

    result = latest_feature_date(conn, data_id=10, function_name='derivative')

    assert result == date(2024, 1, 20)


def test_latest_feature_date_with_no_data():
    """Test latest_feature_date() returns None when no data exists."""
    from gefion.db.ingest import latest_feature_date

    conn = MagicMock()
    cursor_mock = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    cursor_mock.fetchone.return_value = (None,)

    result = latest_feature_date(conn, data_id=10, function_name='indicator')

    assert result is None


def test_latest_feature_date_defaults_to_all_functions():
    """Test latest_feature_date() with function_name=None gets latest across all functions."""
    from gefion.db.ingest import latest_feature_date

    conn = MagicMock()
    cursor_mock = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    cursor_mock.fetchone.return_value = (date(2024, 1, 25),)

    result = latest_feature_date(conn, data_id=10, function_name=None)

    assert result == date(2024, 1, 25)


def test_filter_symbols_needing_features_with_indicators():
    """Test filter_symbols_needing_features() for indicator features."""
    from gefion.db.ingest import filter_symbols_needing_features

    conn = MagicMock()
    cursor_mock = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    # Mock: AAPL needs update (old data), MSFT is up-to-date
    cursor_mock.fetchall.return_value = [
        ("AAPL",),  # Only AAPL needs update
    ]

    symbols = ["AAPL", "MSFT", "GOOGL"]
    target_date = date(2024, 1, 20)

    result = filter_symbols_needing_features(
        conn,
        symbols,
        target_date,
        function_name='indicator'
    )

    assert result == ["AAPL"]


def test_filter_symbols_needing_features_with_derivatives():
    """Test filter_symbols_needing_features() for derivative features."""
    from gefion.db.ingest import filter_symbols_needing_features

    conn = MagicMock()
    cursor_mock = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    cursor_mock.fetchall.return_value = [
        ("AAPL",),
        ("MSFT",),
    ]

    symbols = ["AAPL", "MSFT", "GOOGL"]
    target_date = date(2024, 1, 20)

    result = filter_symbols_needing_features(
        conn,
        symbols,
        target_date,
        function_name='derivative'
    )

    assert result == ["AAPL", "MSFT"]


def test_filter_symbols_needing_features_all_up_to_date():
    """Test filter_symbols_needing_features() when all symbols are up-to-date."""
    from gefion.db.ingest import filter_symbols_needing_features

    conn = MagicMock()
    cursor_mock = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    cursor_mock.fetchall.return_value = []  # No symbols need update

    symbols = ["AAPL", "MSFT"]
    target_date = date(2024, 1, 20)

    result = filter_symbols_needing_features(conn, symbols, target_date, function_name='indicator')

    assert result == []
