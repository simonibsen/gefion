"""
TDD tests for plural source_columns schema migration.

Tests that feature definitions support both:
- Single source column (legacy): source_column: "close"
- Multiple source columns (new): source_columns: ["high", "low", "close"]
"""
import os
import psycopg
import pytest
from g2.db import schema


@pytest.fixture
def db_conn():
    """Create test database connection."""
    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:6432/g2")
    if not os.getenv("ENABLE_DB_TESTS"):
        pytest.skip("Database tests not enabled (set ENABLE_DB_TESTS=1)")

    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")

            # Create schema with new plural columns
            schema.create_feature_definitions_table(conn)

            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def test_feature_definition_with_plural_source_columns(db_conn):
    """Test that feature definitions can use plural source_columns array."""
    with db_conn.cursor() as cur:
        # Insert feature definition with plural source_columns
        cur.execute("""
            INSERT INTO feature_definitions (
                name, function_name, params, source_tables, source_columns,
                store_table, store_column, store_type, active
            ) VALUES (
                'indicator_adx_14',
                'compute_features',
                '{"indicator": "adx", "period": 14}',
                '["stock_ohlcv"]',
                '["high", "low", "close"]',
                'computed_features',
                'value',
                'double precision',
                true
            )
        """)

        # Verify it was inserted
        cur.execute("SELECT name, source_columns FROM feature_definitions WHERE name = 'indicator_adx_14'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 'indicator_adx_14'
        # JSONB array should be returned as list
        assert row[1] == ['high', 'low', 'close']


def test_feature_definition_with_plural_source_tables(db_conn):
    """Test that feature definitions can use plural source_tables array."""
    with db_conn.cursor() as cur:
        # Insert feature definition with plural source_tables
        cur.execute("""
            INSERT INTO feature_definitions (
                name, function_name, params, source_tables, source_columns,
                store_table, store_column, store_type, active
            ) VALUES (
                'cross_sectional_rank',
                'compute_features',
                '{"metric": "return"}',
                '["stock_ohlcv", "computed_features"]',
                '["close"]',
                'computed_features',
                'value',
                'double precision',
                true
            )
        """)

        # Verify it was inserted
        cur.execute("SELECT name, source_tables FROM feature_definitions WHERE name = 'cross_sectional_rank'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 'cross_sectional_rank'
        assert row[1] == ['stock_ohlcv', 'computed_features']


def test_feature_definition_with_single_column_legacy_format(db_conn):
    """Test backward compatibility: singular source_column should still work."""
    with db_conn.cursor() as cur:
        # Insert using legacy singular column format
        cur.execute("""
            INSERT INTO feature_definitions (
                name, function_name, params, source_table, source_column,
                store_table, store_column, store_type, active
            ) VALUES (
                'indicator_rsi_14',
                'compute_features',
                '{"indicator": "rsi", "period": 14}',
                'stock_ohlcv',
                'close',
                'computed_features',
                'value',
                'double precision',
                true
            )
        """)

        # Verify it was inserted
        cur.execute("SELECT name, source_table, source_column FROM feature_definitions WHERE name = 'indicator_rsi_14'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 'indicator_rsi_14'
        assert row[1] == 'stock_ohlcv'
        assert row[2] == 'close'


def test_query_feature_definitions_with_plural_columns(db_conn):
    """Test querying feature definitions returns plural columns correctly."""
    with db_conn.cursor() as cur:
        # Insert multiple feature definitions
        cur.execute("""
            INSERT INTO feature_definitions (
                name, function_name, params, source_tables, source_columns,
                store_table, store_column, store_type, active
            ) VALUES
            (
                'indicator_adx_14',
                'compute_features',
                '{"indicator": "adx", "period": 14}',
                '["stock_ohlcv"]',
                '["high", "low", "close"]',
                'computed_features',
                'value',
                'double precision',
                true
            ),
            (
                'indicator_rsi_14',
                'compute_features',
                '{"indicator": "rsi", "period": 14}',
                '["stock_ohlcv"]',
                '["close"]',
                'computed_features',
                'value',
                'double precision',
                true
            )
        """)

        # Query all active feature definitions
        cur.execute("""
            SELECT name, source_tables, source_columns
            FROM feature_definitions
            WHERE active = true
            ORDER BY name
        """)
        rows = cur.fetchall()

        assert len(rows) == 2

        # ADX (multiple columns)
        assert rows[0][0] == 'indicator_adx_14'
        assert rows[0][1] == ['stock_ohlcv']
        assert rows[0][2] == ['high', 'low', 'close']

        # RSI (single column)
        assert rows[1][0] == 'indicator_rsi_14'
        assert rows[1][1] == ['stock_ohlcv']
        assert rows[1][2] == ['close']


def test_feature_definition_mixed_legacy_and_plural(db_conn):
    """Test that we can have both legacy and plural formats in same database."""
    with db_conn.cursor() as cur:
        # Insert legacy format
        cur.execute("""
            INSERT INTO feature_definitions (
                name, function_name, params, source_table, source_column,
                store_table, store_column, store_type, active
            ) VALUES (
                'legacy_feature',
                'compute_features',
                '{"indicator": "rsi"}',
                'stock_ohlcv',
                'close',
                'computed_features',
                'value',
                'double precision',
                true
            )
        """)

        # Insert plural format
        cur.execute("""
            INSERT INTO feature_definitions (
                name, function_name, params, source_tables, source_columns,
                store_table, store_column, store_type, active
            ) VALUES (
                'plural_feature',
                'compute_features',
                '{"indicator": "adx"}',
                '["stock_ohlcv"]',
                '["high", "low", "close"]',
                'computed_features',
                'value',
                'double precision',
                true
            )
        """)

        # Query both
        cur.execute("SELECT name FROM feature_definitions ORDER BY name")
        rows = cur.fetchall()

        assert len(rows) == 2
        assert rows[0][0] == 'legacy_feature'
        assert rows[1][0] == 'plural_feature'
