"""The universe chokepoint (015, US1).

TDD: written FIRST. One gate through which every modeling cross-section
consumer obtains its population. Resolution order: explicit name wins,
reserved name 'all' bypasses filtering, otherwise the default universe.
Unknown or disabled universes REFUSE loudly (naming valid choices) — never a
silent fallback to "everything". Streaming-SQL consumers compose the gate as
a NOT EXISTS clause instead of symbol lists.

Consumer-routing tests (dataset build, market functions, rankings, backtest
loader) are added in the US1 sweep and live in the later classes here.
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
    "QUC_OK": ("SOFTWARE - APPLICATION", "Stock"),
    "QUC_SPAC": ("SHELL COMPANIES", "Stock"),
    "QUC_ETF": (None, "ETF"),
}


def _cleanup(c):
    with c.cursor() as cur:
        cur.execute("DELETE FROM universe_definitions WHERE name LIKE 'quc_%'")
        cur.execute("DELETE FROM universe_definitions WHERE name = 'modeling_default'")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QUC_%'")


@pytest.fixture
def conn():
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_universe_definitions_table(c)
    schema.create_universe_exclusions_table(c)
    _cleanup(c)
    with c.cursor() as cur:
        for sym, (ind, at) in SYMS.items():
            cur.execute(
                "INSERT INTO stocks (symbol, status, industry, asset_type) "
                "VALUES (%s, 'Active', %s, %s)", (sym, ind, at))
            cur.execute(
                "INSERT INTO stock_ohlcv (data_id, date, close) "
                "SELECT id, '2024-01-02', 10.0 FROM stocks WHERE symbol = %s",
                (sym,))
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


class TestResolve:
    def test_explicit_name_wins(self, conn):
        from gefion.universe import resolve_universe
        from gefion.universe.definitions import define_universe
        define_universe(conn, "quc_a", rules=[])
        r = resolve_universe(conn, "quc_a")
        assert r.name == "quc_a" and r.universe_id is not None
        assert r.fingerprint.startswith("sha256:")

    def test_all_bypasses(self, conn):
        from gefion.universe import resolve_universe
        r = resolve_universe(conn, "all")
        assert r.name == "all" and r.universe_id is None

    def test_none_resolves_default(self, conn):
        from gefion.universe import resolve_universe
        _seed_and_refresh_default(conn)
        r = resolve_universe(conn, None)
        assert r.name == "modeling_default"

    def test_unknown_refuses_naming_valid(self, conn):
        from gefion.universe import UniverseResolutionError, resolve_universe
        from gefion.universe.definitions import define_universe
        define_universe(conn, "quc_known", rules=[])
        with pytest.raises(UniverseResolutionError) as exc:
            resolve_universe(conn, "quc_nope")
        assert "quc_known" in str(exc.value)

    def test_disabled_refuses(self, conn):
        from gefion.universe import UniverseResolutionError, resolve_universe
        from gefion.universe.definitions import define_universe, set_enabled
        define_universe(conn, "quc_off", rules=[])
        set_enabled(conn, "quc_off", False)
        with pytest.raises(UniverseResolutionError):
            resolve_universe(conn, "quc_off")

    def test_no_default_refuses_with_guidance(self, conn):
        from gefion.universe import UniverseResolutionError, resolve_universe
        with pytest.raises(UniverseResolutionError) as exc:
            resolve_universe(conn, None)
        assert "db-init" in str(exc.value) or "modeling_default" in str(exc.value)


class TestMembersAndClause:
    def test_members_respect_default_and_all(self, conn):
        from gefion.universe import universe_members
        _seed_and_refresh_default(conn)
        members = universe_members(conn)                 # default universe
        assert "QUC_OK" in members
        assert "QUC_SPAC" not in members and "QUC_ETF" not in members
        everything = universe_members(conn, "all")
        assert {"QUC_OK", "QUC_SPAC", "QUC_ETF"} <= set(everything)

    def test_member_ids_matches_members(self, conn):
        from gefion.universe import universe_member_ids, universe_members
        _seed_and_refresh_default(conn)
        ids = universe_member_ids(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM stocks WHERE symbol = 'QUC_OK'")
            ok_id = cur.fetchone()[0]
            cur.execute("SELECT id FROM stocks WHERE symbol = 'QUC_SPAC'")
            spac_id = cur.fetchone()[0]
        assert ok_id in ids and spac_id not in ids
        assert len(ids) == len(universe_members(conn))

    def test_exclusion_clause_filters_in_sql(self, conn):
        """The streaming-SQL form: composing the clause into a query over
        stock_ohlcv drops excluded symbols per date."""
        from gefion.universe import resolve_universe, universe_exclusion_clause
        _seed_and_refresh_default(conn)
        r = resolve_universe(conn, None)
        clause, params = universe_exclusion_clause(r.universe_id, "o.date", "o.data_id")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.symbol FROM stock_ohlcv o JOIN stocks s ON s.id = o.data_id "
                "WHERE s.symbol LIKE 'QUC_%%' AND " + clause, params)
            got = {row[0] for row in cur.fetchall()}
        assert got == {"QUC_OK"}

    def test_exclusion_clause_for_all_is_true(self, conn):
        from gefion.universe import universe_exclusion_clause
        clause, params = universe_exclusion_clause(None, "o.date", "o.data_id")
        assert clause.strip() == "TRUE" and params == []


class TestConsumerRouting:
    """US1: the modeling consumers draw their population from the gate."""

    def test_dataset_symbols_respect_universe(self, conn):
        from gefion.ml.dataset import resolve_universe_symbols
        _seed_and_refresh_default(conn)
        syms = resolve_universe_symbols(conn, {})          # default universe
        assert "QUC_OK" in syms
        assert "QUC_SPAC" not in syms and "QUC_ETF" not in syms
        syms_all = resolve_universe_symbols(conn, {"universe": "all"})
        assert {"QUC_OK", "QUC_SPAC", "QUC_ETF"} <= set(syms_all)
        # explicit symbols still win verbatim (documented bypass)
        explicit = resolve_universe_symbols(conn, {"symbols": ["QUC_SPAC"]})
        assert explicit == ["QUC_SPAC"]

    def test_market_function_cross_section_respects_universe(self, conn):
        """The breadth/dispersion population (run_market_function) excludes
        rule-excluded symbols under the default universe; 'all' reproduces
        pre-015 behavior (asset_type='Stock', unfiltered by rules)."""
        from gefion.features.dispatcher import run_market_function
        _seed_and_refresh_default(conn)
        fn_row = {
            "id": 999999, "name": "quc_probe",
            "function_body": ("def compute(rows):\n"
                              "    return float(sum(1 for r in rows "
                              "if r['symbol'].startswith('QUC_')))"),
            "inputs": {},
        }
        by_date = dict(run_market_function(
            conn, fn_row, min_stocks=1)["values"])
        assert by_date[date(2024, 1, 2)] == 1.0     # QUC_OK only
        by_date_all = dict(run_market_function(
            conn, fn_row, min_stocks=1, universe="all")["values"])
        assert by_date_all[date(2024, 1, 2)] == 2.0  # + QUC_SPAC (ETF: no bars in cross-section by asset_type)

    def test_cross_sectional_population_respects_universe(self, conn):
        from gefion.compute.cross_sectional import fetch_feature_with_sectors
        from gefion.db import schema as dbschema
        _seed_and_refresh_default(conn)
        dbschema.create_feature_definitions_table(conn)
        dbschema.create_computed_features_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO feature_definitions (name, function_name) "
                "VALUES ('quc_feat', 'indicator') "
                "ON CONFLICT (name) DO NOTHING")
            cur.execute("SELECT id FROM feature_definitions WHERE name = 'quc_feat'")
            fid = cur.fetchone()[0]
            cur.execute("DELETE FROM computed_features WHERE feature_id = %s", (fid,))
            cur.execute(
                "INSERT INTO computed_features (data_id, feature_id, date, value) "
                "SELECT id, %s, '2024-01-02', 1.5 FROM stocks "
                "WHERE symbol LIKE 'QUC_%%'", (fid,))
        try:
            got = {r["symbol"] for r in
                   fetch_feature_with_sectors(conn, "quc_feat",
                                              target_date=date(2024, 1, 2))}
            assert "QUC_OK" in got
            assert "QUC_SPAC" not in got and "QUC_ETF" not in got
            got_all = {r["symbol"] for r in
                       fetch_feature_with_sectors(conn, "quc_feat",
                                                  target_date=date(2024, 1, 2),
                                                  universe="all")}
            assert {"QUC_OK", "QUC_SPAC", "QUC_ETF"} <= got_all
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM computed_features WHERE feature_id = %s",
                            (fid,))
                cur.execute("DELETE FROM feature_definitions WHERE name = 'quc_feat'")

    def test_backtest_loader_respects_universe(self, conn):
        from gefion.backtest.data_loader import load_price_data_for_backtest
        from gefion.db import schema as dbschema
        _seed_and_refresh_default(conn)
        records = load_price_data_for_backtest(dbschema.test_db_url())
        syms = {r["symbol"] for r in records if r["symbol"].startswith("QUC_")}
        assert "QUC_OK" in syms and "QUC_SPAC" not in syms
        records_all = load_price_data_for_backtest(dbschema.test_db_url(),
                                                   universe="all")
        syms_all = {r["symbol"] for r in records_all
                    if r["symbol"].startswith("QUC_")}
        assert {"QUC_OK", "QUC_SPAC", "QUC_ETF"} <= syms_all
