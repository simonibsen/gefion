"""
Tests for connection pooling functionality.
"""
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from gefion.db import schema, pool
from gefion.db.ingest import upsert_stock


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    connection = create_connection()
    connection.autocommit = True
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def setup_pool():
    """Initialize pool before each test and clean up after."""
    # Clean up any existing pool
    pool.close_pool()

    # Initialize pool for tests
    pool.init_pool(schema.test_db_url(), min_size=2, max_size=5)

    yield

    # Clean up after test
    pool.close_pool()


@pytest.fixture(autouse=True)
def setup_tables(conn):
    """Setup minimal tables for testing."""
    from gefion.db.schema import create_stocks_table
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stocks CASCADE;")
    create_stocks_table(conn)
    yield


def test_pool_initialization():
    """Test that connection pool initializes correctly."""
    assert pool.get_pool() is not None, "Pool should be initialized by fixture"

    p = pool.get_pool()
    # Check pool is open and has correct size
    assert p.min_size == 2
    assert p.max_size == 5


def test_get_connection_from_pool():
    """Test getting a connection from the pool."""
    with pool.get_connection() as conn:
        assert conn is not None
        # Verify we can use the connection
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            result = cur.fetchone()
            assert result[0] == 1


def test_pool_reuses_connections():
    """Test that pool reuses connections instead of creating new ones."""
    connections_seen = []

    # Get same connection multiple times
    for _ in range(5):
        with pool.get_connection() as conn:
            # Use id() to track if it's the same connection object
            connections_seen.append(id(conn))

    # With a pool of min_size=2, we should see connection reuse
    # (not all IDs will be unique)
    unique_conns = len(set(connections_seen))
    assert unique_conns <= 2, f"Expected <= 2 unique connections, got {unique_conns}"


def test_pool_concurrent_access():
    """Test that pool handles concurrent access correctly."""
    def worker(worker_id):
        with pool.get_connection() as conn:
            conn.autocommit = True
            symbol = f"TEST{worker_id}"
            stock_id = upsert_stock(conn, symbol)
            return stock_id

    # Run 10 concurrent workers with pool of max_size=5
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(worker, range(10)))

    # All workers should succeed
    assert len(results) == 10
    assert all(r > 0 for r in results)


def test_pool_performance_vs_direct():
    """Test that pooled connections are faster than creating new connections."""
    num_operations = 20

    # Measure with pool
    start = time.time()
    for i in range(num_operations):
        with pool.get_connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
    time_pooled = time.time() - start

    # Measure without pool (direct connections)
    start = time.time()
    for i in range(num_operations):
        conn = psycopg.connect(schema.test_db_url())
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        conn.close()
    time_direct = time.time() - start

    print(f"\nPooled: {time_pooled:.3f}s, Direct: {time_direct:.3f}s")
    print(f"Speed-up: {time_direct / time_pooled:.1f}x")

    # Pooled should be significantly faster (at least 2x)
    assert time_pooled < time_direct / 2, \
        f"Pooled connections should be much faster (got {time_direct / time_pooled:.1f}x)"


def test_pool_cleanup():
    """Test that pool cleanup works correctly."""
    # Pool is initialized by fixture
    assert pool.get_pool() is not None

    # Close the pool
    pool.close_pool()

    # Pool should be None after cleanup
    assert pool.get_pool() is None


def test_pool_raises_without_init():
    """Test that get_connection raises error if pool not initialized."""
    pool.close_pool()  # Ensure pool is closed

    with pytest.raises(RuntimeError, match="Connection pool not initialized"):
        with pool.get_connection() as conn:
            pass


# --- pool must not survive to interpreter shutdown (issue #138) --------------------

def test_init_pool_registers_atexit_close(monkeypatch):
    """A pool left open at interpreter shutdown is torn down by
    ConnectionPool.__del__, which on Python 3.14 raises
    PythonFinalizationError (threads cannot be joined during finalization) —
    the nightly data-update cron printed that traceback every run. init_pool
    must register close_pool with atexit so the pool is closed while threads
    are still joinable, no matter which caller initialized it."""
    import atexit

    captured = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **kw: captured.append(fn) or fn)
    monkeypatch.setattr(pool, "_atexit_registered", False)

    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        pool.init_pool(schema.test_db_url(), min_size=1, max_size=2)
    except Exception as exc:
        pytest.skip(f"DB not available: {exc}")
    try:
        assert pool.close_pool in captured
        # re-init must not stack duplicate registrations
        pool.init_pool(schema.test_db_url(), min_size=1, max_size=2)
        assert captured.count(pool.close_pool) == 1
    finally:
        pool.close_pool()


def test_close_pool_is_idempotent_for_atexit():
    """atexit may fire after a caller already closed the pool — the second
    close must be a no-op, not an error."""
    pool.close_pool()
    pool.close_pool()
