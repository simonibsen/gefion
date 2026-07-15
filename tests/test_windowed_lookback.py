"""Windowed lookback for incremental stock-feature compute (#120 item 1b).

TDD: written FIRST. After #129, incremental compute fetches FULL history per
(symbol, function group) to write one new day — correct, but nightly cost
grows with history forever. Functions now DECLARE how much pre-cutoff
history they need (inputs.lookback on their registry row — the function is
DB-resident and operator-editable, so the policy must travel with the body,
not live in the dispatcher):

- {"mode": "window", ...}      exact rolling windows (SMA, BB, stoch, ...)
- {"mode": "converging", ...}  recursive/exponentially smoothed (EMA, RSI,
                               MACD, ADX) — multiplier 25 puts truncation
                               error below 1e-10 (decay (1-a)^n)
- undeclared / {"mode":"full"} full history — the honest default; PSAR is
                               path-dependent from series start and never
                               declares a bound.

The equality gate is the contract: for every seed body that declares a
lookback, windowed compute must match full-history compute on the emitted
tail to rtol 1e-9 (pinned direction: full history is the truth; the window
must reproduce it, not the other way around).
"""
import datetime as dt
import json
import os
import pathlib

import numpy as np
import pytest

FUNCTIONS_DIR = pathlib.Path(__file__).parent.parent / "feature-functions"

# real-world spec params per function (mirrors feature-definitions/)
REAL_SPECS = {
    "indicator_sma": [{"period": 20}, {"period": 50}, {"period": 200}],
    "indicator_ema": [{"period": 12}, {"period": 26}],
    "indicator_rsi": [{"period": 14}, {"period": 30}],
    "indicator_bb": [{"output": "upper"}, {"output": "middle"},
                     {"output": "lower"}],
    "indicator_macd": [{"output": "macd"}, {"output": "signal"},
                       {"output": "hist"}],
    "indicator_adx": [{"period": 14}],
    "indicator_stoch": [{"output": "k"}, {"output": "d"}],
    "indicator_psar": [{}],
    "realized_vol": [{"period": 20}],
    "price_change_pct_v1.0": [{}],
}


# --- pure policy -------------------------------------------------------------

def test_lookback_bars_policy():
    from gefion.features.lookback import lookback_bars
    # undeclared / full -> None (fetch everything)
    assert lookback_bars(None, [{"period": 200}]) is None
    assert lookback_bars({"mode": "full"}, [{"period": 200}]) is None
    # window: max period x multiplier + buffer
    bars = lookback_bars({"mode": "window"}, [{"period": 20}, {"period": 200}])
    assert 200 <= bars <= 260
    # converging: multiplier 25 default
    bars = lookback_bars({"mode": "converging"}, [{"period": 30}])
    assert bars >= 750
    # min_bars floor covers hardcoded-param bodies (no period in specs)
    bars = lookback_bars({"mode": "converging", "min_bars": 700},
                         [{"output": "macd"}])
    assert bars >= 700
    # multi-key periods (fastk_period etc.) are seen
    bars = lookback_bars({"mode": "window"}, [{"fastk_period": 14,
                                               "slowk_period": 3}])
    assert bars >= 14


def test_unknown_mode_refuses():
    from gefion.features.lookback import LookbackError, lookback_bars
    with pytest.raises(LookbackError):
        lookback_bars({"mode": "banana"}, [])


# --- equality gate over the REAL seed bodies -----------------------------------

def _synthetic_rows(n=2600, seed=13):
    """Trending random walk with regime shifts — enough texture that a
    truncation bug shows up as a value difference, not a lucky match."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0003, 0.02, n)
    steps[n // 3:n // 2] += 0.004      # bull regime
    steps[2 * n // 3:] -= 0.002        # slow bleed
    close = 100.0 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.008, n)))
    base = dt.date(2016, 1, 1)
    return [{"date": base + dt.timedelta(days=i), "open": float(close[i]),
             "high": float(high[i]), "low": float(low[i]),
             "close": float(close[i]), "adjusted_close": float(close[i]),
             "volume": 1000} for i in range(n)]


def _load_body(path):
    from gefion.features.dispatcher import exec_sandboxed
    payload = json.loads(path.read_text())
    env = exec_sandboxed(payload["function_body"], "compute")
    return payload, env["compute"]


def _by_date(out_rows):
    return {r["date"]: {k: v for k, v in r.items() if k != "date"}
            for r in out_rows}


@pytest.mark.parametrize("path", sorted(FUNCTIONS_DIR.glob("*.json")),
                         ids=lambda p: p.stem)
def test_declared_lookback_reproduces_full_history(path):
    """For every seed body that declares a lookback: computing over only the
    declared window must reproduce the full-history values on the tail."""
    from gefion.features.lookback import lookback_bars
    payload, compute = _load_body(path)
    declaration = (payload.get("inputs") or {}).get("lookback")
    specs = REAL_SPECS.get(path.stem)
    if specs is None:
        pytest.skip(f"no real-world specs mapped for {path.stem}")
    bars = lookback_bars(declaration, specs)
    if bars is None:
        # full-history functions (PSAR, forward-fill) have no window contract
        assert path.stem in ("indicator_psar", "forward_fill_quarterly"), (
            f"{path.stem} declares no lookback — every windowable seed "
            f"function must declare one (#120 item 1b)")
        return
    rows = _synthetic_rows()
    tail = 10
    full_out = _by_date(compute(rows, [dict(s) for s in specs]))
    windowed_out = _by_date(compute(rows[-(bars + tail):],
                                    [dict(s) for s in specs]))
    checked = 0
    for r in rows[-tail:]:
        d = r["date"]
        assert d in full_out and d in windowed_out, (
            f"{path.stem}: no output for tail date {d}")
        for col, full_val in full_out[d].items():
            win_val = windowed_out[d].get(col)
            assert win_val is not None, f"{path.stem}.{col} missing at {d}"
            assert win_val == pytest.approx(full_val, rel=1e-9, abs=1e-12), (
                f"{path.stem}.{col} at {d}: windowed {win_val!r} != "
                f"full {full_val!r} — declared lookback too small")
            checked += 1
    assert checked >= tail  # the gate actually gated something


# --- dispatcher honors the declaration (DB test) -------------------------------

def _conn():
    import psycopg

    from gefion.db import schema
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


BOUNDED_BODY = (
    "def compute(rows, specs):\n"
    "    assert len(rows) <= 120, (\n"
    "        f'dispatcher fetched {len(rows)} rows — lookback not honored')\n"
    "    out = []\n"
    "    for i in range(len(rows)):\n"
    "        if i >= 4:\n"
    "            w = [float(r['close']) for r in rows[i-4:i+1]]\n"
    "            out.append({'date': rows[i]['date'], 'qwl_roll5': sum(w)/5})\n"
    "    return out\n"
)


@pytest.fixture
def world():
    from gefion.db import schema
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    with c.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'qwl%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'qwl%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'qwl%'")
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QWL%')")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QWL%'")
        cur.execute("INSERT INTO stocks (symbol, asset_type) "
                    "VALUES ('QWL1', 'Stock') RETURNING id")
        sid = cur.fetchone()[0]
        base = dt.date(2020, 1, 1)
        for i in range(400):
            close = 100.0 + (i % 37)
            cur.execute(
                """INSERT INTO stock_ohlcv (data_id, date, open, high, low,
                   close, volume) VALUES (%s,%s,%s,%s,%s,%s,1000)""",
                (sid, base + dt.timedelta(days=i), close, close, close, close))
        cur.execute(
            """INSERT INTO feature_functions (name, version, status, enabled,
                   language, function_body, inputs, scope)
               VALUES ('qwl_fn', 'v1', 'active', TRUE, 'python', %s, %s,
                       'stock')""",
            (BOUNDED_BODY,
             json.dumps({"lookback": {"mode": "window", "min_bars": 50}})))
        cur.execute(
            """INSERT INTO feature_definitions (name, function_name, params,
                   entity_table, source_table, source_column, store_table,
                   store_column, active)
               VALUES ('qwl_roll5', 'qwl_fn', '{"period": 5}'::jsonb,
                       'stocks', 'stock_ohlcv', 'close', 'computed_features',
                       'value', TRUE) RETURNING id""")
        fid = cur.fetchone()[0]
    yield c, sid, fid
    with c.cursor() as cur:
        cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                    "(SELECT id FROM feature_definitions WHERE name LIKE 'qwl%')")
        cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'qwl%'")
        cur.execute("DELETE FROM feature_functions WHERE name LIKE 'qwl%'")
        cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                    "(SELECT id FROM stocks WHERE symbol LIKE 'QWL%')")
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QWL%'")
    c.close()


def test_dispatcher_bounded_fetch_and_correct_values(world):
    """Incremental compute for a declaring function fetches ONLY the
    declared window (the body asserts on row count: 400 bars of history,
    window ~65) and still writes values identical to a full recompute."""
    from gefion.features.dispatcher import compute_features
    conn, sid, fid = world
    base = dt.date(2020, 1, 1)
    # Seed the store via SQL for all but the last 3 days (a first-time
    # compute legitimately sees full history — the body's row-count assert
    # exists to catch the INCREMENTAL path fetching more than the window).
    with conn.cursor() as cur:
        for i in range(4, 397):
            d = base + dt.timedelta(days=i)
            vals = [100.0 + ((i - k) % 37) for k in range(5)]
            cur.execute(
                """INSERT INTO computed_features (data_id, date, feature_id,
                   value) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (sid, d, fid, sum(vals) / 5))
    # three unseen days remain (397..399); incremental must fetch only the
    # declared window (<=120 rows by the body's own assert) and fill them
    res = compute_features(conn, sid, function_names=["qwl_fn"],
                           incremental=True)
    assert res["qwl_fn"]["errors"] == []
    assert res["qwl_fn"]["inserted"] == 3
    with conn.cursor() as cur:
        cur.execute("SELECT date, value FROM computed_features "
                    "WHERE feature_id = %s AND date > %s ORDER BY date",
                    (fid, base + dt.timedelta(days=396)))
        got = cur.fetchall()
    assert len(got) == 3
    for d, v in got:
        i = (d - base).days
        expect = sum(100.0 + ((i - k) % 37) for k in range(5)) / 5
        assert float(v) == pytest.approx(expect)
