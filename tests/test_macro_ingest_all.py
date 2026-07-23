"""`macro ingest --all`: refresh every registered EXTERNAL series (017).

TDD: written FIRST. The nightly chain needs one idempotent step that keeps
every provider-backed macro series fresh (VIX went stale for two weeks
because refresh was per-series and in no cron). Derived/materialized series
are never touched — they have their own pipelines — and one failing
provider must not stop the others (reported, not raised).
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


@pytest.fixture
def conn():
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_macro_series_tables(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    with c.cursor() as cur:
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'qma_%'")
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_qma_%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_qma_%'")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'qma_%'")
    c.close()


def _rows(_provider, _full):
    return [{"date": "2026-07-01", "value": 17.5},
            {"date": "2026-07-02", "value": 18.0}]


class TestRefreshAllSeries:
    def test_refreshes_external_skips_derived(self, conn):
        from gefion.macro import catalog
        from gefion.macro.ingest import ingest_series, refresh_all_series
        ingest_series(conn, "qma_vix", provider="fred:QMAVIX", kind="index",
                      cadence="daily", fetch=_rows)
        ingest_series(conn, "qma_rate", provider="fred:QMARATE", kind="rate",
                      cadence="daily", fetch=_rows)
        catalog.ensure_series(conn, "qma_derived", provider="derived",
                              kind="derived", cadence="daily")
        calls = []

        def fetch(provider, full):
            calls.append((provider, full))
            return _rows(provider, full)

        result = refresh_all_series(conn, fetch=fetch)
        providers = {p for p, _ in calls}
        assert providers == {"fred:QMAVIX", "fred:QMARATE"}
        assert all(full is False for _, full in calls)   # incremental
        refreshed = {r["series"] for r in result["refreshed"]}
        assert refreshed == {"qma_vix", "qma_rate"}
        assert result["failed"] == {}
        assert "qma_derived" not in refreshed

    def test_one_failure_does_not_stop_the_rest(self, conn):
        from gefion.macro.ingest import ingest_series, refresh_all_series
        ingest_series(conn, "qma_ok", provider="fred:QMAOK", kind="index",
                      cadence="daily", fetch=_rows)
        ingest_series(conn, "qma_bad", provider="fred:QMABAD", kind="index",
                      cadence="daily", fetch=_rows)

        def fetch(provider, full):
            if "BAD" in provider:
                raise RuntimeError("provider down")
            return _rows(provider, full)

        result = refresh_all_series(conn, fetch=fetch)
        assert {r["series"] for r in result["refreshed"]} == {"qma_ok"}
        assert "qma_bad" in result["failed"]
        assert "provider down" in result["failed"]["qma_bad"]


class TestCLI:
    def test_ingest_all_flag_exists_and_refuses_with_name(self):
        from typer.testing import CliRunner

        from gefion.cli import app
        r = CliRunner().invoke(app, ["macro", "ingest", "--help"])
        assert r.exit_code == 0 and "--all" in r.output
        r = CliRunner().invoke(app, ["macro", "ingest", "--all",
                                     "--name", "vix",
                                     "--db-url", schema.test_db_url()])
        assert r.exit_code != 0        # --all and --name are exclusive
