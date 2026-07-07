"""Fresh-holdout reserve tests (006, T027 — US3 expressive tier).

TDD: written FIRST. Free-form/detector candidates are admissible only against
a declared, dated reserve block distinct from the outer holdout, and a block
is SINGLE-USE: fresh-holdout honesty is entirely about non-reuse (R4), so
consumption is a DB fact and re-declaring a consumed block is refused unless
explicitly justified — and the justification is recorded.
"""
import os

import psycopg
import pytest
from psycopg.types.json import Json

from gefion.db import schema
from gefion.regimes.discovery import freshhold

BOUNDARIES = {
    "inner_start": "2020-01-06",
    "inner_end": "2021-06-30",
    "holdout_start": "2021-07-01",
    "holdout_end": "2021-11-19",
}


# --- declaration validity (no DB) --------------------------------------------

def test_valid_reserve_is_normalized():
    reserve = freshhold.validate_reserve(BOUNDARIES, "2020-06-01", "2020-09-01")
    assert reserve == {"start": "2020-06-01", "end": "2020-09-01"}


def test_reserve_must_be_a_forward_block():
    with pytest.raises(freshhold.ReserveError):
        freshhold.validate_reserve(BOUNDARIES, "2020-09-01", "2020-06-01")


def test_reserve_must_not_overlap_the_outer_holdout():
    """The reserve is DISTINCT from the outer holdout — overlapping blocks
    would let expressive candidates peek at the judge."""
    with pytest.raises(freshhold.ReserveError):
        freshhold.validate_reserve(BOUNDARIES, "2021-06-01", "2021-08-01")
    with pytest.raises(freshhold.ReserveError):
        freshhold.validate_reserve(BOUNDARIES, "2021-07-01", "2021-11-19")


# --- single-use consumption (DB) ---------------------------------------------

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
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'freshtest-%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'freshtest-%'")
    c.close()


def _consumed_run(conn, name, start, end):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO regime_discovery_runs
                   (name, seed, search_space, segregation, dataset_version,
                    reserve_consumed)
               VALUES (%s, 1, %s, %s, 'test', TRUE) RETURNING id""",
            (name, Json({"signal_source": "features", "grading_scheme": "walk_forward",
                         "universe_filter": ["passthrough"]}),
             Json({**BOUNDARIES, "reserve": {"start": start, "end": end}})),
        )
        return cur.fetchone()[0]


def test_unconsumed_block_is_available(conn):
    reserve = freshhold.require_reserve(conn, BOUNDARIES, "2020-06-01", "2020-09-01")
    assert reserve["start"] == "2020-06-01"
    assert "justification" not in reserve


def test_consumed_block_is_refused_without_justification(conn):
    _consumed_run(conn, "freshtest-prior", "2020-06-01", "2020-09-01")
    with pytest.raises(freshhold.ReserveError):
        freshhold.require_reserve(conn, BOUNDARIES, "2020-06-01", "2020-09-01")
    # any overlap with the consumed block counts as reuse
    with pytest.raises(freshhold.ReserveError):
        freshhold.require_reserve(conn, BOUNDARIES, "2020-08-15", "2020-12-01")


def test_redeclaration_with_justification_is_recorded(conn):
    prior = _consumed_run(conn, "freshtest-prior2", "2020-06-01", "2020-09-01")
    reserve = freshhold.require_reserve(
        conn, BOUNDARIES, "2020-06-01", "2020-09-01",
        justification="new dataset version invalidates prior consumption")
    assert reserve["justification"].startswith("new dataset version")
    assert prior in reserve["overlaps_consumed_runs"]


def test_consume_is_single_use(conn):
    run_id = _consumed_run(conn, "freshtest-consume", "2019-01-01", "2019-03-01")
    with conn.cursor() as cur:
        cur.execute("UPDATE regime_discovery_runs SET reserve_consumed = FALSE WHERE id = %s",
                    (run_id,))
    freshhold.consume(conn, run_id)
    with pytest.raises(freshhold.ReserveError):
        freshhold.consume(conn, run_id)
