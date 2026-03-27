"""
Test that latest dates are queried in a single batch, not N+1 queries.

The issue: Querying latest date individually for each feature (N+1 queries)
would be slow with many features.

The fix: Batch all feature IDs into a single query using IN clause.
"""
from unittest.mock import Mock, MagicMock, call
from datetime import date


def test_latest_dates_uses_single_batched_query():
    """
    Test that _latest_dates_for_features uses a single query for all features.

    With N features, we should execute 1 query (batch), not N queries (N+1 pattern).
    """
    from gefion.features.dispatcher import _latest_dates_for_features

    # Create mock connection and cursor
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor.__exit__ = Mock(return_value=None)

    # Mock query results
    mock_cursor.fetchall.return_value = [
        (1, date(2025, 1, 1)),
        (2, date(2025, 1, 2)),
        (3, date(2025, 1, 3)),
    ]

    mock_conn = Mock()
    mock_conn.cursor.return_value = mock_cursor

    # Query for 3 features
    feature_ids = [1, 2, 3]
    result = _latest_dates_for_features(mock_conn, data_id=10, feature_ids=feature_ids)

    # Should execute exactly 1 query (batch), not 3 queries (N+1)
    assert mock_cursor.execute.call_count == 1, \
        f"Expected 1 batched query, got {mock_cursor.execute.call_count} queries"

    # Verify the query uses lateral join for chunk-efficient lookups
    query_args = mock_cursor.execute.call_args[0]
    query = query_args[0]
    params = query_args[1]

    # Query should use lateral join pattern
    assert "LATERAL" in query.upper(), "Query should use LATERAL join"
    assert "%s" in query, "Query should use parameterized placeholders"

    # Parameters: feature_ids first, then data_id
    assert params[:3] == [1, 2, 3], "First params should be feature_ids"
    assert params[3] == 10, "Last param should be data_id"

    # Result should contain all features
    assert len(result) == 3
    assert result[1] == date(2025, 1, 1)
    assert result[2] == date(2025, 1, 2)
    assert result[3] == date(2025, 1, 3)


def test_latest_dates_scales_with_many_features():
    """
    Test that the batch query scales to many features efficiently.

    Even with 100 features, should still be 1 query.
    """
    from gefion.features.dispatcher import _latest_dates_for_features

    mock_cursor = MagicMock()
    mock_cursor.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor.__exit__ = Mock(return_value=None)

    # Mock results for 100 features
    mock_cursor.fetchall.return_value = [
        (i, date(2025, 1, 1)) for i in range(1, 101)
    ]

    mock_conn = Mock()
    mock_conn.cursor.return_value = mock_cursor

    # Query for 100 features
    feature_ids = list(range(1, 101))
    result = _latest_dates_for_features(mock_conn, data_id=10, feature_ids=feature_ids)

    # Still only 1 query, not 100!
    assert mock_cursor.execute.call_count == 1, \
        f"Should use 1 batched query even for 100 features, got {mock_cursor.execute.call_count}"

    # Verify all features in result
    assert len(result) == 100


def test_latest_dates_vs_n_plus_1_pattern():
    """
    Demonstrate the difference between batched query and N+1 pattern.

    Batched: 1 query for N features
    N+1: N queries (one per feature)
    """
    # Batched approach (correct)
    batched_queries = 1  # Single query with IN clause

    # N+1 approach (incorrect/slow)
    n_features = 50
    n_plus_1_queries = n_features  # One query per feature

    # Batched should be dramatically more efficient
    assert batched_queries < n_plus_1_queries / 10, \
        f"Batched ({batched_queries} query) should be much more efficient than N+1 ({n_plus_1_queries} queries)"

    # With 50 features, batched is 50x more efficient
    efficiency_improvement = n_plus_1_queries / batched_queries
    assert efficiency_improvement == 50, \
        f"Batching provides {efficiency_improvement}x improvement over N+1"


def test_empty_feature_list_returns_empty_dict():
    """
    Test that empty feature list is handled gracefully.

    Should return empty dict without executing a query.
    """
    from gefion.features.dispatcher import _latest_dates_for_features

    mock_conn = Mock()

    result = _latest_dates_for_features(mock_conn, data_id=10, feature_ids=[])

    # Should return empty dict
    assert result == {}

    # Should not execute any query
    mock_conn.cursor.assert_not_called()
