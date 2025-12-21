"""Test generic feature computation dispatcher (TDD)."""
import pytest
from datetime import date, timedelta
from unittest.mock import Mock, patch, MagicMock
import psycopg
from g2.features.dispatcher import (
    compute_features,
    COMPUTE_FUNCTIONS,
    register_compute_function,
)


@pytest.fixture
def mock_conn():
    """Mock database connection."""
    conn = Mock(spec=psycopg.Connection)
    cursor = Mock()
    conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
    conn.cursor.return_value.__exit__ = Mock(return_value=None)
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    return conn


def test_compute_functions_registry_exists():
    """Test that COMPUTE_FUNCTIONS registry exists and is a dict."""
    assert isinstance(COMPUTE_FUNCTIONS, dict)
    assert 'indicator' in COMPUTE_FUNCTIONS
    assert 'derivative' in COMPUTE_FUNCTIONS


def test_register_compute_function():
    """Test registering a new compute function."""
    def mock_compute(rows, specs):
        return []

    register_compute_function('test_function', mock_compute)
    assert 'test_function' in COMPUTE_FUNCTIONS
    assert COMPUTE_FUNCTIONS['test_function'] == mock_compute


def test_compute_features_basic(mock_conn):
    """Test basic compute_features call."""
    # Setup mock to return no feature definitions
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    result = compute_features(mock_conn, data_id=1)

    # Should return dict with results per function_name
    assert isinstance(result, dict)


def test_compute_features_reads_feature_definitions(mock_conn):
    """Test that compute_features reads from feature_definitions table."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    compute_features(mock_conn, data_id=1)

    # Should query feature_definitions
    calls = [str(call) for call in cursor.execute.call_args_list]
    assert any('feature_definitions' in call for call in calls)


def test_compute_features_filters_by_function_names(mock_conn):
    """Test filtering feature_definitions by function_names."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    compute_features(mock_conn, data_id=1, function_names=['indicator'])

    # Should include WHERE clause filtering function_name
    execute_calls = cursor.execute.call_args_list
    assert len(execute_calls) > 0


def test_compute_features_filters_by_feature_names(mock_conn):
    """Test filtering feature_definitions by specific feature names."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    compute_features(mock_conn, data_id=1, feature_names=['indicator_rsi_14'])

    execute_calls = cursor.execute.call_args_list
    assert len(execute_calls) > 0


def test_compute_features_fetches_from_stock_ohlcv(mock_conn):
    """Test fetching source data from stock_ohlcv table for indicators."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value

    # Mock feature definition for RSI (source_table=stock_ohlcv)
    cursor.fetchall.side_effect = [
        # First call: feature_definitions query
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value')
        ],
        # Second call: stock_ohlcv query
        [
            (date(2024, 1, 1), 100.0),
            (date(2024, 1, 2), 102.0),
        ]
    ]

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        mock_compute = Mock(return_value=[])
        mock_funcs.__getitem__.return_value = mock_compute
        mock_funcs.get.return_value = mock_compute

        compute_features(mock_conn, data_id=1, function_names=['indicator'])

        # Should query stock_ohlcv table
        calls = [str(call) for call in cursor.execute.call_args_list]
        assert any('stock_ohlcv' in call for call in calls)


def test_compute_features_fetches_from_computed_features(mock_conn):
    """Test fetching source data from computed_features for derivatives."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value

    # Mock feature definition for derivative (source_table=computed_features)
    cursor.fetchall.side_effect = [
        # First call: feature_definitions query
        [
            (2, 'derivative_rsi_14_slope_5', 'derivative',
             {'source_feature': 'indicator_rsi_14', 'type': 'slope', 'window': 5},
             'computed_features', 'value', 'computed_features', 'value')
        ],
        # Second call: lookup source feature_id
        [],
        # Third call: computed_features query
        []
    ]
    cursor.fetchone.return_value = (1,)  # source feature_id

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs, patch('g2.features.dispatcher._load_db_function', return_value=None):
        mock_compute = Mock(return_value=[])
        mock_funcs.__getitem__.return_value = mock_compute
        mock_funcs.get.return_value = mock_compute

        compute_features(mock_conn, data_id=1, function_names=['derivative'])

        # Should query computed_features table
        calls = [str(call) for call in cursor.execute.call_args_list]
        assert any('computed_features' in call for call in calls)


def test_compute_features_incremental_mode(mock_conn):
    """Test incremental computation (only new dates)."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value

    # Mock latest feature date
    cursor.fetchone.side_effect = [
        (date(2024, 1, 10),),  # Latest date for this feature
    ]
    cursor.fetchall.side_effect = [
        # Feature definitions
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value')
        ],
        # Source data (only dates after latest)
        [
            (date(2024, 1, 11), 105.0),
            (date(2024, 1, 12), 106.0),
        ]
    ]

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        mock_compute = Mock(return_value=[])
        mock_funcs.__getitem__.return_value = mock_compute
        mock_funcs.get.return_value = mock_compute

        compute_features(mock_conn, data_id=1, incremental=True)

        # Should query for latest date and filter source data


def test_compute_features_full_refresh_mode(mock_conn):
    """Test full refresh mode (recompute all dates)."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        # Feature definitions
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value')
        ],
        # All source data
        [
            (date(2024, 1, i), 100.0 + i)
            for i in range(1, 31)
        ]
    ]

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        mock_compute = Mock(return_value=[])
        mock_funcs.__getitem__.return_value = mock_compute
        mock_funcs.get.return_value = mock_compute

        result = compute_features(mock_conn, data_id=1, full_refresh=True)

        # Should NOT query for latest date, should fetch all data


def test_compute_features_calls_correct_compute_function(mock_conn):
    """Test that dispatcher calls the correct compute function."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        # Feature definitions for indicators
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value')
        ],
        # Source data
        [
            (date(2024, 1, 1), 100.0),
        ]
    ]

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        mock_indicator_compute = Mock(return_value=[])
        mock_funcs.__getitem__.return_value = mock_indicator_compute
        mock_funcs.get.return_value = mock_indicator_compute

        compute_features(mock_conn, data_id=1, function_names=['indicator'])

        # Should call the indicator compute function
        # (will verify when implemented)


def test_compute_features_stores_results(mock_conn):
    """Test that computed results are stored in database."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        # Feature definitions
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value')
        ],
        # Source data
        [
            (date(2024, 1, i), 100.0 + i)
            for i in range(1, 21)
        ]
    ]

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        # Mock compute function returns results
        mock_compute = Mock(return_value=[
            {'date': date(2024, 1, 15), 'rsi_14': 65.0},
            {'date': date(2024, 1, 16), 'rsi_14': 67.0},
        ])
        mock_funcs.__getitem__.return_value = mock_compute
        mock_funcs.get.return_value = mock_compute

        with patch('g2.features.dispatcher.insert_computed_features') as mock_insert:
            compute_features(mock_conn, data_id=1)

            # Should call insert_computed_features
            # (will verify when implemented)


def test_compute_features_returns_insert_counts(mock_conn):
    """Test that compute_features returns count of inserted rows per function."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        # Feature definitions
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value')
        ],
        # Source data
        [(date(2024, 1, 1), 100.0)]
    ]

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        mock_compute = Mock(return_value=[{'date': date(2024, 1, 1), 'rsi_14': 50.0}])
        mock_funcs.__getitem__.return_value = mock_compute
        mock_funcs.get.return_value = mock_compute

        with patch('g2.features.dispatcher.insert_computed_features', return_value=1):
            result = compute_features(mock_conn, data_id=1)

            # Should return dict with counts
            assert isinstance(result, dict)


def test_compute_features_handles_errors(mock_conn):
    """Test error handling and aggregation."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        # Feature definitions
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value')
        ],
        # Source data
        [(date(2024, 1, 1), 100.0)]
    ]

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        # Mock compute function raises error
        mock_compute = Mock(side_effect=ValueError("Test error"))
        mock_funcs.__getitem__.return_value = mock_compute
        mock_funcs.get.return_value = mock_compute

        # Should not raise, should return error info
        result = compute_features(mock_conn, data_id=1)

        # Should contain error information
        assert isinstance(result, dict)


def test_compute_features_multiple_function_types(mock_conn):
    """Test computing multiple function types in one call."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        # Feature definitions (both indicator and derivative)
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value'),
            (2, 'derivative_rsi_14_slope_5', 'derivative',
             {'source_feature': 'indicator_rsi_14', 'type': 'slope', 'window': 5},
             'computed_features', 'value', 'computed_features', 'value')
        ],
        # Source data for indicators
        [(date(2024, 1, 1), 100.0)],
        # Source data for derivatives
        []
    ]
    cursor.fetchone.return_value = (1,)  # source feature_id

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        mock_indicator = Mock(return_value=[])
        mock_derivative = Mock(return_value=[])

        def get_func(name):
            if name == 'indicator':
                return mock_indicator
            elif name == 'derivative':
                return mock_derivative
            return Mock(return_value=[])

        mock_funcs.__getitem__.side_effect = get_func
        mock_funcs.get.side_effect = get_func

        result = compute_features(mock_conn, data_id=1)

        # Should handle both function types
        assert isinstance(result, dict)


def test_compute_features_groups_by_source_table(mock_conn):
    """Test that features are grouped by source_table to minimize queries."""
    cursor = mock_conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        # Multiple features with same source_table
        [
            (1, 'indicator_rsi_14', 'indicator',
             {'indicator': 'rsi', 'period': 14},
             'stock_ohlcv', 'close', 'computed_features', 'value'),
            (2, 'indicator_macd', 'indicator',
             {'indicator': 'macd'},
             'stock_ohlcv', 'close', 'computed_features', 'value')
        ],
        # Should only query stock_ohlcv once
        [(date(2024, 1, 1), 100.0)]
    ]

    with patch('g2.features.dispatcher.COMPUTE_FUNCTIONS') as mock_funcs:
        mock_compute = Mock(return_value=[])
        mock_funcs.__getitem__.return_value = mock_compute
        mock_funcs.get.return_value = mock_compute

        compute_features(mock_conn, data_id=1, function_names=['indicator'])

        # Should efficiently batch queries
        # (exact behavior to verify when implemented)
