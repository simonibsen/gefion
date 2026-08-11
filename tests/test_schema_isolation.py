"""
TDD tests for the cross-test schema isolation guard (issue #195).

All DB tests share one `public` schema in `gefion_test`. Modules that DROP
and recreate canonical tables without restoring afterward can leave the
schema mutated for whichever module happens to run next — including an
orphaned SERIAL sequence, which collides with a later `CREATE TABLE ...
SERIAL` (`pg_class_relname_nsp_index` UniqueViolation).

These tests drive `schema_fingerprint()`, the helper the module-scoped
autouse guard in conftest.py uses to detect that drift.
"""
import os

import psycopg
import pytest

from gefion.db import schema


def require_db():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")


@pytest.fixture
def db_conn():
    require_db()
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="module", autouse=True)
def _restore_after_module():
    """This module intentionally mutates the schema to prove the guard
    catches it — restore canonical state afterward (issue #195)."""
    yield
    if os.getenv("ENABLE_DB_TESTS") == "1":
        from conftest import restore_test_db
        restore_test_db()


def test_fingerprint_detects_dropped_canonical_table(db_conn):
    from conftest import schema_fingerprint, restore_test_db

    before = schema_fingerprint(db_conn)
    assert ("feature_definitions", "r") in before

    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS feature_definitions CASCADE")

    after = schema_fingerprint(db_conn)
    assert after != before
    assert ("feature_definitions", "r") not in after

    # Independent of test order: leave canonical state as found.
    restore_test_db()


def test_fingerprint_detects_orphaned_sequence(db_conn):
    """Reproduces the ml_datasets_id_seq failure directly: a table backed by
    a sequence that isn't OWNED BY the column survives DROP TABLE, so a later
    `CREATE TABLE ... SERIAL` collides with the leftover sequence name."""
    from conftest import schema_fingerprint

    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS isoseqtest CASCADE")
        cur.execute("DROP SEQUENCE IF EXISTS isoseqtest_id_seq CASCADE")
        cur.execute("CREATE SEQUENCE isoseqtest_id_seq")
        cur.execute(
            """
            CREATE TABLE isoseqtest (
                id INTEGER PRIMARY KEY DEFAULT nextval('isoseqtest_id_seq')
            )
            """
        )

    before = schema_fingerprint(db_conn)
    assert ("isoseqtest", "r") in before
    assert ("isoseqtest_id_seq", "S") in before

    try:
        with db_conn.cursor() as cur:
            # Recreate the table WITHOUT the sequence — the table is gone but
            # the unowned sequence is left behind, orphaned.
            cur.execute("DROP TABLE isoseqtest")

        after = schema_fingerprint(db_conn)
        assert after != before
        assert ("isoseqtest", "r") not in after
        assert ("isoseqtest_id_seq", "S") in after, (
            "orphaned sequence must still show up in the fingerprint"
        )
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DROP SEQUENCE IF EXISTS isoseqtest_id_seq CASCADE")


def test_restore_test_db_returns_to_canonical_fingerprint(db_conn):
    from conftest import schema_fingerprint, restore_test_db

    canonical = schema_fingerprint(db_conn)

    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS feature_definitions CASCADE")
    assert schema_fingerprint(db_conn) != canonical

    restore_test_db()

    with psycopg.connect(schema.test_db_url()) as fresh:
        fresh.autocommit = True
        restored = schema_fingerprint(fresh)
    assert restored == canonical
