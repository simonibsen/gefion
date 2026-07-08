"""Entity-registry validation tests (007, T004).

TDD: written FIRST. The declared entity axis is only trustworthy if declarations
are validated at registration: the named table must exist and carry an integer
`id` primary key (spec edge case: "refused at registration time — never a runtime
surprise"). Dynamic table names are composed with psycopg.sql.Identifier only
AFTER validation; interpolated strings are forbidden by the constitution.
"""
import os

import psycopg
import pytest

from gefion.db import schema
from gefion.entities import registry


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    c = _conn()
    with c.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS enttest_ok, enttest_badpk")
        cur.execute("CREATE TABLE enttest_ok (id SERIAL PRIMARY KEY, name TEXT)")
        cur.execute("CREATE TABLE enttest_badpk (key TEXT PRIMARY KEY)")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'enttest_%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'enttest_%'")
        cur.execute("DROP TABLE IF EXISTS enttest_ok, enttest_badpk")
    c.close()


# --- validate_entity_table ----------------------------------------------------

def test_stocks_is_a_valid_entity_table(conn):
    registry.validate_entity_table(conn, "stocks")  # must not raise


def test_any_table_with_integer_id_pk_is_valid(conn):
    registry.validate_entity_table(conn, "enttest_ok")


def test_nonexistent_table_refused(conn):
    with pytest.raises(registry.EntityTableError):
        registry.validate_entity_table(conn, "no_such_table")


def test_table_without_integer_id_pk_refused(conn):
    with pytest.raises(registry.EntityTableError):
        registry.validate_entity_table(conn, "enttest_badpk")


def test_injection_shaped_names_refused(conn):
    """Validation happens BEFORE any identifier composition; a hostile name
    never reaches SQL."""
    for bad in ("stocks; DROP TABLE stocks", 'stocks"', "stocks--"):
        with pytest.raises(registry.EntityTableError):
            registry.validate_entity_table(conn, bad)


def test_entity_identifier_composes_safely(conn):
    """entity_identifier returns a psycopg Composable usable in queries —
    only for already-validated tables."""
    ident = registry.entity_identifier(conn, "enttest_ok")
    with conn.cursor() as cur:
        from psycopg import sql
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(ident))
        assert cur.fetchone()[0] == 0


# --- registration hook ----------------------------------------------------------

def test_registration_accepts_declared_entity_table(conn):
    from gefion.db.ingest import ensure_feature_definitions
    ids = ensure_feature_definitions(conn, [{
        "name": "enttest_feature", "function_name": "indicator",
        "params": None, "source_table": "enttest_ok", "source_column": "name",
        "store_table": "computed_features", "store_column": "value",
        "store_type": "double precision", "active": True,
        "entity_table": "enttest_ok",
    }])
    assert "enttest_feature" in ids
    with conn.cursor() as cur:
        cur.execute("SELECT entity_table FROM feature_definitions WHERE name = 'enttest_feature'")
        assert cur.fetchone()[0] == "enttest_ok"


def test_registration_defaults_to_stocks(conn):
    from gefion.db.ingest import ensure_feature_definitions
    ensure_feature_definitions(conn, [{
        "name": "enttest_default", "function_name": "indicator",
        "params": None, "source_table": "stock_ohlcv", "source_column": "close",
        "store_table": "computed_features", "store_column": "value",
        "store_type": "double precision", "active": True,
    }])
    with conn.cursor() as cur:
        cur.execute("SELECT entity_table FROM feature_definitions WHERE name = 'enttest_default'")
        assert cur.fetchone()[0] == "stocks"


def test_registration_refuses_undeclared_entity_table(conn):
    """The spec edge case: refused at registration, never a runtime surprise."""
    from gefion.db.ingest import ensure_feature_definitions
    with pytest.raises(registry.EntityTableError):
        ensure_feature_definitions(conn, [{
            "name": "enttest_bad", "function_name": "indicator",
            "params": None, "source_table": "x", "source_column": "y",
            "store_table": "computed_features", "store_column": "value",
            "store_type": "double precision", "active": True,
            "entity_table": "no_such_table",
        }])
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM feature_definitions WHERE name = 'enttest_bad'")
        assert cur.fetchone()[0] == 0


# --- enumeration (feeds the orphan scan and the feeds graph) --------------------

def test_declared_entity_tables_enumerates_distinct(conn):
    from gefion.db.ingest import ensure_feature_definitions
    # Register one default (stocks) and one declared definition ourselves —
    # earlier suite modules may have deleted the seeded definitions (shared
    # test DB — issue #29 lesson), so neither may be assumed present.
    ensure_feature_definitions(conn, [{
        "name": "enttest_feature_stock", "function_name": "indicator",
        "params": None, "source_table": "stock_ohlcv", "source_column": "close",
        "store_table": "computed_features", "store_column": "value",
        "store_type": "double precision", "active": True,
    }, {
        "name": "enttest_feature2", "function_name": "indicator",
        "params": None, "source_table": "enttest_ok", "source_column": "name",
        "store_table": "computed_features", "store_column": "value",
        "store_type": "double precision", "active": True,
        "entity_table": "enttest_ok",
    }])
    declared = registry.declared_entity_tables(conn)
    assert "stocks" in declared
    assert "enttest_ok" in declared
