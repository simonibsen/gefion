"""Schema tests for regime slicing (005) — T004.

TDD: written FIRST. Verifies the owner-approved regime_definitions and
regime_labels tables exist after db-init, that regime_labels is a hypertable
with the expected primary key, and that the BRIN index on date is present.
"""
import os
import pytest
import psycopg

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


def test_regime_definitions_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'regime_definitions' ORDER BY ordinal_position"
        )
        cols = {r[0] for r in cur.fetchall()}
    expected = {
        "id", "name", "scope", "expression", "bucketing", "persistence",
        "origin", "descriptive_metadata", "status", "created_at",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_regime_labels_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'regime_labels' ORDER BY ordinal_position"
        )
        cols = {r[0] for r in cur.fetchall()}
    assert {"regime_id", "date", "entity_id", "label", "dataset_version"}.issubset(cols)


def test_regime_labels_is_hypertable(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'regime_labels'"
        )
        assert cur.fetchone() is not None, "regime_labels is not a hypertable"


def test_regime_labels_primary_key(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'regime_labels'::regclass AND i.indisprimary
            ORDER BY a.attname
            """
        )
        pk_cols = {r[0] for r in cur.fetchall()}
    assert pk_cols == {"regime_id", "entity_id", "date"}, f"unexpected PK: {pk_cols}"


def test_regime_labels_brin_index_on_date(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'regime_labels'"
        )
        defs = " ".join(r[0].lower() for r in cur.fetchall())
    assert "brin" in defs and "date" in defs, "missing BRIN index on date"
