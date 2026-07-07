"""Schema tests for agentic regime discovery (006) — T003.

TDD: written FIRST. Verifies the four owner-approved discovery tables
(contracts/sql.md, approved 2026-07-07) exist after db-init with the expected
columns, CHECK-constraint enums, uniqueness, and CASCADE behavior.
"""
import os

import psycopg
import pytest
from psycopg.types.json import Json

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


def _columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


def test_regime_discovery_runs_columns(conn):
    cols = _columns(conn, "regime_discovery_runs")
    expected = {
        "id", "name", "seed", "search_space", "segregation", "reserve_consumed",
        "family_size", "status", "dataset_version", "created_at", "completed_at",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_regime_candidates_columns(conn):
    cols = _columns(conn, "regime_candidates")
    expected = {
        "id", "run_id", "candidate_hash", "expression", "tier",
        "provenance", "results", "counted_in_family", "verdict",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_discovery_diagnostics_columns(conn):
    cols = _columns(conn, "discovery_diagnostics")
    expected = {"id", "run_id", "kind", "detail", "sample_dependent",
                "dataset_version", "created_at"}
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_regime_trust_grades_columns(conn):
    cols = _columns(conn, "regime_trust_grades")
    expected = {"id", "candidate_id", "fold", "confirmed", "descriptive",
                "detail", "graded_at"}
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def _insert_run(cur, name="schema-test-run", status="pre_registered"):
    cur.execute(
        """INSERT INTO regime_discovery_runs
               (name, seed, search_space, segregation, dataset_version, status)
           VALUES (%s, 42, %s, %s, 'test', %s) RETURNING id""",
        (name, Json({}), Json({}), status),
    )
    return cur.fetchone()[0]


@pytest.fixture
def clean(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'schema-test-%'")
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'schema-test-%'")


def test_run_status_check_constraint(conn, clean):
    with conn.cursor() as cur:
        for status in ("pre_registered", "enumerated", "evaluated", "complete", "invalid"):
            _insert_run(cur, f"schema-test-{status}", status)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_run(cur, "schema-test-bad", "running")


def test_candidate_tier_and_verdict_checks(conn, clean):
    with conn.cursor() as cur:
        run_id = _insert_run(cur)
        cur.execute(
            """INSERT INTO regime_candidates (run_id, candidate_hash, expression, tier, verdict)
               VALUES (%s, 'h1', %s, 'grammar', 'admitted')""",
            (run_id, Json({})),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """INSERT INTO regime_candidates (run_id, candidate_hash, expression, tier)
                   VALUES (%s, 'h2', %s, 'freeform')""",
                (run_id, Json({})),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """INSERT INTO regime_candidates (run_id, candidate_hash, expression, tier, verdict)
                   VALUES (%s, 'h3', %s, 'grammar', 'maybe')""",
                (run_id, Json({})),
            )


def test_candidate_hash_unique_per_run(conn, clean):
    with conn.cursor() as cur:
        run_id = _insert_run(cur)
        cur.execute(
            """INSERT INTO regime_candidates (run_id, candidate_hash, expression, tier)
               VALUES (%s, 'dup', %s, 'grammar')""",
            (run_id, Json({})),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                """INSERT INTO regime_candidates (run_id, candidate_hash, expression, tier)
                   VALUES (%s, 'dup', %s, 'grammar')""",
                (run_id, Json({})),
            )


def test_trust_grade_unique_and_descriptive_separation(conn, clean):
    with conn.cursor() as cur:
        run_id = _insert_run(cur)
        cur.execute(
            """INSERT INTO regime_candidates (run_id, candidate_hash, expression, tier)
               VALUES (%s, 'g1', %s, 'grammar') RETURNING id""",
            (run_id, Json({})),
        )
        cand_id = cur.fetchone()[0]
        # same fold may hold one forward confirmation AND one descriptive slice
        cur.execute(
            "INSERT INTO regime_trust_grades (candidate_id, fold, confirmed) VALUES (%s, 1, true)",
            (cand_id,),
        )
        cur.execute(
            """INSERT INTO regime_trust_grades (candidate_id, fold, confirmed, descriptive)
               VALUES (%s, 1, true, true)""",
            (cand_id,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO regime_trust_grades (candidate_id, fold, confirmed) VALUES (%s, 1, false)",
                (cand_id,),
            )


def test_cascade_from_runs(conn, clean):
    """Deleting a run cascades to candidates, diagnostics, and grades."""
    with conn.cursor() as cur:
        run_id = _insert_run(cur)
        cur.execute(
            """INSERT INTO regime_candidates (run_id, candidate_hash, expression, tier)
               VALUES (%s, 'c1', %s, 'interaction') RETURNING id""",
            (run_id, Json({})),
        )
        cand_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO discovery_diagnostics
                   (run_id, kind, detail, sample_dependent, dataset_version)
               VALUES (%s, 'min_sample_refusal', %s, true, 'test')""",
            (run_id, Json({"effective_n": 3, "floor": 20})),
        )
        cur.execute(
            "INSERT INTO regime_trust_grades (candidate_id, fold, confirmed) VALUES (%s, 1, true)",
            (cand_id,),
        )
        cur.execute("DELETE FROM regime_discovery_runs WHERE id = %s", (run_id,))
        for table, col, val in (
            ("regime_candidates", "run_id", run_id),
            ("discovery_diagnostics", "run_id", run_id),
            ("regime_trust_grades", "candidate_id", cand_id),
        ):
            cur.execute(f"SELECT count(*) FROM {table} WHERE {col} = %s", (val,))
            assert cur.fetchone()[0] == 0, f"{table} did not cascade"
