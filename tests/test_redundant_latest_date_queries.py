"""
Test that latest date queries are skipped in full refresh mode.

The issue: Querying latest dates when doing full refresh is wasteful since
we're recomputing all dates anyway.

The fix: Only query latest dates when incremental=True and full_refresh=False.
"""
from unittest.mock import Mock, patch, call


def test_latest_dates_not_queried_in_full_refresh():
    """
    Test that latest dates are not queried when full_refresh=True.

    In full refresh mode, we recompute all dates regardless of what's already
    in the database, so querying latest dates is wasteful.
    """
    from gefion.features.dispatcher import compute_features

    with patch('gefion.features.dispatcher._fetch_feature_definitions') as mock_fetch_defs, \
         patch('gefion.features.dispatcher._latest_dates_for_features') as mock_latest_dates, \
         patch('gefion.features.dispatcher._process_function_group') as mock_process:

        # Setup mocks
        mock_fetch_defs.return_value = [
            (1, 'test_feature', 'test_func', {}, 'stock_ohlcv', 'close', 'computed_features', 'value')
        ]
        mock_process.return_value = {'inserted': 0, 'errors': []}

        mock_conn = Mock()
        mock_conn.autocommit = True

        # Test full refresh mode
        result = compute_features(
            mock_conn,
            data_id=1,
            incremental=False,  # Not incremental
            full_refresh=True,   # Full refresh
        )

        # Latest dates should NOT be queried in full refresh
        mock_latest_dates.assert_not_called()


def test_latest_dates_queried_in_incremental_mode():
    """
    Test that latest dates ARE queried when incremental=True.

    In incremental mode, we need to know the latest computed date for each
    feature so we can fetch only new data.
    """
    from gefion.features.dispatcher import compute_features

    with patch('gefion.features.dispatcher._fetch_feature_definitions') as mock_fetch_defs, \
         patch('gefion.features.dispatcher._latest_dates_for_features') as mock_latest_dates, \
         patch('gefion.features.dispatcher._process_function_group') as mock_process:

        # Setup mocks
        mock_fetch_defs.return_value = [
            (1, 'test_feature', 'test_func', {}, 'stock_ohlcv', 'close', 'computed_features', 'value')
        ]
        mock_latest_dates.return_value = {1: None}
        mock_process.return_value = {'inserted': 0, 'errors': []}

        mock_conn = Mock()
        mock_conn.autocommit = True

        # Test incremental mode
        result = compute_features(
            mock_conn,
            data_id=1,
            incremental=True,    # Incremental
            full_refresh=False,  # Not full refresh
        )

        # Latest dates SHOULD be queried in incremental mode
        mock_latest_dates.assert_called_once()
        args, kwargs = mock_latest_dates.call_args
        assert args[0] == mock_conn
        assert args[1] == 1  # data_id
        assert args[2] == [1]  # feature_ids


def test_full_refresh_overrides_incremental():
    """
    Test that full_refresh=True overrides incremental=True.

    Even if incremental=True, full_refresh=True should take precedence
    and skip latest date queries.
    """
    from gefion.features.dispatcher import compute_features

    with patch('gefion.features.dispatcher._fetch_feature_definitions') as mock_fetch_defs, \
         patch('gefion.features.dispatcher._latest_dates_for_features') as mock_latest_dates, \
         patch('gefion.features.dispatcher._process_function_group') as mock_process:

        # Setup mocks
        mock_fetch_defs.return_value = [
            (1, 'test_feature', 'test_func', {}, 'stock_ohlcv', 'close', 'computed_features', 'value')
        ]
        mock_process.return_value = {'inserted': 0, 'errors': []}

        mock_conn = Mock()
        mock_conn.autocommit = True

        # Test: incremental=True but full_refresh=True
        result = compute_features(
            mock_conn,
            data_id=1,
            incremental=True,    # Incremental (but overridden)
            full_refresh=True,   # Full refresh takes precedence
        )

        # Latest dates should NOT be queried (full_refresh overrides)
        mock_latest_dates.assert_not_called()
