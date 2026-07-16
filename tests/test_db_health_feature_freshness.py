"""db-health feature-freshness guard (#120 follow-on, 2026-07-16).

TDD: written FIRST. The 2026-07 incident: incremental feature compute
silently no-opped for 8 trading days and nothing surfaced it — features
lagged price bars and every consumer (predictions, derived series, hunts)
quietly degraded. db-health now measures the STALEST active stock feature
against the latest price bar on a reference symbol and warns, naming the
fixing command. Stalest (min), not freshest (max): PSAR stayed current
through the incident, so a max would have said "fresh" while 90% of the
store was stale.
"""
import datetime as dt
import json
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date


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
                "(SELECT id FROM feature_definitions WHERE name LIKE 'qff%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'qff%'")
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'qff%'")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'QFF%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QFF%'")


@pytest.fixture
def world():
    """Reference stock with 30 bars; two active features — one current, one
    stale by 5 sessions (the incident shape: PSAR fresh, SMA stuck)."""
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO stocks (symbol, asset_type) "
                    "VALUES ('QFF1', 'Stock') RETURNING id")
        sid = cur.fetchone()[0]
        base = D(2030, 1, 1)     # far future: strictly newer than other
        for i in range(30):      # tests' fixture bars in the shared DB
            close = 100.0 + i
            cur.execute(
                """INSERT INTO stock_ohlcv (data_id, date, open, high, low,
                   close, volume) VALUES (%s,%s,%s,%s,%s,%s,1000)""",
                (sid, base + dt.timedelta(days=i), close, close, close, close))
        for name in ("qff_fresh", "qff_stale"):
            cur.execute(
                """INSERT INTO feature_definitions (name, function_name,
                       entity_table, source_table, source_column, active)
                   VALUES (%s, %s, 'stocks', 'stock_ohlcv', 'close', TRUE)
                   RETURNING id""", (name, name + "_fn"))
            fid = cur.fetchone()[0]
            last = 29 if name == "qff_fresh" else 24   # stale: 5 sessions back
            for i in range(last + 1):
                cur.execute(
                    """INSERT INTO computed_features (data_id, date,
                       feature_id, value) VALUES (%s,%s,%s,%s)""",
                    (sid, base + dt.timedelta(days=i), fid, float(i)))
    yield c, sid
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _db_health():
    from typer.testing import CliRunner

    from gefion.cli import app
    r = CliRunner().invoke(app, ["db-health", "--db-url",
                                 schema.test_db_url(), "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output.strip().splitlines()[-1])
    return payload


def test_stale_feature_raises_freshness_warning(world):
    payload = _db_health()
    health = payload.get("health", payload)
    fresh = health.get("feature_freshness")
    assert fresh is not None, "db-health must report feature_freshness"
    assert fresh["sessions_behind"] == 5
    assert fresh["stalest_feature"] == "qff_stale"
    warnings = payload.get("warnings") or health.get("warnings") or []
    hits = [w for w in warnings if "feat-compute" in w and "5" in w]
    assert hits, f"expected a freshness warning naming the fixing command, got: {warnings}"


def test_current_features_no_warning(world):
    conn, sid = world
    base = D(2030, 1, 1)
    with conn.cursor() as cur:   # bring the stale feature current
        cur.execute("SELECT id FROM feature_definitions WHERE name = 'qff_stale'")
        fid = cur.fetchone()[0]
        for i in range(25, 30):
            cur.execute(
                """INSERT INTO computed_features (data_id, date, feature_id,
                   value) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (sid, base + dt.timedelta(days=i), fid, float(i)))
    payload = _db_health()
    health = payload.get("health", payload)
    fresh = health.get("feature_freshness")
    assert fresh["sessions_behind"] == 0
    warnings = payload.get("warnings") or health.get("warnings") or []
    assert not [w for w in warnings if "lags price bars" in w]
