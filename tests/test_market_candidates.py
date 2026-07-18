"""Candidate store + gate tests for generated market features (014).

TDD: written FIRST. The gate invariant is structural: candidates live in
market_function_candidates, never in feature_functions, so pending/rejected
generated code has no execution path. These tests cover the owner-approved
schema (T001), the store primitives (T004), and the review gate + atomic
promotion (T007).
"""
import os
from datetime import date

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


@pytest.fixture
def conn():
    c = _conn()
    schema.create_market_function_candidates_table(c)

    def _cleanup(cur):
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'mfc_test_%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'mfc_test_%'")
        cur.execute("DELETE FROM market_function_candidates WHERE name LIKE 'mfc_test_%'")

    with c.cursor() as cur:
        _cleanup(cur)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


# --- T001: owner-approved schema (DDL approved 2026-07-18) -------------------------

class TestCandidateSchema:
    def test_table_exists_with_expected_columns(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'market_function_candidates'"
            )
            cols = {r[0] for r in cur.fetchall()}
        expected = {
            "id", "name", "version", "kind", "function_body", "inputs",
            "description", "origin", "principle_id", "generator", "dry_run",
            "review_state", "reviewed_by", "reviewed_at", "review_reason",
            "promoted_function_id", "created_at",
        }
        assert expected.issubset(cols), f"missing columns: {expected - cols}"

    def test_creator_is_idempotent(self, conn):
        schema.create_market_function_candidates_table(conn)
        schema.create_market_function_candidates_table(conn)

    def test_kind_and_state_constrained(self, conn):
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO market_function_candidates "
                    "(name, kind, function_body, origin) "
                    "VALUES ('mfc_test_bad', 'per_stock', 'def compute(rows): pass', 'template')"
                )
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO market_function_candidates "
                    "(name, kind, function_body, origin, review_state) "
                    "VALUES ('mfc_test_bad', 'cross_section', 'x', 'template', 'maybe')"
                )

    def test_name_version_unique(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO market_function_candidates "
                "(name, version, kind, function_body, origin) "
                "VALUES ('mfc_test_uq', 1, 'cross_section', 'x', 'template')"
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO market_function_candidates "
                    "(name, version, kind, function_body, origin) "
                    "VALUES ('mfc_test_uq', 1, 'cross_section', 'y', 'template')"
                )

    def test_schema_sql_carries_the_table(self):
        """Two-file rule: schema.sql is the canonical DDL."""
        from pathlib import Path
        import gefion
        root = Path(gefion.__file__).parent.parent.parent
        assert "market_function_candidates" in (root / "sql" / "schema.sql").read_text()
        migrations = list((root / "sql" / "migrations").glob("*market_function_candidates*"))
        assert migrations, "migration file missing (two-file rule)"
