"""Universe provenance on cross-sectional rankings (issue #153).

TDD: written FIRST. A stored decile rank is only meaningful relative to the
population it was ranked within. These tests drive:

- a canonical schema creator for cross_sectional_features (the table
  previously existed only as a historical migration — fresh databases,
  including gefion_test, lacked it entirely);
- universe_name + universe_fingerprint provenance columns (the spec-015
  ml_datasets.universe pattern) stamped by the compute/store path;
- the backtest population stamp used by cross_sectional_decile runs.

Legacy posture: rows written before this change carry NULL universe columns.
NULL means "population unknown" (pre-015 unfiltered market or post-015
default universe — not distinguishable after the fact). Readers must never
silently treat NULL as modeling_default: absence of data is not evidence.
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


SYMS = {
    "XUP_OK": ("SOFTWARE - APPLICATION", "Stock"),
    "XUP_SPAC": ("SHELL COMPANIES", "Stock"),
}


def _cleanup(c):
    with c.cursor() as cur:
        cur.execute("DELETE FROM universe_definitions WHERE name LIKE 'xup_%'")
        cur.execute("DELETE FROM universe_definitions "
                    "WHERE name = 'modeling_default'")
        cur.execute(
            "DELETE FROM cross_sectional_features WHERE data_id IN "
            "(SELECT id FROM stocks WHERE symbol LIKE 'XUP_%')")
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions "
                    " WHERE name = 'xup_feat')")
        cur.execute("DELETE FROM feature_definitions WHERE name = 'xup_feat'")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'XUP_%'")


@pytest.fixture
def conn():
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_universe_definitions_table(c)
    schema.create_universe_exclusions_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_cross_sectional_features_table(c)
    _cleanup(c)
    with c.cursor() as cur:
        for sym, (ind, at) in SYMS.items():
            cur.execute(
                "INSERT INTO stocks (symbol, status, industry, asset_type, "
                "sector) VALUES (%s, 'Active', %s, %s, 'TECHNOLOGY')",
                (sym, ind, at))
    yield c
    _cleanup(c)
    # restore the canonical db-init state (seeded default) for later suites
    from gefion.universe.definitions import seed_default_universe
    seed_default_universe(c)
    c.close()


def _seed_and_refresh_default(conn):
    from gefion.universe.definitions import seed_default_universe
    from gefion.universe.membership import refresh_universe
    seed_default_universe(conn)
    refresh_universe(conn, "modeling_default")


def _seed_feature_values(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feature_definitions (name, function_name) "
            "VALUES ('xup_feat', 'indicator') ON CONFLICT (name) DO NOTHING")
        cur.execute(
            "INSERT INTO computed_features (data_id, feature_id, date, value) "
            "SELECT s.id, fd.id, '2024-01-02', s.id * 1.0 "
            "FROM stocks s, feature_definitions fd "
            "WHERE s.symbol LIKE 'XUP_%' AND fd.name = 'xup_feat'")


def _stored_rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.symbol, x.comparison_group, x.universe_name, "
            "       x.universe_fingerprint "
            "FROM cross_sectional_features x JOIN stocks s ON s.id = x.data_id "
            "WHERE s.symbol LIKE 'XUP_%'")
        return cur.fetchall()


class TestCanonicalCreator:
    """The table must be creatable via the canonical schema.py path."""

    def test_creator_builds_table_with_universe_columns(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name = 'cross_sectional_features'")
            cols = dict(cur.fetchall())
        assert "universe_name" in cols
        assert "universe_fingerprint" in cols
        # NULL = pre-provenance legacy rows; the columns must stay nullable
        assert cols["universe_name"] == "YES"
        assert cols["universe_fingerprint"] == "YES"
        # the ranking key is unchanged
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                " AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'cross_sectional_features'::regclass "
                " AND i.indisprimary")
            pk = {r[0] for r in cur.fetchall()}
        assert pk == {"data_id", "date", "feature_name", "comparison_group"}

    def test_creator_upgrades_legacy_shape(self, conn):
        """Idempotent on a pre-provenance table: columns get added."""
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS cross_sectional_features")
            cur.execute("""
                CREATE TABLE cross_sectional_features (
                    data_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    feature_name TEXT NOT NULL,
                    comparison_group TEXT NOT NULL DEFAULT 'market',
                    value DOUBLE PRECISION,
                    rank INTEGER,
                    percentile DOUBLE PRECISION,
                    created_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (data_id, date, feature_name, comparison_group)
                )
            """)
        schema.create_cross_sectional_features_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'cross_sectional_features'")
            cols = {r[0] for r in cur.fetchall()}
        assert {"universe_name", "universe_fingerprint"} <= cols

    def test_registered_in_init_schema_tables(self, conn):
        from gefion.cli_helpers import init_schema_tables
        init_schema_tables(conn, ["cross_sectional_features"])


class TestStoreStampsUniverse:
    def test_store_stamps_resolved_universe(self, conn):
        from gefion.compute.cross_sectional import (
            store_cross_sectional_rankings)
        from gefion.universe import resolve_universe
        from gefion.universe.definitions import define_universe
        define_universe(conn, "xup_u", rules=[])
        resolved = resolve_universe(conn, "xup_u")
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM stocks WHERE symbol = 'XUP_OK'")
            data_id = cur.fetchone()[0]
        rankings = [{"symbol": "XUP_OK", "data_id": data_id, "value": 1.0,
                     "rank": 1, "percentile": 1.0,
                     "comparison_group": "market"}]
        n = store_cross_sectional_rankings(
            conn, rankings, "xup_feat", date(2024, 1, 2), universe=resolved)
        assert n == 1
        rows = _stored_rows(conn)
        assert rows == [("XUP_OK", "market", "xup_u", resolved.fingerprint)]
        assert resolved.fingerprint.startswith("sha256:")

    def test_recompute_refreshes_stamp(self, conn):
        """ON CONFLICT rewrite must update the stamp, never leave it stale."""
        from gefion.compute.cross_sectional import (
            store_cross_sectional_rankings)
        from gefion.universe import ResolvedUniverse
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM stocks WHERE symbol = 'XUP_OK'")
            data_id = cur.fetchone()[0]
        rankings = [{"symbol": "XUP_OK", "data_id": data_id, "value": 1.0,
                     "rank": 1, "percentile": 1.0,
                     "comparison_group": "market"}]
        store_cross_sectional_rankings(
            conn, rankings, "xup_feat", date(2024, 1, 2),
            universe=ResolvedUniverse("xup_a", None, "sha256:aaa"))
        store_cross_sectional_rankings(
            conn, rankings, "xup_feat", date(2024, 1, 2),
            universe=ResolvedUniverse("xup_b", None, "sha256:bbb"))
        rows = _stored_rows(conn)
        assert rows == [("XUP_OK", "market", "xup_b", "sha256:bbb")]

    def test_unstamped_rows_stay_null_not_default(self, conn):
        """Legacy posture: no universe given -> NULL, and NULL is not
        modeling_default. Absence of data is never evidence."""
        from gefion.compute.cross_sectional import (
            store_cross_sectional_rankings)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM stocks WHERE symbol = 'XUP_OK'")
            data_id = cur.fetchone()[0]
        rankings = [{"symbol": "XUP_OK", "data_id": data_id, "value": 1.0,
                     "rank": 1, "percentile": 1.0,
                     "comparison_group": "market"}]
        store_cross_sectional_rankings(
            conn, rankings, "xup_feat", date(2024, 1, 2))
        rows = _stored_rows(conn)
        assert rows == [("XUP_OK", "market", None, None)]
        # a reader filtering by universe must NOT see legacy rows
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM cross_sectional_features "
                "WHERE universe_name = 'modeling_default'")
            assert cur.fetchone()[0] == 0


class TestInsertHelperPassThrough:
    def test_insert_helper_carries_universe_keys(self, conn):
        from gefion.db.cross_sectional import insert_cross_sectional_features
        features = [{
            "symbol": "XUP_OK",
            "date": "2024-01-02",
            "feature_name": "xup_feat",
            "comparison_group": "market",
            "value": 1.0,
            "rank": 1,
            "percentile": 1.0,
            "universe_name": "xup_u",
            "universe_fingerprint": "sha256:abc",
        }]
        assert insert_cross_sectional_features(conn, features) == 1
        assert _stored_rows(conn) == [
            ("XUP_OK", "market", "xup_u", "sha256:abc")]

    def test_insert_helper_defaults_to_null_stamp(self, conn):
        from gefion.db.cross_sectional import insert_cross_sectional_features
        features = [{
            "symbol": "XUP_OK",
            "date": "2024-01-02",
            "feature_name": "xup_feat",
            "value": 1.0,
            "rank": 1,
            "percentile": 1.0,
        }]
        assert insert_cross_sectional_features(conn, features) == 1
        assert _stored_rows(conn) == [("XUP_OK", "market", None, None)]


class TestComputePathStampsUniverse:
    def test_compute_and_store_stamps_default_universe(self, conn):
        from gefion.compute.cross_sectional import compute_and_store_rankings
        _seed_and_refresh_default(conn)
        _seed_feature_values(conn)
        result = compute_and_store_rankings(
            conn, "xup_feat", target_date=date(2024, 1, 2),
            include_sectors=False)
        assert result["success"]
        assert result["universe_name"] == "modeling_default"
        assert result["universe_fingerprint"].startswith("sha256:")
        rows = _stored_rows(conn)
        assert rows, "rankings were stored"
        assert all(r[2] == "modeling_default" for r in rows)
        assert all(r[3] == result["universe_fingerprint"] for r in rows)
        # the gate still filters the population: SPAC excluded by default
        assert all(r[0] != "XUP_SPAC" for r in rows)

    def test_compute_and_store_stamps_all(self, conn):
        from gefion.compute.cross_sectional import compute_and_store_rankings
        _seed_and_refresh_default(conn)
        _seed_feature_values(conn)
        result = compute_and_store_rankings(
            conn, "xup_feat", target_date=date(2024, 1, 2),
            include_sectors=False, universe="all")
        assert result["success"]
        assert result["universe_name"] == "all"
        assert result["universe_fingerprint"] is None
        rows = _stored_rows(conn)
        assert {r[0] for r in rows} == {"XUP_OK", "XUP_SPAC"}
        assert all(r[2] == "all" and r[3] is None for r in rows)

    def test_unknown_universe_refuses(self, conn):
        from gefion.compute.cross_sectional import compute_and_store_rankings
        from gefion.universe import UniverseResolutionError
        _seed_and_refresh_default(conn)
        _seed_feature_values(conn)
        with pytest.raises(UniverseResolutionError):
            compute_and_store_rankings(
                conn, "xup_feat", target_date=date(2024, 1, 2),
                universe="xup_nope")


class TestBacktestPopulationStamp:
    """cross_sectional_decile ranks the population the backtest feeds it —
    the run must record which universe that population came from."""

    def test_explicit_symbols_are_their_own_universe(self):
        # no DB needed: explicit lists bypass the gate (015 convention)
        from gefion.backtest.data_loader import resolve_backtest_universe
        stamp = resolve_backtest_universe(
            "postgresql://unused/unused", ["AAPL", "MSFT"], None)
        assert stamp == {"universe_name": "explicit",
                         "universe_fingerprint": None}

    def test_gated_population_stamps_resolved_universe(self, conn):
        from gefion.backtest.data_loader import resolve_backtest_universe
        _seed_and_refresh_default(conn)
        stamp = resolve_backtest_universe(schema.test_db_url(), None, None)
        assert stamp["universe_name"] == "modeling_default"
        assert stamp["universe_fingerprint"].startswith("sha256:")
        stamp_all = resolve_backtest_universe(
            schema.test_db_url(), None, "all")
        assert stamp_all == {"universe_name": "all",
                             "universe_fingerprint": None}
