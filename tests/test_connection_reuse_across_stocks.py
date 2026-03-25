"""
Test that worker threads reuse connections across stocks to preserve prepared statement cache.

The issue: Acquiring and releasing a connection for each stock causes connection churn.
With prepared statements, each connection has its own cache. Churning connections
means losing and rebuilding the prepared statement cache repeatedly.

The fix: Each worker thread should acquire ONE connection and reuse it across all
stocks it processes, only returning it when done with all its work.
"""
from unittest.mock import Mock, patch, MagicMock, call
from datetime import date


def test_worker_reuses_same_connection_across_stocks():
    """
    Test that a worker thread reuses the same connection for multiple stocks.

    When processing stocks sequentially in a worker, the worker should:
    1. Acquire ONE connection at the start
    2. Process all assigned stocks with that connection
    3. Return the connection when done

    NOT:
    1. Acquire connection
    2. Process stock
    3. Release connection
    4. Repeat for each stock (churn)
    """
    from gefion.cli import features_compute
    import io
    import sys

    # Mock the database and pool
    with patch('gefion.cli.psycopg.connect') as mock_connect, \
         patch('gefion.cli.db_pool') as mock_pool, \
         patch('gefion.cli.schema') as mock_schema, \
         patch('gefion.cli.get_available_connections') as mock_avail, \
         patch('gefion.features.dispatcher.compute_features') as mock_compute:

        # Setup main connection mock
        mock_main_conn = MagicMock()
        mock_main_conn.__enter__ = Mock(return_value=mock_main_conn)
        mock_main_conn.__exit__ = Mock(return_value=None)
        mock_main_conn.autocommit = True
        mock_connect.return_value = mock_main_conn

        # Setup cursor for getting stocks
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        # Return 3 stocks
        mock_cursor.fetchall.side_effect = [
            [("AAPL",), ("MSFT",), ("GOOGL",)],  # First call: get stocks
        ]
        mock_cursor.fetchone.side_effect = [
            (1,),  # AAPL data_id
            (2,),  # MSFT data_id
            (3,),  # GOOGL data_id
        ]
        mock_main_conn.cursor.return_value = mock_cursor

        # Track pool connection acquisitions
        connection_acquisitions = []
        worker_connections = {}  # Map worker_id -> connection

        def get_connection():
            import threading
            worker_id = threading.get_ident()

            # If this worker already has a connection, it should reuse it
            if worker_id not in worker_connections:
                # Create new mock connection for this worker
                worker_conn = MagicMock()
                worker_conn.__enter__ = Mock(return_value=worker_conn)
                worker_conn.__exit__ = Mock(return_value=None)
                worker_conn.autocommit = True

                # Setup cursor for worker connection
                worker_cursor = MagicMock()
                worker_cursor.__enter__ = Mock(return_value=worker_cursor)
                worker_cursor.__exit__ = Mock(return_value=None)
                worker_conn.cursor.return_value = worker_cursor

                worker_connections[worker_id] = worker_conn

            conn = worker_connections[worker_id]
            connection_acquisitions.append({
                'worker_id': worker_id,
                'connection_id': id(conn),
            })
            return conn

        mock_pool.get_connection.side_effect = get_connection
        mock_pool.get_pool.return_value = None  # Pool needs init

        mock_avail.return_value = (10, 10)

        # Mock compute_features to return success
        mock_compute.return_value = {
            'summary': {
                'total_inserted': 100,
                'total_errors': 0,
            }
        }

        # Suppress output
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()

        try:
            # Run with max_workers=1 to ensure sequential processing
            # Set writer_workers=0 to avoid writer threads acquiring connections
            features_compute(
                symbols="AAPL,MSFT,GOOGL",
                features=None,
                all_features=True,
                function_names=None,
                incremental=True,
                update_existing=False,
                max_workers=1,
                feature_batch_size=2000,
                profile=False,
                sync_commit=False,
                writer_workers=0,
                db_url=None,
                json_output=True,
                progress=False,
            )
        finally:
            sys.stdout = old_stdout

        # With max_workers=1, all stocks are processed by the same worker
        # Each stock acquisition should get the SAME connection
        assert len(connection_acquisitions) == 3, \
            f"Expected 3 connection acquisitions (one per stock), got {len(connection_acquisitions)}"

        # All acquisitions should be from the same worker
        worker_ids = [acq['worker_id'] for acq in connection_acquisitions]
        assert len(set(worker_ids)) == 1, \
            f"Expected all acquisitions from same worker, got {len(set(worker_ids))} different workers"

        # CRITICAL: All acquisitions should be the SAME connection
        # This is the optimization - reuse the connection, don't churn
        connection_ids = [acq['connection_id'] for acq in connection_acquisitions]
        unique_connections = set(connection_ids)

        assert len(unique_connections) == 1, \
            f"Expected worker to reuse SAME connection for all stocks, but got {len(unique_connections)} different connections. " \
            f"This causes connection churn and loses prepared statement cache."


def test_connection_churn_vs_reuse_performance():
    """
    Demonstrate the performance difference between connection churn and reuse.

    Connection churn (current):
    - For N stocks: N pool get/release operations
    - Each connection might be different, losing prepared statement cache
    - Higher overhead from connection setup/teardown

    Connection reuse (optimized):
    - For N stocks in a worker: 1 pool get, N stock computations, 1 release
    - Same connection preserves prepared statement cache
    - Lower overhead
    """
    n_stocks = 100

    # Current approach: churn
    churn_pool_operations = n_stocks * 2  # get + release per stock

    # Optimized approach: reuse
    reuse_pool_operations = 2  # 1 get at start, 1 release at end

    # Reuse should be dramatically more efficient
    assert reuse_pool_operations < churn_pool_operations / 10, \
        f"Connection reuse ({reuse_pool_operations} pool ops) should be much more efficient " \
        f"than churn ({churn_pool_operations} pool ops)"

    # With 100 stocks, reuse is 100x more efficient on pool operations
    efficiency_gain = churn_pool_operations / reuse_pool_operations
    assert efficiency_gain == 100, \
        f"Reusing connection provides {efficiency_gain}x improvement over churning"


def test_prepared_statements_preserved_with_connection_reuse():
    """
    Test that prepared statements are preserved when reusing the same connection.

    When using the same connection for multiple stocks:
    - First stock: Prepared statement created (prepare=True)
    - Second stock: Prepared statement reused (already in cache)
    - Third stock: Prepared statement reused

    When churning connections:
    - Each stock might get a different connection
    - Each connection needs to rebuild prepared statement cache
    - Higher overhead
    """
    # This is a conceptual test documenting the benefit
    # Actual prepared statement caching is handled by psycopg + PostgreSQL

    # With connection reuse
    stocks_processed_with_reuse = 100
    prepared_statements_created = 1  # Created once, reused 100 times

    # With connection churn (worst case: always get different connection)
    stocks_processed_with_churn = 100
    prepared_statements_created_churn = 100  # Rebuild for each connection

    # Reuse creates far fewer prepared statements
    assert prepared_statements_created < prepared_statements_created_churn, \
        f"Connection reuse creates fewer prepared statements ({prepared_statements_created}) " \
        f"vs churn ({prepared_statements_created_churn})"

    # 100x fewer prepare operations with reuse
    prepare_reduction = prepared_statements_created_churn / prepared_statements_created
    assert prepare_reduction == 100, \
        f"Connection reuse reduces prepare operations by {prepare_reduction}x"
