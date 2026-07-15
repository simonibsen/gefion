"""Market-function derive: ret_20 window must see pre-cutoff history.

TDD: written FIRST. `run_market_function` computes ret_20 as a SQL window
(LAG(close, 20)) over the streamed cross-section — but the stream was
filtered to `date > start`, so an incremental derive starved the lag: every
post-cutoff date's ret_20 was NULL until 20 NEW trading days had accrued,
and history-dependent series (dispersion_20) silently wrote nothing (prod:
stuck at 2026-07-10 while breadth healed). Same defect class as the
per-stock incremental fix (#129), in the market path.

Correct semantics: warm the window up with pre-cutoff rows, emit (and
write) only dates after the cutoff, values identical to a full recompute.
"""
import datetime as dt
import json
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date
N_STOCKS = 4
BODY = (
    "def compute(rows):\n"
    "    vals = sorted(r['ret_20'] for r in rows if r.get('ret_20') is not None)\n"
    "    if not vals:\n"
    "        return None\n"
    "    n = len(vals)\n"
    "    mid = n // 2\n"
    "    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0\n"
)


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


def _cleanup(cur):
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name LIKE '%qml%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE '%qml%'")
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'qml%'")
    cur.execute("DELETE FROM macro_series WHERE name LIKE 'qml%'")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'QML%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QML%'")


def _close(stock_idx, day_idx):
    return 100.0 + stock_idx * 10 + day_idx * (0.5 + 0.1 * stock_idx)


def _bar(cur, sid, d, close):
    cur.execute(
        """INSERT INTO stock_ohlcv (data_id, date, open, high, low, close,
           volume) VALUES (%s,%s,%s,%s,%s,%s,1000) ON CONFLICT DO NOTHING""",
        (sid, d, close, close, close, close))


@pytest.fixture
def world():
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute(
            "INSERT INTO stocks (symbol, asset_type) VALUES "
            "('QML1','Stock'),('QML2','Stock'),('QML3','Stock'),"
            "('QML4','Stock') RETURNING id")
        ids = [r[0] for r in cur.fetchall()]
        base = D(2024, 1, 1)
        for i in range(30):
            for j, sid in enumerate(ids):
                _bar(cur, sid, base + dt.timedelta(days=i), _close(j, i))
        cur.execute(
            """INSERT INTO feature_functions (name, version, status, enabled,
                   language, function_body, inputs, scope)
               VALUES ('qml_ret20_median', 'v1', 'active', TRUE, 'python',
                       %s, %s, 'market')""",
            (BODY, json.dumps({"features": ["ret_20"]})))
    yield c, ids
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _expected(day_idx):
    """Median across stocks of close[d]/close[d-20] - 1 (full-history)."""
    rets = sorted(_close(j, day_idx) / _close(j, day_idx - 20) - 1
                  for j in range(N_STOCKS))
    mid = N_STOCKS // 2
    return (rets[mid - 1] + rets[mid]) / 2.0


def _stored(cur):
    cur.execute(
        """SELECT cf.date, cf.value FROM computed_features cf
           JOIN feature_definitions fd ON fd.id = cf.feature_id
           WHERE fd.name = 'macro_qml_ret20_median' ORDER BY cf.date""")
    return {d: float(v) for d, v in cur.fetchall()}


def test_incremental_derive_heals_with_full_window(world):
    from gefion.macro.derived import derive_series
    conn, ids = world
    base = D(2024, 1, 1)
    # first derive: nothing stored -> full history; LAG(20) yields values
    # from day index 20 onward (days 21..30 of 30) = 10 values
    written = derive_series(conn, "qml_ret20_median", min_stocks=2)
    assert written == 10
    # three new bars arrive — far fewer than the 20-day window needs
    with conn.cursor() as cur:
        for i in range(30, 33):
            for j, sid in enumerate(ids):
                _bar(cur, sid, base + dt.timedelta(days=i), _close(j, i))
    written = derive_series(conn, "qml_ret20_median", min_stocks=2)
    assert written == 3
    with conn.cursor() as cur:
        stored = _stored(cur)
    assert len(stored) == 13
    for i in range(30, 33):
        assert stored[base + dt.timedelta(days=i)] == \
            pytest.approx(_expected(i)), f"day {i} != full recompute"


def test_incremental_derive_noop_when_current(world):
    from gefion.macro.derived import derive_series
    conn, _ = world
    assert derive_series(conn, "qml_ret20_median", min_stocks=2) == 10
    assert derive_series(conn, "qml_ret20_median", min_stocks=2) == 0
