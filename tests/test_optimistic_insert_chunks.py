"""
Test optimistic insert with automatic chunk creation.

Tests that insert_computed_features uses optimistic insert:
1. Try insert first (fast path)
2. Only create chunks if chunk-not-found error
3. Retry insert after creating chunks
4. Propagate other errors correctly
"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch, call
import psycopg.errors

from gefion.db.ingest import insert_computed_features


def test_optimistic_insert_fast_path_no_chunk_creation():
    """Test that successful insert does NOT create chunks (fast path)."""
    # Mock connection
    conn = MagicMock()
    conn.autocommit = False

    # Mock cursor that succeeds immediately
    cursor_mock = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    # Test data
    rows = [
        {"date": date(2024, 1, 1), "rsi": 50.0, "macd": 0.5},
        {"date": date(2024, 1, 2), "rsi": 55.0, "macd": 0.6},
    ]
    feature_map = {"rsi": 1, "macd": 2}

    with patch("gefion.db.pool.should_prepare_statements", return_value=False):
        with patch("gefion.utils.timescale.ensure_chunks_for_date_range") as mock_ensure:
            # Call insert
            result = insert_computed_features(
                conn=conn,
                data_id=10,
                rows=rows,
                feature_map=feature_map,
                batch_size=200,
            )

    # Verify insert succeeded
    assert result == 4  # 2 rows * 2 features

    # CRITICAL: ensure_chunks should NOT be called (optimistic fast path)
    mock_ensure.assert_not_called()

    # Verify commit was called
    conn.commit.assert_called()


def test_optimistic_insert_slow_path_creates_chunks_on_error():
    """Test that chunk-not-found error triggers chunk creation and retry."""
    # Mock connection
    conn = MagicMock()
    conn.autocommit = False
    conn.info.dsn = "postgresql://test:test@localhost/testdb"

    # Mock cursor that fails first time, succeeds second time
    cursor_mock = MagicMock()

    # Track call count
    call_count = [0]

    def execute_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: simulate chunk-not-found error
            raise psycopg.errors.InternalError("no chunks found for insert")
        # Second call: succeed
        return None

    cursor_mock.execute.side_effect = execute_side_effect
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    # Test data
    rows = [
        {"date": date(2020, 1, 1), "rsi": 50.0},  # Old date, might not have chunk
    ]
    feature_map = {"rsi": 1}

    with patch("gefion.db.pool.should_prepare_statements", return_value=False):
        with patch("gefion.utils.timescale.ensure_chunks_for_date_range") as mock_ensure:
            with patch("warnings.warn") as mock_warn:
                # Mock psycopg.connect to return a mock connection for chunk creation
                with patch("psycopg.connect") as mock_connect:
                    chunk_conn = MagicMock()
                    chunk_conn.autocommit = False
                    mock_connect.return_value.__enter__.return_value = chunk_conn

                    # Call insert
                    result = insert_computed_features(
                        conn=conn,
                        data_id=10,
                        rows=rows,
                        feature_map=feature_map,
                        batch_size=200,
                    )

    # Verify insert succeeded after retry
    assert result == 1  # 1 row * 1 feature

    # CRITICAL: ensure_chunks SHOULD be called after first failure
    mock_ensure.assert_called_once()
    call_args = mock_ensure.call_args[0]
    assert call_args[1] == "computed_features"
    assert call_args[2] < date(2020, 1, 1)  # min_date - buffer
    assert call_args[3] > date(2020, 1, 1)  # max_date + buffer

    # Verify warning was issued
    mock_warn.assert_called_once()
    warn_msg = mock_warn.call_args[0][0]
    assert "Chunk not found" in warn_msg
    assert "2020-01-01" in warn_msg

    # Verify commit was called
    conn.commit.assert_called()


def test_optimistic_insert_propagates_non_chunk_errors():
    """Test that non-chunk errors are propagated correctly."""
    # Mock connection
    conn = MagicMock()
    conn.autocommit = False

    # Mock cursor that fails with non-chunk error
    cursor_mock = MagicMock()
    cursor_mock.execute.side_effect = psycopg.errors.UniqueViolation("duplicate key")
    conn.cursor.return_value.__enter__.return_value = cursor_mock

    # Test data
    rows = [{"date": date(2024, 1, 1), "rsi": 50.0}]
    feature_map = {"rsi": 1}

    with patch("gefion.db.pool.should_prepare_statements", return_value=False):
        with patch("gefion.utils.timescale.ensure_chunks_for_date_range") as mock_ensure:
            # Call insert - should raise exception
            with pytest.raises(Exception) as exc_info:
                insert_computed_features(
                    conn=conn,
                    data_id=10,
                    rows=rows,
                    feature_map=feature_map,
                    batch_size=200,
                )

    # Verify error contains debug info
    error_msg = str(exc_info.value)
    assert "insert_computed_features failed" in error_msg
    assert "sample_types" in error_msg

    # CRITICAL: ensure_chunks should NOT be called for non-chunk errors
    mock_ensure.assert_not_called()


def test_optimistic_insert_empty_data():
    """Test that empty data returns 0 without any operations."""
    conn = MagicMock()

    with patch("gefion.utils.timescale.ensure_chunks_for_date_range") as mock_ensure:
        result = insert_computed_features(
            conn=conn,
            data_id=10,
            rows=[],
            feature_map={"rsi": 1},
            batch_size=200,
        )

    # No operations should happen
    assert result == 0
    mock_ensure.assert_not_called()
    conn.commit.assert_not_called()
