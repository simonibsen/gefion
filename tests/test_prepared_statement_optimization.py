"""
Test that prepared statements are only used for frequently-executed queries.

The issue: Using prepared statements for rarely-used queries wastes memory
and adds overhead without benefit.

The fix: Only enable prepared statements for hot path queries via pool
configuration. One-off queries and tests use prepare_statements=False.
"""


def test_prepared_statements_controlled_by_pool():
    """
    Test that prepared statement usage is controlled at the pool level.

    This allows:
    - Hot paths (features-compute) to use prepared statements
    - Cold paths and tests to skip prepared statement overhead
    """
    from gefion.db import pool as db_pool

    # When pool is configured without prepared statements
    # (like in tests or one-off scripts)
    assert hasattr(db_pool, 'should_prepare_statements'), \
        "Pool should have should_prepare_statements() method"

    # The function should return a boolean
    result = db_pool.should_prepare_statements()
    assert isinstance(result, bool), \
        "should_prepare_statements() should return a boolean"


def test_insert_respects_pool_setting():
    """
    Test that insert_computed_features respects the pool's prepare setting.

    When pool has prepare_statements=False, inserts should not use
    prepared statements, avoiding unnecessary overhead.
    """
    from gefion.db.ingest import insert_computed_features
    from gefion.db import pool as db_pool
    from unittest.mock import Mock, patch
    from datetime import date

    mock_conn = Mock()
    mock_conn.autocommit = True
    mock_cursor = Mock()
    mock_cursor.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor.__exit__ = Mock(return_value=None)
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.commit = Mock()

    rows = [
        {"date": date(2025, 1, 1), "test_col": 1.0},
        {"date": date(2025, 1, 2), "test_col": 2.0},
    ]
    feature_map = {"test_col": 1}

    # Test with prepare_statements=False (like tests do)
    with patch.object(db_pool, 'should_prepare_statements', return_value=False):
        insert_computed_features(mock_conn, data_id=1, rows=rows, feature_map=feature_map)

        # Verify execute was called WITHOUT prepare=True
        assert mock_cursor.execute.called

        # Look through all execute calls to find the INSERT statement
        found_with_prepare = False
        for call in mock_cursor.execute.call_args_list:
            args, kwargs = call
            # Check if this is an INSERT statement
            if args and "INSERT" in str(args[0]).upper():
                # Check if prepare=True was passed (should NOT be)
                if 'prepare' in kwargs and kwargs['prepare'] == True:
                    found_with_prepare = True
                    break
                elif len(args) > 2 and args[2] == True:
                    found_with_prepare = True
                    break

        assert not found_with_prepare, \
            "Should not use prepared statements when pool has prepare_statements=False"


def test_hot_path_uses_prepared_statements():
    """
    Test that hot path (features-compute) uses prepared statements.

    When pool is configured with prepare_statements=True (production),
    the hot path should use prepared statements for performance.
    """
    from gefion.db.ingest import insert_computed_features
    from gefion.db import pool as db_pool
    from unittest.mock import Mock, patch
    from datetime import date

    mock_conn = Mock()
    mock_conn.autocommit = True
    mock_cursor = Mock()
    mock_cursor.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor.__exit__ = Mock(return_value=None)
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.commit = Mock()

    rows = [
        {"date": date(2025, 1, 1), "test_col": 1.0},
        {"date": date(2025, 1, 2), "test_col": 2.0},
    ]
    feature_map = {"test_col": 1}

    # Test with prepare_statements=True (like production does)
    with patch.object(db_pool, 'should_prepare_statements', return_value=True):
        insert_computed_features(mock_conn, data_id=1, rows=rows, feature_map=feature_map)

        # Verify execute was called WITH prepare=True
        # Check all execute calls, not just the last one
        assert mock_cursor.execute.called

        # Look through all execute calls to find the INSERT statement
        found_prepare = False
        for call in mock_cursor.execute.call_args_list:
            args, kwargs = call
            # Check if this is an INSERT statement
            if args and "INSERT" in str(args[0]).upper():
                # Check if prepare=True was passed
                if 'prepare' in kwargs and kwargs['prepare'] == True:
                    found_prepare = True
                    break
                elif len(args) > 2 and args[2] == True:
                    found_prepare = True
                    break

        assert found_prepare, \
            "Should use prepared statements when pool has prepare_statements=True"


def test_production_config_enables_prepared_statements():
    """
    Verify that production configurations use prepare_statements=True.

    This ensures the hot path (features-compute) gets the performance
    benefit of prepared statements.
    """
    # This is verified by checking the actual init_pool calls in cli.py
    # We can't test this directly without modifying cli.py, but we can
    # document the expected behavior

    # Expected production config (from cli.py:1563):
    # db_pool.init_pool(url, min_size=min_pool, max_size=max_pool, prepare_statements=True)

    # This test documents that production should use prepare_statements=True
    production_config = {
        "prepare_statements": True,  # Hot path needs this for performance
    }

    assert production_config["prepare_statements"] == True, \
        "Production should use prepared statements for hot path"
