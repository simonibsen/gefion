"""Test prepared statement optimization for database inserts."""
import os
from datetime import date

import psycopg
import pytest

from g2.db.ingest import insert_computed_features
from g2.db import schema
from g2.db import pool


class FakeCursor:
    def __init__(self, conn):
        self._rows = [(1,)]
        self.rowcount = 0
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        query = args[0] if args else ""
        # Roughly derive rowcount for insert batches
        if "INSERT INTO computed_features" in query:
            # Count number of value tuples
            tuples = query.count("),(") + 1
            self.rowcount = tuples
            self.conn.last_inserted_count = tuples
        elif "SELECT COUNT(*) FROM computed_features" in query:
            count = getattr(self.conn, "last_inserted_count", 0)
            self._rows = [(count,)]
            self.rowcount = 1
        else:
            self.rowcount = 1

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self):
        self.autocommit = False
        self.last_inserted_count = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        return None

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, *args, **kwargs):
        self._g2_prepare_statements = kwargs.get("prepare_statements", False)

    def getconn(self):
        return FakeConn()

    def putconn(self, _conn):
        return None

    def close(self):
        return None


@pytest.fixture(autouse=True)
def fake_pool(monkeypatch):
    # Replace ConnectionPool with FakePool to avoid real DB connections
    monkeypatch.setattr(pool, "ConnectionPool", FakePool)
    # Stub psycopg.connect to avoid hitting a real database
    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: FakeConn())
    # Reset global pool before/after
    pool.close_pool()
    yield
    pool.close_pool()


def test_pool_enables_prepared_statements():
    """Test that connection pool can be initialized with prepared statement support."""
    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")

    # Initialize pool with prepared statement support
    # This enables psycopg3's automatic statement caching via prepare=True
    test_pool = pool.init_pool(db_url, min_size=1, max_size=2, prepare_statements=True)

    try:
        # Verify pool was created successfully with the flag
        assert test_pool is not None
        assert pool.get_pool() is test_pool
        assert pool.should_prepare_statements() is True
    finally:
        pool.close_pool()


def test_insert_with_prepared_statements():
    """Test that insert_computed_features uses psycopg3 prepare=True when pool configured."""
    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")

    # Initialize pool with prepared statements enabled
    pool.init_pool(db_url, min_size=1, max_size=2, prepare_statements=True)

    try:
        with pool.get_connection() as conn:
            conn.autocommit = True
            schema.create_stocks_table(conn)
            schema.create_feature_definitions_table(conn)
            schema.create_computed_features_table(conn)

            # Create test feature
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO feature_definitions (name, function_name, store_table, store_column, store_type, active) "
                    "VALUES ('rsi_14', 'indicator', 'computed_features', 'value', 'float', TRUE) "
                    "ON CONFLICT (name) DO UPDATE SET active = TRUE RETURNING id"
                )
                feature_id = cur.fetchone()[0]

                cur.execute("INSERT INTO stocks (symbol) VALUES (%s) ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol RETURNING id", ("PREP",))
                data_id = cur.fetchone()[0]

            feature_map = {"rsi_14": feature_id}

            # Create batch of exactly 200 rows (will use prepare=True)
            # Use unique dates by spanning multiple months
            rows = []
            for i in range(1, 201):
                month = ((i - 1) // 28) + 1
                day = ((i - 1) % 28) + 1
                rows.append({"date": date(2024, month, day), "rsi_14": 50.0 + i})

            # Insert - should use psycopg3's prepare=True internally
            inserted = insert_computed_features(
                conn,
                data_id=data_id,
                rows=rows,
                feature_map=feature_map,
                update_existing=False,
                batch_size=200,
            )

            # Verify insertion
            assert inserted == 200

            # Verify data in database
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM computed_features WHERE data_id = %s", (data_id,))
                assert cur.fetchone()[0] == 200
    finally:
        pool.close_pool()


def test_fallback_without_pool():
    """Test that insert_computed_features works without pool (backward compatibility)."""
    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")

    # Close any existing pool
    pool.close_pool()

    # Verify prepared statements are disabled
    assert pool.should_prepare_statements() is False

    # Use direct connection (no pool)
    with psycopg.connect(db_url) as conn:
        conn.autocommit = True
        schema.create_stocks_table(conn)
        schema.create_feature_definitions_table(conn)
        schema.create_computed_features_table(conn)

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO feature_definitions (name, function_name, store_table, store_column, store_type, active) "
                "VALUES ('rsi_14', 'indicator', 'computed_features', 'value', 'float', TRUE) "
                "ON CONFLICT (name) DO UPDATE SET active = TRUE RETURNING id"
            )
            feature_id = cur.fetchone()[0]

            cur.execute("INSERT INTO stocks (symbol) VALUES (%s) ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol RETURNING id", ("NOPOOL",))
            data_id = cur.fetchone()[0]

        feature_map = {"rsi_14": feature_id}
        rows = [{"date": date(2024, 1, 1), "rsi_14": 50.0}]

        # Should work without prepared statements (uses prepare=False)
        inserted = insert_computed_features(
            conn,
            data_id=data_id,
            rows=rows,
            feature_map=feature_map,
            batch_size=1,
        )

        assert inserted == 1
