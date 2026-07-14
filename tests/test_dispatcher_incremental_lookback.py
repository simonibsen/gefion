"""Incremental feature compute must see full history (#120 P0, 2026-07-14).

TDD: written FIRST. The incremental sweep fetched only source rows AFTER the
last computed date, so windowed functions (SMA-200, RSI-14, BB, MACD, ...)
received a handful of bars, computed nothing, and silently no-opped — every
night, forever, because the last computed date never advanced. Prod features
were stale from 2026-07-03 until this fix (daily feature rows fell from
~104k to ~10k; the survivors were PSAR — no minimum window, and its gap
values were WRONG because PSAR is path-dependent — and externally
materialized predictions).

Correct incremental semantics: compute over FULL history (identical values
to a full recompute), write only rows newer than the last computed date
(insert_computed_features skip_before).
"""
import datetime as dt
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date

# 5-bar rolling mean: emits a value only when the full window is available —
# the same shape that made real windowed indicators silently no-op.
ROLL5_BODY = (
    "def compute(rows, specs):\n"
    "    out = []\n"
    "    for i in range(len(rows)):\n"
    "        if i >= 4:\n"
    "            w = [float(r['close']) for r in rows[i-4:i+1]]\n"
    "            out.append({'date': rows[i]['date'], 'qil_roll5': sum(w)/5})\n"
    "    return out\n"
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
                "(SELECT id FROM feature_definitions WHERE name LIKE 'qil%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'qil%'")
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'qil%'")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'QIL%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QIL%'")


def _bar(cur, sid, d, close):
    cur.execute(
        """INSERT INTO stock_ohlcv (data_id, date, open, high, low, close,
           volume) VALUES (%s,%s,%s,%s,%s,%s,1000)""",
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
        cur.execute("INSERT INTO stocks (symbol, asset_type) "
                    "VALUES ('QIL1', 'Stock') RETURNING id")
        sid = cur.fetchone()[0]
        base = D(2024, 1, 1)
        for i in range(20):
            _bar(cur, sid, base + dt.timedelta(days=i), 100.0 + i)
        cur.execute(
            """INSERT INTO feature_functions (name, version, status, enabled,
                   language, function_body, scope)
               VALUES ('qil_fn', 'v1', 'active', TRUE, 'python', %s,
                       'stock')""", (ROLL5_BODY,))
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name, params,
                   entity_table, source_table, source_column, store_table,
                   store_column, active)
               VALUES ('qil_roll5', 'qil_fn', '{}'::jsonb, 'stocks',
                       'stock_ohlcv', 'close', 'computed_features', 'value',
                       TRUE) RETURNING id""")
        fid = cur.fetchone()[0]
    yield c, sid, fid
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _stored(cur, fid):
    cur.execute("SELECT date, value FROM computed_features "
                "WHERE feature_id = %s ORDER BY date", (fid,))
    return {d: float(v) for d, v in cur.fetchall()}


def _expected_roll5(closes_by_date):
    dates = sorted(closes_by_date)
    out = {}
    for i, d in enumerate(dates):
        if i >= 4:
            out[d] = sum(closes_by_date[dates[j]] for j in
                         range(i - 4, i + 1)) / 5
    return out


def _run(conn, sid, **kwargs):
    from gefion.features.dispatcher import compute_features
    return compute_features(conn, sid, function_names=["qil_fn"],
                            incremental=True, **kwargs)


def test_incremental_heals_gap_with_correct_values(world):
    conn, sid, fid = world
    base = D(2024, 1, 1)
    # initial run: nothing computed yet -> full history
    res = _run(conn, sid)
    assert res["qil_fn"]["inserted"] == 16  # 20 bars, window 5
    # three new bars arrive (a gap smaller than the window)
    closes = {base + dt.timedelta(days=i): 100.0 + i for i in range(20)}
    with conn.cursor() as cur:
        for i in range(20, 23):
            d = base + dt.timedelta(days=i)
            closes[d] = 100.0 + i
            _bar(cur, sid, d, closes[d])
    res = _run(conn, sid)
    # the windowed function must still emit the 3 new values...
    assert res["qil_fn"]["inserted"] == 3
    with conn.cursor() as cur:
        stored = _stored(cur, fid)
    # ...and every stored value must equal a from-scratch recompute
    assert stored == pytest.approx(_expected_roll5(closes))


def test_incremental_does_not_rewrite_existing_rows(world):
    conn, sid, fid = world
    base = D(2024, 1, 1)
    _run(conn, sid)
    # poison one stored value; a second incremental run must not touch it
    # (update_existing=False semantics preserved: old rows are skipped, not
    # re-upserted)
    poisoned_date = base + dt.timedelta(days=10)
    with conn.cursor() as cur:
        cur.execute("UPDATE computed_features SET value = -1 "
                    "WHERE feature_id = %s AND date = %s",
                    (fid, poisoned_date))
        _bar(cur, sid, base + dt.timedelta(days=20), 120.0)
    res = _run(conn, sid)
    assert res["qil_fn"]["inserted"] == 1
    with conn.cursor() as cur:
        stored = _stored(cur, fid)
    assert stored[poisoned_date] == -1.0  # untouched


def test_incremental_heals_via_writer_queue_path(world):
    from gefion.db import pool as db_pool
    conn, sid, fid = world
    base = D(2024, 1, 1)
    _run(conn, sid)
    with conn.cursor() as cur:
        for i in range(20, 23):
            _bar(cur, sid, base + dt.timedelta(days=i), 100.0 + i)
    db_pool.init_pool(schema.test_db_url())  # writer threads draw from the pool
    try:
        _run(conn, sid, writer_workers=1)
    finally:
        db_pool.close_pool()
    with conn.cursor() as cur:
        stored = _stored(cur, fid)
    assert len(stored) == 19  # 16 + 3, no duplicates, gap healed
