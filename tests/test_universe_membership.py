"""Universe membership materialization (015, US1/US3 foundations).

TDD: written FIRST. Membership lives in COMPLEMENT form: exclusion intervals
per (universe, symbol, rule); a symbol is a member as-of D iff no interval
covers D. Static rules produce one open-ended interval per excluded symbol;
time-varying rules (close) produce gaps-and-islands intervals whose trailing
island stays open-ended. Refresh reconciles deterministically and refuses
(FR-010) rather than silently gutting downstream consumers.
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
    # symbol: (industry, asset_type)
    "QUM_AAA": ("SOFTWARE - APPLICATION", "Stock"),
    "QUM_SPAC": ("SHELL COMPANIES", "Stock"),
    "QUM_ETF": (None, "ETF"),
    "QUM_NOIND": (None, "Stock"),          # unclassified — must stay a member
    "QUM_PENNY": ("BIOTECHNOLOGY", "Stock"),
}

# QUM_PENNY closes: two sub-dollar islands, second one trailing (open-ended)
PENNY_BARS = [
    ("2024-01-02", 2.00), ("2024-01-03", 0.90), ("2024-01-04", 0.80),
    ("2024-01-05", 1.50), ("2024-01-08", 1.40), ("2024-01-09", 0.70),
    ("2024-01-10", 0.60),
]
AAA_BARS = [("2024-01-02", 10.0), ("2024-01-03", 11.0), ("2024-01-04", 12.0),
            ("2024-01-05", 11.5), ("2024-01-08", 12.5), ("2024-01-09", 13.0),
            ("2024-01-10", 13.5)]


def _cleanup(c):
    with c.cursor() as cur:
        cur.execute("DELETE FROM universe_definitions WHERE name LIKE 'qum_%'")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QUM_%'")  # cascades


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
        for sym, bars in (("QUM_PENNY", PENNY_BARS), ("QUM_AAA", AAA_BARS)):
            for d, close in bars:
                cur.execute(
                    "INSERT INTO stock_ohlcv (data_id, date, close) "
                    "SELECT id, %s, %s FROM stocks WHERE symbol = %s",
                    (d, close, sym))
    yield c
    _cleanup(c)
    c.close()


def _define(conn, name, rules, pins=None):
    from gefion.universe.definitions import define_universe
    return define_universe(conn, name, rules=rules, pins=pins or [])


RULE_SHELLS = {"name": "no-shells", "attribute": "industry", "op": "eq",
               "value": "SHELL COMPANIES", "reason": "cash boxes"}
RULE_ETFS = {"name": "no-etfs", "attribute": "asset_type", "op": "eq",
             "value": "ETF", "reason": "funds"}
RULE_PENNY = {"name": "no-penny", "attribute": "close", "op": "lt",
              "value": 1.00, "reason": "sub-dollar distortion"}


def _rows(conn, name):
    """Exclusion rows for OUR fixture symbols only — the shared test DB may
    hold stocks from other suites, so assertions never count strangers."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.symbol, e.rule_name, e.excluded_from, e.excluded_to "
            "FROM universe_exclusions e "
            "JOIN universe_definitions u ON u.id = e.universe_id "
            "JOIN stocks s ON s.id = e.data_id "
            "WHERE u.name = %s AND s.symbol LIKE 'QUM_%%' "
            "ORDER BY s.symbol, e.rule_name, e.excluded_from", (name,))
        return cur.fetchall()


def _exclude_everything_rules(conn):
    """Rules that match every stock in the DB regardless of foreign fixtures."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT asset_type FROM stocks "
                    "WHERE asset_type IS NOT NULL")
        types = [r[0] for r in cur.fetchall()]
    return [
        {"name": "all-typed", "attribute": "asset_type", "op": "in",
         "value": types, "reason": "r"},
        {"name": "untyped", "attribute": "asset_type", "op": "is_missing",
         "reason": "r"},
    ]


class TestStaticRules:
    def test_static_rules_one_open_ended_interval(self, conn):
        from gefion.universe.membership import refresh_universe
        _define(conn, "qum_static", [RULE_SHELLS, RULE_ETFS])
        delta = refresh_universe(conn, "qum_static")
        rows = _rows(conn, "qum_static")
        assert [(r[0], r[1], r[3]) for r in rows] == [
            ("QUM_ETF", "no-etfs", None),
            ("QUM_SPAC", "no-shells", None),
        ]
        assert delta["added"] >= 2 and delta["removed"] == 0

    def test_unclassified_symbol_not_silently_excluded(self, conn):
        from gefion.universe.membership import refresh_universe
        from gefion.universe import universe_members
        # ne-rule on industry: NULL industry must NOT match (absence of data
        # is not evidence of exclusion)
        _define(conn, "qum_ne", [{"name": "only-shells-out", "attribute":
                                  "industry", "op": "ne",
                                  "value": "SOFTWARE - APPLICATION",
                                  "reason": "r"}])
        refresh_universe(conn, "qum_ne")
        members = universe_members(conn, "qum_ne")
        assert "QUM_NOIND" in members
        assert "QUM_AAA" in members and "QUM_SPAC" not in members

    def test_is_missing_requires_explicit_rule(self, conn):
        from gefion.universe.membership import refresh_universe
        from gefion.universe import universe_members
        _define(conn, "qum_miss", [{"name": "must-classify", "attribute":
                                    "industry", "op": "is_missing",
                                    "reason": "r"}])
        refresh_universe(conn, "qum_miss")
        assert "QUM_NOIND" not in universe_members(conn, "qum_miss")


class TestTimeVaryingRules:
    def test_close_rule_islands_with_trailing_open(self, conn):
        from gefion.universe.membership import refresh_universe
        _define(conn, "qum_close", [RULE_PENNY])
        refresh_universe(conn, "qum_close")
        rows = [r for r in _rows(conn, "qum_close") if r[0] == "QUM_PENNY"]
        assert [(r[2], r[3]) for r in rows] == [
            (date(2024, 1, 3), date(2024, 1, 4)),
            (date(2024, 1, 9), None),  # trailing island open-ended
        ]

    def test_as_of_membership_crosses_threshold(self, conn):
        from gefion.universe.membership import refresh_universe
        from gefion.universe import universe_members
        _define(conn, "qum_asof", [RULE_PENNY])
        refresh_universe(conn, "qum_asof")
        assert "QUM_PENNY" in universe_members(conn, "qum_asof",
                                               as_of=date(2024, 1, 2))
        assert "QUM_PENNY" not in universe_members(conn, "qum_asof",
                                                   as_of=date(2024, 1, 3))
        assert "QUM_PENNY" in universe_members(conn, "qum_asof",
                                               as_of=date(2024, 1, 5))
        assert "QUM_PENNY" not in universe_members(conn, "qum_asof",
                                                   as_of=date(2024, 1, 10))


class TestReconcileAndDeterminism:
    def test_re_refresh_is_identical_and_zero_delta(self, conn):
        from gefion.universe.membership import refresh_universe
        _define(conn, "qum_det", [RULE_SHELLS, RULE_PENNY])
        refresh_universe(conn, "qum_det")
        before = _rows(conn, "qum_det")
        delta = refresh_universe(conn, "qum_det")
        assert delta["added"] == 0 and delta["removed"] == 0
        assert _rows(conn, "qum_det") == before

    def test_rule_removal_reconciles(self, conn):
        from gefion.universe.membership import refresh_universe
        _define(conn, "qum_rec", [RULE_SHELLS, RULE_ETFS])
        refresh_universe(conn, "qum_rec")
        _define(conn, "qum_rec", [RULE_SHELLS])  # drop the ETF rule
        delta = refresh_universe(conn, "qum_rec")
        assert delta["removed"] >= 1
        assert [r[0] for r in _rows(conn, "qum_rec")] == ["QUM_SPAC"]


class TestPins:
    def test_pins_beat_rules(self, conn):
        from gefion.universe.membership import refresh_universe
        from gefion.universe import universe_members
        _define(conn, "qum_pin", [RULE_SHELLS],
                pins=[{"symbol": "QUM_SPAC", "action": "include",
                       "reason": "known operating co misclassified"},
                      {"symbol": "QUM_AAA", "action": "exclude",
                       "reason": "manual quarantine"}])
        refresh_universe(conn, "qum_pin")
        members = universe_members(conn, "qum_pin")
        assert "QUM_SPAC" in members       # include pin beats the rule
        assert "QUM_AAA" not in members    # exclude pin


class TestGuard:
    def test_empty_universe_refused_even_with_force(self, conn):
        from gefion.universe.membership import (UniverseGuardError,
                                                refresh_universe)
        _define(conn, "qum_empty", _exclude_everything_rules(conn))
        with pytest.raises(UniverseGuardError):
            refresh_universe(conn, "qum_empty")
        with pytest.raises(UniverseGuardError):
            refresh_universe(conn, "qum_empty", force=True)
        assert _rows(conn, "qum_empty") == []  # nothing applied

    def test_initial_population_not_shrink_guarded(self, conn):
        """The very first refresh may exclude a huge fraction (that's the
        point of shipping the default universe) — the shrink guard compares
        against a PRIOR refresh only. Near-total exclusion with one include
        pin must pass on FIRST refresh."""
        from gefion.universe.membership import refresh_universe
        _define(conn, "qum_init", _exclude_everything_rules(conn),
                pins=[{"symbol": "QUM_AAA", "action": "include",
                       "reason": "keep one"}])
        delta = refresh_universe(conn, "qum_init")   # ~100% out: no refusal
        assert delta["added"] >= 4

    def test_outsized_shrink_refused_unless_forced(self, conn):
        from gefion.universe.membership import (UniverseGuardError,
                                                refresh_universe)
        _define(conn, "qum_shrink", [RULE_SHELLS])
        refresh_universe(conn, "qum_shrink")         # tiny baseline exclusion
        # now exclude ~everything (>25pp jump) but keep one member via pin
        _define(conn, "qum_shrink", _exclude_everything_rules(conn),
                pins=[{"symbol": "QUM_AAA", "action": "include",
                       "reason": "keep one"}])
        with pytest.raises(UniverseGuardError):
            refresh_universe(conn, "qum_shrink")
        delta = refresh_universe(conn, "qum_shrink", force=True)
        assert delta["added"] >= 3


class TestExplainAndSummary:
    def test_explain_member_and_excluded(self, conn):
        from gefion.universe.membership import (explain_symbol,
                                                refresh_universe)
        _define(conn, "qum_exp", [RULE_SHELLS])
        refresh_universe(conn, "qum_exp")
        out = explain_symbol(conn, "QUM_SPAC", name="qum_exp")
        assert out["member"] is False
        assert out["excluded_by"][0]["rule"] == "no-shells"
        assert out["excluded_by"][0]["reason"] == "cash boxes"
        assert explain_symbol(conn, "QUM_AAA", name="qum_exp")["member"] is True

    def test_explain_as_of(self, conn):
        from gefion.universe.membership import (explain_symbol,
                                                refresh_universe)
        _define(conn, "qum_expo", [RULE_PENNY])
        refresh_universe(conn, "qum_expo")
        assert explain_symbol(conn, "QUM_PENNY", name="qum_expo",
                              as_of=date(2024, 1, 5))["member"] is True
        assert explain_symbol(conn, "QUM_PENNY", name="qum_expo",
                              as_of=date(2024, 1, 3))["member"] is False

    def test_summary_counts_and_flaps(self, conn):
        from gefion.universe.membership import (membership_summary,
                                                refresh_universe)
        _define(conn, "qum_sum", [RULE_SHELLS, RULE_PENNY])
        refresh_universe(conn, "qum_sum")
        s = membership_summary(conn, "qum_sum")
        # >= because the shared test DB may hold matching foreign fixtures
        assert s["currently_excluded"] >= 2          # SPAC + PENNY (open now)
        assert s["by_rule"]["no-shells"] >= 1
        assert s["flaps"].get("no-penny", 0) >= 2    # two islands for PENNY
