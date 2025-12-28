"""
Test that feature_functions table has the necessary composite index.

The issue: Queries filtering on (enabled, status, name) are slow without an index.
The query: WHERE enabled = TRUE AND status = 'active' AND name = %s

The fix: Add composite index on (enabled, status, name) to optimize this lookup.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os
import psycopg
import pytest

from g2.config import load_settings
from g2.db import schema


pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run."
)


def get_db_url():
    """Get database URL from environment or settings."""
    settings = load_settings()
    return os.environ.get("DATABASE_URL", settings.database_url)


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = get_db_url()
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        yield conn


def test_feature_functions_has_composite_index(db_conn):
    """
    Test that feature_functions table has an index on (enabled, status, name).

    This index optimizes the common query pattern:
    WHERE enabled = TRUE AND status = 'active' AND name = %s
    """
    # Create the table (which should also create the index)
    schema.create_feature_functions_table(db_conn)

    # Check if the index exists
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'feature_functions'
            AND indexdef LIKE '%enabled%status%name%'
        """)
        indexes = cur.fetchall()

    # Should have at least one index covering these columns
    assert len(indexes) > 0, \
        "Missing composite index on feature_functions(enabled, status, name)"

    # Verify the index definition
    index_found = False
    for idx_name, idx_def in indexes:
        # Index should cover enabled, status, and name in some order
        if 'enabled' in idx_def.lower() and 'status' in idx_def.lower() and 'name' in idx_def.lower():
            index_found = True
            break

    assert index_found, \
        f"Index found but doesn't cover all required columns: {indexes}"


def test_index_improves_query_performance(db_conn):
    """
    Test that the index is actually used by the query planner.

    Use EXPLAIN to verify the index is being used for the lookup query.
    """
    schema.create_feature_functions_table(db_conn)

    # Get the query plan
    with db_conn.cursor() as cur:
        cur.execute("""
            EXPLAIN (FORMAT TEXT)
            SELECT language, function_body, version
            FROM feature_functions
            WHERE enabled = TRUE AND status = 'active' AND name = 'test_function'
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 1
        """)
        plan = "\n".join([row[0] for row in cur.fetchall()])

    # The plan should mention an index scan (not a sequential scan)
    # With the index, it should use "Index Scan" or "Bitmap Index Scan"
    # Without the index, it would use "Seq Scan" (sequential scan)
    assert "index" in plan.lower() or "Index" in plan, \
        f"Query is not using an index. Plan:\n{plan}"


def test_index_name_convention(db_conn):
    """
    Test that the index follows a clear naming convention.

    Index should be named something like:
    idx_feature_functions_enabled_status_name
    """
    schema.create_feature_functions_table(db_conn)

    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'feature_functions'
            AND indexname LIKE '%enabled%status%name%'
        """)
        index_names = [row[0] for row in cur.fetchall()]

    assert len(index_names) > 0, "Index with expected naming pattern not found"

    # At least one index should exist with a descriptive name
    found_descriptive_index = any(
        'enabled' in name and 'status' in name and 'name' in name
        for name in index_names
    )

    assert found_descriptive_index, \
        f"Index exists but doesn't have a clear name: {index_names}"
