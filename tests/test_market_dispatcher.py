"""Market-level dispatcher mode (spec 011, epic #114).

TDD: written FIRST. Market function bodies are Python IN the database
(scope='market'), executed per-date over the stock cross-section by the SAME
sandbox as per-stock bodies. DB is the source of truth; failures are honest
and isolated; the migration from legacy SQL is gated on numeric equality.
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
                "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_mdx%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_mdx%'")
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'mdx%'")
    cur.execute("DELETE FROM macro_series WHERE name LIKE 'mdx%'")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'MDX%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'MDX%'")


@pytest.fixture(scope="module")
def world():
    """4 stocks x 30 days; sma200 stand-in flat at 100 so breadth is exact."""
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("""INSERT INTO stocks (symbol, asset_type) VALUES
            ('MDX1','Stock'),('MDX2','Stock'),('MDX3','Stock'),('MDX4','Stock')
            RETURNING id""")
        ids = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT id FROM feature_definitions WHERE name='indicator_sma_200'")
        row = cur.fetchone()
        if row:
            sma_id = row[0]
        else:
            cur.execute("INSERT INTO feature_definitions (name, function_name, "
                        "entity_table) VALUES ('indicator_sma_200','indicator','stocks') "
                        "RETURNING id")
            sma_id = cur.fetchone()[0]
        drifts = [0.01, 0.005, -0.005, -0.01]
        base = D(2024, 1, 1)
        for i in range(30):
            d = base + dt.timedelta(days=i)
            for sid, drift in zip(ids, drifts):
                close = 100.0 * (1 + drift) ** i
                cur.execute("""INSERT INTO stock_ohlcv (data_id, date, open, high,
                    low, close, volume) VALUES (%s,%s,%s,%s,%s,%s,1000)
                    ON CONFLICT DO NOTHING""", (sid, d, close, close, close, close))
                cur.execute("""INSERT INTO computed_features (data_id, date,
                    feature_id, value) VALUES (%s,%s,%s,100.0)
                    ON CONFLICT DO NOTHING""", (sid, d, sma_id))
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _mk_fn(conn, name, body, inputs=None, enabled=True):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feature_functions
                   (name, version, status, enabled, language, function_body,
                    inputs, scope)
               VALUES (%s, 'v1', 'active', %s, 'python', %s, %s, 'market')
               ON CONFLICT DO NOTHING""",
            (name, enabled, body,
             json.dumps(inputs) if inputs else None))


# --- T001: schema ---------------------------------------------------------------------

def test_scope_column_exists_defaults_and_checks(world):
    with world.cursor() as cur:
        cur.execute("""SELECT column_default, is_nullable FROM information_schema.columns
                       WHERE table_name='feature_functions' AND column_name='scope'""")
        row = cur.fetchone()
        assert row is not None, "scope column missing"
        assert "stock" in row[0] and row[1] == "NO"
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("INSERT INTO feature_functions (name, version, language, "
                        "function_body, scope) VALUES ('mdxbad','v1','python','x','galaxy')")


def test_fx_list_shows_scope(world):
    from typer.testing import CliRunner
    from gefion.cli import app
    _mk_fn(world, "mdx_scope_probe", "def compute(rows):\n    return 1.0")
    r = CliRunner().invoke(app, ["feat-fx-list", "--json",
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    fns = {f["name"]: f for f in payload["functions"]}
    assert fns["mdx_scope_probe"]["scope"] == "market"


# --- T003: executor -------------------------------------------------------------------

def _get_fn(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, function_body, inputs FROM feature_functions "
                    "WHERE name=%s", (name,))
        r = cur.fetchone()
    return {"id": r[0], "name": r[1], "function_body": r[2], "inputs": r[3]}


def test_market_body_runs_per_date_with_declared_inputs(world):
    from gefion.features.dispatcher import run_market_function
    _mk_fn(world, "mdx_breadth",
           "def compute(rows):\n"
           "    hits = [r for r in rows if 'indicator_sma_200' in r"
           " and r['close'] > r['indicator_sma_200']]\n"
           "    return 100.0 * len(hits) / len(rows)",
           inputs={"features": ["indicator_sma_200"]})
    out = run_market_function(world, _get_fn(world, "mdx_breadth"),
                              start=None, min_stocks=2)
    values = dict(out["values"])
    assert len(values) >= 29
    assert abs(values[D(2024, 1, 15)] - 50.0) < 1e-9    # 2 of 4 above


def test_none_nan_are_gaps_not_values(world):
    from gefion.features.dispatcher import run_market_function
    _mk_fn(world, "mdx_gappy",
           "def compute(rows):\n"
           "    return None if len(rows) % 2 == 0 else float('nan')")
    out = run_market_function(world, _get_fn(world, "mdx_gappy"),
                              start=None, min_stocks=2)
    assert out["values"] == []                           # all gaps
    assert out["gaps"] >= 29


def test_raising_body_is_isolated_failure(world):
    from gefion.features.dispatcher import MarketFunctionError, run_market_function
    _mk_fn(world, "mdx_boom", "def compute(rows):\n    raise ValueError('kaboom')")
    with pytest.raises(MarketFunctionError) as exc:
        run_market_function(world, _get_fn(world, "mdx_boom"),
                            start=None, min_stocks=2)
    assert "kaboom" in str(exc.value)


def test_sandbox_refuses_forbidden_import(world):
    from gefion.features.dispatcher import MarketFunctionError, run_market_function
    _mk_fn(world, "mdx_evil",
           "import os\ndef compute(rows):\n    return 1.0")
    with pytest.raises(MarketFunctionError) as exc:
        run_market_function(world, _get_fn(world, "mdx_evil"),
                            start=None, min_stocks=2)
    assert "not allowed" in str(exc.value)


def test_wrong_shape_return_is_failure(world):
    from gefion.features.dispatcher import MarketFunctionError, run_market_function
    _mk_fn(world, "mdx_shape", "def compute(rows):\n    return 'high'")
    with pytest.raises(MarketFunctionError):
        run_market_function(world, _get_fn(world, "mdx_shape"),
                            start=None, min_stocks=2)


def test_thin_days_never_reach_the_body(world):
    from gefion.features.dispatcher import run_market_function
    _mk_fn(world, "mdx_thin", "def compute(rows):\n    return float(len(rows))")
    out = run_market_function(world, _get_fn(world, "mdx_thin"),
                              start=None, min_stocks=50)   # floor above n=4
    assert out["values"] == [] and out["gaps"] >= 29
