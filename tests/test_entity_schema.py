"""Schema tests for the first-class entity model (007) — T002/T011.

TDD: written FIRST. Part 1 (Migration A): feature_definitions gains the declared
entity axis with zero behavior change. Part 2 (Migration B, gated behind the
orphan scan and entity-delete existing) lands later in this file: the
computed_features hard FK is retired and the macro_series pair exists.
"""
import os

import psycopg
import pytest

from gefion.db import schema


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    c = _conn()
    yield c
    c.close()


# --- Part 1: the declared entity axis (Migration A) ---------------------------

def test_entity_table_column_exists_with_stocks_default(conn):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT is_nullable, column_default, data_type
               FROM information_schema.columns
               WHERE table_name = 'feature_definitions'
                 AND column_name = 'entity_table'"""
        )
        row = cur.fetchone()
    assert row is not None, "feature_definitions.entity_table missing"
    is_nullable, default, data_type = row
    assert is_nullable == "NO"
    assert "stocks" in (default or "")
    assert data_type == "text"


def test_existing_definitions_default_to_stocks(conn):
    """Migration A is a behavioral no-op: every pre-existing definition resolves
    against stocks (SC-201's schema-level half)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM feature_definitions WHERE entity_table != 'stocks'"
        )
        assert cur.fetchone()[0] == 0
