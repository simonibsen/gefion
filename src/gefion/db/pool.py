"""
Database connection pooling for efficient connection reuse.

This module provides connection pooling to avoid the overhead of creating
new connections for each operation (50-200ms per connection).
"""
from __future__ import annotations

from typing import Optional
from contextlib import contextmanager

import atexit
import os
from psycopg_pool import ConnectionPool
import psycopg

from gefion.observability import create_span, set_attributes


_pool: Optional[ConnectionPool] = None
_atexit_registered = False


def init_pool(conninfo: str, min_size: int = 2, max_size: int = 10, timeout: float = 30.0, prepare_statements: bool = True) -> ConnectionPool:
    """
    Initialize the global connection pool.

    Args:
        conninfo: PostgreSQL connection string
        min_size: Minimum number of connections to maintain
        max_size: Maximum number of connections allowed
        timeout: Maximum seconds to wait for a connection
        prepare_statements: If True, enables automatic prepared statement caching (10-30% speedup)
                          When enabled, psycopg3 will automatically prepare and cache frequently-used queries

    Returns:
        ConnectionPool instance
    """
    global _pool, _atexit_registered
    if _pool is not None:
        _pool.close()

    # A pool left open at interpreter shutdown is torn down by
    # ConnectionPool.__del__, which cannot join the pool's worker threads
    # during finalization (PythonFinalizationError on Python 3.14) — close
    # while threads are still joinable, whichever caller initialized us (#138)
    if not _atexit_registered:
        atexit.register(close_pool)
        _atexit_registered = True

    # psycopg3 will automatically cache prepared statements when prepare=True is used in execute()
    # No explicit configuration needed - the prepare_statements flag is stored for reference by consumers
    _pool = ConnectionPool(
        conninfo=conninfo,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        open=True,
    )

    # Store the prepare_statements flag as a pool attribute for reference
    _pool._gefion_prepare_statements = prepare_statements

    return _pool


def get_pool() -> Optional[ConnectionPool]:
    """Get the global connection pool instance."""
    return _pool


def should_prepare_statements() -> bool:
    """
    Check if prepared statements are enabled.
    Priority:
      1) Pool flag when a pool is initialized.
      2) Env override G2_PREPARE_STATEMENTS (defaults to 1/true).
    """
    if _pool is not None:
        return bool(getattr(_pool, "_gefion_prepare_statements", False))
    env = os.getenv("G2_PREPARE_STATEMENTS", "1").lower()
    return env in ("1", "true", "yes", "on")


def close_pool() -> None:
    """Close the global connection pool and release all connections."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_connection():
    """
    Get a connection from the pool using context manager.

    Usage:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ...")

    If no pool is initialized, falls back to creating a direct connection.
    """
    if _pool is None:
        raise RuntimeError("Connection pool not initialized. Call init_pool() first.")

    with create_span("db.get_connection") as span:
        # Get pool stats before acquiring connection
        if hasattr(_pool, '_pool'):
            pool_size = _pool._pool.qsize() if hasattr(_pool._pool, 'qsize') else 0
            set_attributes(span, pool_available=pool_size)

        conn = _pool.getconn()
        try:
            set_attributes(span, connection_acquired=True)
            yield conn
        finally:
            _pool.putconn(conn)
            set_attributes(span, connection_returned=True)


def get_connection_direct(conninfo: str):
    """
    Fallback: Get a direct connection without pooling.

    Use this for compatibility when pooling is not set up.
    """
    return psycopg.connect(conninfo)
