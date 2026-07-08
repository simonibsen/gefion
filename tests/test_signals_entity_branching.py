"""Loader branching on the declared entity axis (007, T013 — US1).

TDD: written FIRST. The discovery loader resolves each feature's entity_table
in-query: stocks features keep today's behavior byte-identical (cross-sectional
daily median, symbol-universe filtering); a non-stock feature's market-level
value is the value itself (the median over a single entity degenerates), and
the symbol-universe filter never applies to it. Cross-entity aggregation is
impossible by construction — every series is scoped to one feature_id, so a
macro series id colliding numerically with a stock id can never mix values.
"""
import datetime
import os

import psycopg
import pytest

from gefion.db import schema
from gefion.regimes.discovery import signals


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


BASE = datetime.date(2024, 1, 1)


@pytest.fixture
def seeded(request):
    c = _conn()

    def _cleanup():
        with c.cursor() as cur:
            cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                        "(SELECT id FROM feature_definitions WHERE name LIKE 'sigent_%')")
            cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'sigent_%'")
            cur.execute("DELETE FROM macro_series WHERE name LIKE 'sigent_%'")
            cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                        "(SELECT id FROM stocks WHERE symbol LIKE 'SGE%')")
            cur.execute("DELETE FROM stocks WHERE symbol LIKE 'SGE%'")
        c.close()

    request.addfinalizer(_cleanup)
    with c.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'SGE%'")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'sigent_%'")
        cur.execute("DELETE FROM macro_series WHERE name LIKE 'sigent_%'")
        # three stocks with distinct values -> a real median
        cur.execute(
            """INSERT INTO stocks (symbol, name, asset_type) VALUES
               ('SGE1', 'A', 'Common Stock'), ('SGE2', 'B', 'Common Stock'),
               ('SGE3', 'C', 'Common Stock') RETURNING id""")
        stock_ids = [r[0] for r in cur.fetchall()]
        cur.execute(
            "INSERT INTO feature_definitions (name, function_name, entity_table) "
            "VALUES ('sigent_stock_feat', 'indicator', 'stocks') RETURNING id")
        stock_feat = cur.fetchone()[0]
        # one macro series
        cur.execute(
            """INSERT INTO macro_series (name, provider, kind, cadence)
               VALUES ('sigent_vix', 'test', 'index', 'daily') RETURNING id""")
        series_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO feature_definitions (name, function_name, entity_table) "
            "VALUES ('sigent_macro_feat', 'macro_value', 'macro_series') RETURNING id")
        macro_feat = cur.fetchone()[0]
        for i in range(5):
            d = BASE + datetime.timedelta(days=i)
            for j, sid in enumerate(stock_ids):
                close = 100.0 + i + 10 * j
                cur.execute(
                    """INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, volume)
                       VALUES (%s, %s, %s, %s, %s, %s, 1000) ON CONFLICT DO NOTHING""",
                    (sid, d, close, close, close, close))
                cur.execute(
                    """INSERT INTO computed_features (data_id, date, feature_id, value)
                       VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (sid, d, stock_feat, float(10 * (j + 1))))  # 10, 20, 30
            cur.execute(
                """INSERT INTO computed_features (data_id, date, feature_id, value)
                   VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (series_id, d, macro_feat, 15.0 + i))
    yield c, {"stock_ids": stock_ids, "series_id": series_id,
              "stock_feat": stock_feat, "macro_feat": macro_feat}


def test_stocks_features_unchanged_regression_vector(seeded):
    """SC-201's loader half: the cross-sectional daily median, exactly as
    before the entity axis existed."""
    conn, ctx = seeded
    with conn.cursor() as cur:
        series = signals._feature_series(cur, "sigent_stock_feat", None)
    assert len(series) == 5
    assert all(v == 20.0 for _, v in series)  # median of 10, 20, 30


def test_symbol_universe_applies_to_stocks_features(seeded):
    conn, ctx = seeded
    with conn.cursor() as cur:
        series = signals._feature_series(cur, "sigent_stock_feat",
                                         ["SGE1", "SGE2"])
    assert all(v == 15.0 for _, v in series)  # median of 10, 20


def test_single_entity_series_degenerates_to_the_value(seeded):
    """A macro feature's market-level series is the series itself."""
    conn, ctx = seeded
    with conn.cursor() as cur:
        series = signals._feature_series(cur, "sigent_macro_feat", None)
    assert [v for _, v in series] == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_symbol_filter_never_applies_to_non_stock_features(seeded):
    """The universe filter is a stocks concept: a macro series must come back
    whole under any symbol universe (today's code would join stocks on the
    series id and destroy it)."""
    conn, ctx = seeded
    with conn.cursor() as cur:
        filtered = signals._feature_series(cur, "sigent_macro_feat",
                                           ["SGE1", "SGE2"])
    assert [v for _, v in filtered] == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_cross_entity_aggregation_impossible(seeded):
    """A macro series id that collides numerically with a stock id never mixes
    values: series are scoped per feature_id."""
    conn, ctx = seeded
    # Manufacture the collision: give the *stocks* feature a value at the
    # macro series' id on the same dates (legal now — no FK), then check the
    # macro feature's series is untouched by it.
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO computed_features (data_id, date, feature_id, value)
               VALUES (%s, %s, %s, 999.0) ON CONFLICT DO NOTHING""",
            (ctx["series_id"], BASE, ctx["stock_feat"]))
        series = signals._feature_series(cur, "sigent_macro_feat", None)
        cur.execute(
            "DELETE FROM computed_features WHERE data_id = %s AND feature_id = %s",
            (ctx["series_id"], ctx["stock_feat"]))
    assert [v for _, v in series] == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_load_market_data_serves_macro_alongside_stocks(seeded):
    """End-to-end: a discovery load with a symbol universe carries both the
    equity median and the untouched macro series."""
    conn, ctx = seeded
    market = signals.load_market_data(
        conn, ["sigent_stock_feat", "sigent_macro_feat"],
        symbols=["SGE1", "SGE2", "SGE3"])
    assert [v for _, v in market.features["sigent_stock_feat"]] == [20.0] * 5
    assert [v for _, v in market.features["sigent_macro_feat"]] == \
        [15.0, 16.0, 17.0, 18.0, 19.0]
