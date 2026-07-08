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


# --- Part 2: FK retirement + the macro home (Migration B) ----------------------

def _fk_between(cur, child: str, parent: str) -> int:
    cur.execute(
        """SELECT count(*) FROM pg_constraint
           WHERE contype = 'f'
             AND conrelid = %s::regclass
             AND confrelid = %s::regclass""",
        (child, parent),
    )
    return cur.fetchone()[0]


def test_computed_features_stocks_fk_absent_fresh_init(conn):
    """Fresh db-init (this test database) carries no computed_features→stocks
    hard FK: entity identity is declared, not hard-wired."""
    with conn.cursor() as cur:
        assert _fk_between(cur, "computed_features", "stocks") == 0


def test_migration_b_idempotent_on_migrated_db(conn):
    """The migrated-existing-db path: applying Migration B (introspected-name
    constraint drop + macro tables) is idempotent and leaves no FK either."""
    import pathlib
    migration = (pathlib.Path(__file__).parent.parent / "sql" / "migrations"
                 / "20260708_000002_entity_model.sql").read_text()
    with conn.cursor() as cur:
        cur.execute(migration)
        assert _fk_between(cur, "computed_features", "stocks") == 0


def test_macro_series_catalog_shape(conn):
    with conn.cursor() as cur:
        # UNIQUE(name)
        cur.execute(
            """SELECT count(*) FROM pg_constraint
               WHERE conrelid = 'macro_series'::regclass AND contype = 'u'""")
        assert cur.fetchone()[0] >= 1, "macro_series.name UNIQUE missing"
        # cadence CHECK enforces the vocabulary
        cur.execute(
            """INSERT INTO macro_series (name, provider, kind, cadence)
               VALUES ('schema_test_series', 'test', 'index', 'daily')
               RETURNING id""")
        series_id = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """INSERT INTO macro_series (name, provider, kind, cadence)
                   VALUES ('schema_test_bad', 'test', 'index', 'hourly')""")
        cur.execute("DELETE FROM macro_series WHERE id = %s", (series_id,))


def test_macro_series_values_shape(conn):
    """(series_id, date) PK; value NOT NULL; OHLC optional; FK CASCADE — the
    catalog row's deletion takes its values with it (deletion story declared
    at DDL time)."""
    from datetime import date
    with conn.cursor() as cur:
        cur.execute(
            """SELECT a.attname
               FROM pg_index i
               JOIN pg_attribute a ON a.attrelid = i.indrelid
                                  AND a.attnum = ANY(i.indkey)
               WHERE i.indrelid = 'macro_series_values'::regclass
                 AND i.indisprimary""")
        assert {r[0] for r in cur.fetchall()} == {"series_id", "date"}
        cur.execute(
            """SELECT is_nullable FROM information_schema.columns
               WHERE table_name = 'macro_series_values' AND column_name = 'value'""")
        assert cur.fetchone()[0] == "NO"
        # OHLC optional: value-only insert succeeds (the SC-207 family shape)
        cur.execute(
            """INSERT INTO macro_series (name, provider, kind, cadence)
               VALUES ('schema_test_vals', 'test', 'rate', 'monthly')
               RETURNING id""")
        series_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO macro_series_values (series_id, date, value)
               VALUES (%s, %s, 3.25)""", (series_id, date(2026, 1, 1)))
        cur.execute("DELETE FROM macro_series WHERE id = %s", (series_id,))
        cur.execute(
            "SELECT count(*) FROM macro_series_values WHERE series_id = %s",
            (series_id,))
        assert cur.fetchone()[0] == 0, "ON DELETE CASCADE missing"
