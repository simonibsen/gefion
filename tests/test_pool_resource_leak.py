"""
Test that connection pool is properly closed after CLI commands.

The bug: Connection pool is unconditionally closed in finally block, even if it
was already initialized before the function was called. This breaks callers who
manage their own pool lifecycle.

The fix: Only close the pool if the function created it (pool_needed=True).
"""
import os
from unittest.mock import Mock, patch, call

import pytest


def test_features_compute_closes_pool_if_it_created_it():
    """
    Test that features-compute closes the pool only if it initialized it.

    When pool_needed=True (function creates the pool), it should close it.
    """
    from g2.cli import features_compute

    with patch('g2.cli.psycopg.connect') as mock_connect, \
         patch('g2.cli.db_pool') as mock_pool, \
         patch('g2.features.dispatcher.compute_features') as mock_compute:

        # Setup: Pool doesn't exist initially
        mock_pool.get_pool.return_value = None  # pool_needed will be True

        # Mock database connection
        mock_conn = Mock()
        mock_conn.autocommit = True
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)

        # Mock symbol query returning one stock
        mock_cursor.fetchall.return_value = [("TEST",)]
        mock_cursor.fetchone.return_value = (1,)  # stock id
        mock_conn.cursor.return_value = mock_cursor

        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_connect.return_value.__exit__.return_value = None

        # Mock pool connection for worker
        mock_worker_conn = Mock()
        mock_worker_conn.autocommit = True
        mock_worker_conn.cursor.return_value = mock_cursor
        mock_pool.get_connection.return_value.__enter__.return_value = mock_worker_conn
        mock_pool.get_connection.return_value.__exit__.return_value = None

        # Mock compute_features result
        mock_compute.return_value = {
            "summary": {
                "total_inserted": 10,
                "total_errors": 0
            }
        }

        # Run the command
        url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")
        features_compute(
            db_url=url,
            symbols=None,
            features=None,
            function_names=None,
            all_features=False,
            incremental=True,
            update_existing=False,
            max_workers=1,
            writer_workers=0,
            feature_batch_size=200,
            profile=False,
            json_output=False,
            progress=False,
            sync_commit=True
        )

        # Verify pool was initialized (because get_pool returned None)
        mock_pool.init_pool.assert_called_once()

        # Verify pool was closed (because we created it)
        mock_pool.close_pool.assert_called_once()


def test_features_compute_doesnt_close_existing_pool():
    """
    Test that features-compute doesn't close a pool that already existed.

    When pool_needed=False (pool already exists), it should NOT close it.
    This is the bug - currently it always closes the pool.
    """
    from g2.cli import features_compute

    with patch('g2.cli.psycopg.connect') as mock_connect, \
         patch('g2.cli.db_pool') as mock_pool, \
         patch('g2.features.dispatcher.compute_features') as mock_compute:

        # Setup: Pool already exists
        existing_pool = Mock()
        mock_pool.get_pool.return_value = existing_pool  # pool_needed will be False

        # Mock database connection
        mock_conn = Mock()
        mock_conn.autocommit = True
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)

        # Mock symbol query
        mock_cursor.fetchall.return_value = [("TEST",)]
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor

        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_connect.return_value.__exit__.return_value = None

        # Mock pool connection
        mock_worker_conn = Mock()
        mock_worker_conn.autocommit = True
        mock_worker_conn.cursor.return_value = mock_cursor
        mock_pool.get_connection.return_value.__enter__.return_value = mock_worker_conn
        mock_pool.get_connection.return_value.__exit__.return_value = None

        # Mock compute_features
        mock_compute.return_value = {
            "summary": {
                "total_inserted": 10,
                "total_errors": 0
            }
        }

        # Run the command
        url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")
        features_compute(
            db_url=url,
            symbols=None,
            features=None,
            function_names=None,
            all_features=False,
            incremental=True,
            update_existing=False,
            max_workers=1,
            writer_workers=0,
            feature_batch_size=200,
            profile=False,
            json_output=False,
            progress=False,
            sync_commit=True
        )

        # Verify pool was NOT initialized (already existed)
        mock_pool.init_pool.assert_not_called()

        # Verify pool was NOT closed (we didn't create it)
        # This is the bug - currently it DOES get closed unconditionally
        mock_pool.close_pool.assert_not_called()


def test_features_compute_closes_pool_even_on_error():
    """
    Test that pool cleanup happens even when errors occur.

    If the function created the pool and an error occurs, the pool should
    still be closed in the finally block.
    """
    from g2.cli import features_compute

    with patch('g2.cli.psycopg.connect') as mock_connect, \
         patch('g2.cli.db_pool') as mock_pool, \
         patch('g2.features.dispatcher.compute_features') as mock_compute:

        # Setup: Pool doesn't exist
        mock_pool.get_pool.return_value = None

        # Mock database connection
        mock_conn = Mock()
        mock_conn.autocommit = True
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)

        # Mock symbol query
        mock_cursor.fetchall.return_value = [("TEST",)]
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor

        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_connect.return_value.__exit__.return_value = None

        # Mock pool connection
        mock_worker_conn = Mock()
        mock_worker_conn.autocommit = True
        mock_worker_conn.cursor.return_value = mock_cursor
        mock_pool.get_connection.return_value.__enter__.return_value = mock_worker_conn
        mock_pool.get_connection.return_value.__exit__.return_value = None

        # Make compute_features raise an error
        mock_compute.side_effect = RuntimeError("Computation failed")

        # Run the command - should handle the error
        url = os.getenv("DATABASE_URL", "postgresql://localhost/g2test")
        features_compute(
            db_url=url,
            symbols=None,
            features=None,
            function_names=None,
            all_features=False,
            incremental=True,
            update_existing=False,
            max_workers=1,
            writer_workers=0,
            feature_batch_size=200,
            profile=False,
            json_output=False,
            progress=False,
            sync_commit=True
        )

        # Verify pool was initialized
        mock_pool.init_pool.assert_called_once()

        # Verify pool was closed even though an error occurred
        mock_pool.close_pool.assert_called_once()
