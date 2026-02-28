"""
TDD tests for stocks table fundamentals columns.

Tests that sector, industry, name, and updated_at columns exist
and work correctly for storing company fundamental data.
"""
import os
import psycopg
import pytest
from datetime import datetime, timedelta


@pytest.fixture
def db_conn():
    """Create test database connection and ensure schema exists."""
    if not os.getenv("ENABLE_DB_TESTS"):
        pytest.skip("Database tests not enabled (set ENABLE_DB_TESTS=1)")
    from g2.db.schema import test_db_url
    db_url = test_db_url()

    try:
        with psycopg.connect(db_url) as conn:
            # Ensure stocks table exists with required columns
            from g2.db.schema import create_stocks_table
            create_stocks_table(conn)
            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def test_stocks_sector_column_exists(db_conn):
    """Test that sector column exists in stocks table."""
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'stocks' AND column_name = 'sector'
        """)
        result = cur.fetchone()

    assert result is not None, "sector column should exist"
    column_name, data_type, is_nullable = result
    assert data_type == "text"
    assert is_nullable == "YES"  # Nullable since not all stocks have sector


def test_stocks_industry_column_exists(db_conn):
    """Test that industry column exists in stocks table."""
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'stocks' AND column_name = 'industry'
        """)
        result = cur.fetchone()

    assert result is not None, "industry column should exist"
    column_name, data_type, is_nullable = result
    assert data_type == "text"
    assert is_nullable == "YES"


def test_stocks_name_column_exists(db_conn):
    """Test that name column exists in stocks table."""
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'stocks' AND column_name = 'name'
        """)
        result = cur.fetchone()

    assert result is not None, "name column should exist"
    column_name, data_type, is_nullable = result
    assert data_type == "text"
    assert is_nullable == "YES"


def test_stocks_updated_at_column_exists(db_conn):
    """Test that updated_at column exists in stocks table."""
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'stocks' AND column_name = 'updated_at'
        """)
        result = cur.fetchone()

    assert result is not None, "updated_at column should exist"
    column_name, data_type, is_nullable = result
    assert "timestamp" in data_type
    assert is_nullable == "YES"


def test_stocks_fundamentals_update(db_conn):
    """Test that fundamentals can be updated for a stock."""
    with db_conn.cursor() as cur:
        cur.execute("SELECT id, symbol FROM stocks LIMIT 1")
        row = cur.fetchone()
        if not row:
            pytest.skip("No stocks in database")
        stock_id, symbol = row

    try:
        with db_conn.cursor() as cur:
            cur.execute("""
                UPDATE stocks
                SET sector = 'Technology',
                    industry = 'Software',
                    name = 'Test Company Inc.',
                    updated_at = NOW()
                WHERE id = %s
            """, (stock_id,))
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT sector, industry, name, updated_at
                FROM stocks WHERE id = %s
            """, (stock_id,))
            result = cur.fetchone()

        assert result is not None
        sector, industry, name, updated_at = result
        assert sector == "Technology"
        assert industry == "Software"
        assert name == "Test Company Inc."
        assert updated_at is not None
        assert (datetime.now() - updated_at) < timedelta(minutes=1)

    finally:
        # Reset to NULL
        with db_conn.cursor() as cur:
            cur.execute("""
                UPDATE stocks
                SET sector = NULL, industry = NULL, name = NULL, updated_at = NULL
                WHERE id = %s
            """, (stock_id,))
        db_conn.commit()


def test_stocks_sector_index_exists(db_conn):
    """Test that an index exists on sector for efficient queries."""
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'stocks' AND indexdef LIKE '%sector%'
        """)
        result = cur.fetchone()

    assert result is not None, "Index on sector column should exist"
