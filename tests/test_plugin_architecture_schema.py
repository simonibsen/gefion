"""
TDD tests for plugin architecture schema changes.

Tests that the feature_functions table supports the 'called_by' column
for hierarchical plugin system.
"""
import os
import psycopg
import pytest
from g2.db import schema


@pytest.fixture
def db_conn():
    """Create test database connection."""
    db_url = os.getenv("DATABASE_URL", "postgresql://g2:g2pass@localhost:5432/g2")
    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
            yield conn
    except psycopg.OperationalError:
        pytest.skip("Database not available")


def test_feature_functions_table_has_called_by_column(db_conn):
    """Test that feature_functions table includes called_by column."""
    schema.create_feature_functions_table(db_conn)

    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'feature_functions'
            AND column_name = 'called_by'
        """)
        result = cur.fetchone()

    assert result is not None, "called_by column should exist"
    column_name, data_type, is_nullable = result
    assert column_name == "called_by"
    assert data_type == "text"
    assert is_nullable == "YES"  # Optional - plugins don't have called_by


def test_feature_functions_supports_plugin_hierarchy(db_conn):
    """Test that we can create meta-functions and plugins."""
    schema.create_feature_functions_table(db_conn)

    # Insert meta-function (no called_by)
    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, status, enabled
            ) VALUES (
                'compute_indicators', '1.0', 'python', 'def compute(...): pass', 'active', TRUE
            ) RETURNING id
        """)
        meta_id = cur.fetchone()[0]

    # Insert plugin (has called_by pointing to meta-function)
    with db_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO feature_functions (
                name, version, language, function_body, status, enabled, called_by
            ) VALUES (
                'indicator_rsi', '1.0', 'python', 'def compute(...): pass', 'active', TRUE, 'compute_indicators'
            ) RETURNING id
        """)
        plugin_id = cur.fetchone()[0]

    # Verify we can query plugins by meta-function
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT name FROM feature_functions
            WHERE called_by = 'compute_indicators'
            AND enabled = TRUE
            AND status = 'active'
        """)
        plugins = [row[0] for row in cur.fetchall()]

    assert 'indicator_rsi' in plugins
    assert len(plugins) == 1


def test_feature_functions_index_includes_called_by(db_conn):
    """Test that there's an index for efficient called_by queries."""
    schema.create_feature_functions_table(db_conn)

    with db_conn.cursor() as cur:
        # Check for index that includes called_by for plugin discovery
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'feature_functions'
            AND indexdef LIKE '%called_by%'
        """)
        indexes = cur.fetchall()

    assert len(indexes) > 0, "Should have index on called_by for plugin discovery"
