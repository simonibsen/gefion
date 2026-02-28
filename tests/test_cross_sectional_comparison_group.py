"""
TDD tests for cross_sectional_features comparison_group column.

Tests that the comparison_group column exists and works correctly
for storing market-relative vs sector-relative vs industry-relative features.
"""
import os
import psycopg
import pytest
from datetime import date
from g2.db import schema


def _ensure_cross_sectional_schema(conn):
    """Ensure cross_sectional_features table exists with comparison_group."""
    from g2.db.schema import create_stocks_table
    create_stocks_table(conn)

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cross_sectional_features (
                data_id INTEGER NOT NULL,
                date DATE NOT NULL,
                feature_name TEXT NOT NULL,
                comparison_group TEXT NOT NULL DEFAULT 'market',
                value DOUBLE PRECISION,
                rank INTEGER,
                percentile DOUBLE PRECISION,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (data_id, date, feature_name, comparison_group)
            );
            CREATE INDEX IF NOT EXISTS cross_sectional_features_comparison_group_idx
                ON cross_sectional_features(comparison_group, date);
        """)
    conn.commit()


@pytest.fixture
def db_conn():
    """Create test database connection and ensure schema exists."""
    if not os.getenv("ENABLE_DB_TESTS"):
        pytest.skip("Database tests not enabled (set ENABLE_DB_TESTS=1)")
    db_url = schema.test_db_url()

    try:
        with psycopg.connect(db_url) as conn:
            _ensure_cross_sectional_schema(conn)
            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def test_comparison_group_column_exists(db_conn):
    """Test that comparison_group column exists in cross_sectional_features."""
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, column_default
            FROM information_schema.columns
            WHERE table_name = 'cross_sectional_features'
            AND column_name = 'comparison_group'
        """)
        result = cur.fetchone()

    assert result is not None, "comparison_group column should exist"
    column_name, data_type, column_default = result
    assert column_name == "comparison_group"
    assert data_type == "text"
    assert "market" in (column_default or ""), "Default should be 'market'"


def test_comparison_group_in_primary_key(db_conn):
    """Test that comparison_group is part of the primary key."""
    with db_conn.cursor() as cur:
        # Get primary key columns
        cur.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'cross_sectional_features'::regclass
            AND i.indisprimary
        """)
        pk_columns = [row[0] for row in cur.fetchall()]

    assert "comparison_group" in pk_columns, \
        f"comparison_group should be in primary key, got: {pk_columns}"


def test_same_feature_different_comparison_groups(db_conn):
    """Test that same feature can have different comparison groups."""
    # Get a valid data_id
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM stocks LIMIT 1")
        row = cur.fetchone()
        if not row:
            pytest.skip("No stocks in database")
        data_id = row[0]

    test_date = date(2025, 1, 15)
    feature_name = "test_return_1d"

    try:
        with db_conn.cursor() as cur:
            # Insert same feature with different comparison groups
            cur.execute("""
                INSERT INTO cross_sectional_features
                    (data_id, date, feature_name, comparison_group, value, rank, percentile)
                VALUES
                    (%s, %s, %s, 'market', 0.02, 5, 0.95),
                    (%s, %s, %s, 'sector:Technology', 0.02, 2, 0.90),
                    (%s, %s, %s, 'industry:Software', 0.02, 1, 1.0)
                ON CONFLICT DO NOTHING
            """, (data_id, test_date, feature_name) * 3)
        db_conn.commit()

        # Verify all three were inserted
        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT comparison_group, rank, percentile
                FROM cross_sectional_features
                WHERE data_id = %s AND date = %s AND feature_name = %s
                ORDER BY comparison_group
            """, (data_id, test_date, feature_name))
            results = cur.fetchall()

        assert len(results) == 3, f"Should have 3 rows, got {len(results)}"
        groups = [r[0] for r in results]
        assert 'market' in groups
        assert 'sector:Technology' in groups
        assert 'industry:Software' in groups

    finally:
        # Cleanup
        with db_conn.cursor() as cur:
            cur.execute("""
                DELETE FROM cross_sectional_features
                WHERE data_id = %s AND date = %s AND feature_name = %s
            """, (data_id, test_date, feature_name))
        db_conn.commit()


def test_default_comparison_group_is_market(db_conn):
    """Test that omitting comparison_group defaults to 'market'."""
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM stocks LIMIT 1")
        row = cur.fetchone()
        if not row:
            pytest.skip("No stocks in database")
        data_id = row[0]

    test_date = date(2025, 1, 16)
    feature_name = "test_default_group"

    try:
        with db_conn.cursor() as cur:
            # Insert without specifying comparison_group
            cur.execute("""
                INSERT INTO cross_sectional_features
                    (data_id, date, feature_name, value, rank, percentile)
                VALUES (%s, %s, %s, 0.01, 10, 0.80)
                ON CONFLICT DO NOTHING
            """, (data_id, test_date, feature_name))
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute("""
                SELECT comparison_group
                FROM cross_sectional_features
                WHERE data_id = %s AND date = %s AND feature_name = %s
            """, (data_id, test_date, feature_name))
            result = cur.fetchone()

        assert result is not None, "Row should exist"
        assert result[0] == "market", f"Default should be 'market', got '{result[0]}'"

    finally:
        with db_conn.cursor() as cur:
            cur.execute("""
                DELETE FROM cross_sectional_features
                WHERE data_id = %s AND date = %s AND feature_name = %s
            """, (data_id, test_date, feature_name))
        db_conn.commit()
