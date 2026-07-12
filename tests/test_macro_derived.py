"""Derived macro series: breadth + dispersion (new information families).

TDD: written FIRST. These are facts about the universe's SHAPE that no
single-stock indicator carries: breadth (% of stocks above their own 200-day
average) and return dispersion (cross-sectional std of 20-day returns).
They follow the macro mold exactly — a macro_series row + a feature with
entity_table='macro_series', values in computed_features — so they become
discovery atoms with zero DDL, like macro_vix. Days with too few stocks get
NO value (an honest gap, never a garbage number).
"""
import datetime as dt
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
                "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_breadth%' "
                " OR name LIKE 'macro_dispersion%' OR name LIKE 'mder_%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_breadth%' "
                "OR name LIKE 'macro_dispersion%' OR name LIKE 'mder_%'")
    cur.execute("DELETE FROM macro_series WHERE name IN ('breadth_sma200','dispersion_20')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'MDER%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'MDER%'")


@pytest.fixture(scope="module")
def world():
    """4 stocks, 60 days: two trending up (above their SMA200 stand-in),
    two down; daily returns engineered so 20d dispersion is known."""
    c = _conn()
    schema.create_stocks_table(c)               # canonical creators, any order
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("""INSERT INTO stocks (symbol, asset_type) VALUES
            ('MDER1','Stock'),('MDER2','Stock'),('MDER3','Stock'),('MDER4','Stock')
            RETURNING id""")
        ids = [r[0] for r in cur.fetchall()]
        cur.execute("INSERT INTO feature_definitions (name, function_name, "
                    "entity_table) VALUES ('mder_sma200_standin','indicator','stocks') "
                    "RETURNING id")
        # use the REAL feature name the derivation reads:
        cur.execute("UPDATE feature_definitions SET name='indicator_sma_200' "
                    "WHERE name='mder_sma200_standin' AND NOT EXISTS "
                    "(SELECT 1 FROM feature_definitions WHERE name='indicator_sma_200')")
        cur.execute("SELECT id FROM feature_definitions WHERE name='indicator_sma_200'")
        sma_id = cur.fetchone()[0]
        base = D(2024, 1, 1)
        # trends: +1%/d, +0.5%/d, -0.5%/d, -1%/d ; sma stand-in flat at 100
        drifts = [0.01, 0.005, -0.005, -0.01]
        for i in range(60):
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


def test_breadth_is_fraction_above_sma200(world):
    from gefion.macro.derived import derive_series
    n = derive_series(world, "breadth_sma200", min_stocks=2)
    assert n > 0
    with world.cursor() as cur:
        cur.execute("""SELECT cf.value FROM computed_features cf
            JOIN feature_definitions fd ON fd.id = cf.feature_id
            WHERE fd.name='macro_breadth_sma200' ORDER BY cf.date DESC LIMIT 1""")
        val = cur.fetchone()[0]
    assert abs(val - 50.0) < 1e-6               # 2 of 4 above their SMA (%)


def test_dispersion_matches_cross_sectional_std(world):
    import numpy as np
    from gefion.macro.derived import derive_series
    derive_series(world, "dispersion_20", min_stocks=2)
    with world.cursor() as cur:
        cur.execute("""SELECT cf.value FROM computed_features cf
            JOIN feature_definitions fd ON fd.id = cf.feature_id
            WHERE fd.name='macro_dispersion_20' ORDER BY cf.date DESC LIMIT 1""")
        val = cur.fetchone()[0]
    rets = [1.01**20 - 1, 1.005**20 - 1, 0.995**20 - 1, 0.99**20 - 1]
    assert abs(val - float(np.std(rets))) < 1e-6


def test_thin_days_get_no_value(world):
    """min_stocks floor: a day with too few stocks is an honest gap."""
    from gefion.macro.derived import derive_series
    derive_series(world, "breadth_sma200", min_stocks=50)   # floor above n=4
    with world.cursor() as cur:
        cur.execute("""SELECT count(*) FROM computed_features cf
            JOIN feature_definitions fd ON fd.id = cf.feature_id
            WHERE fd.name='macro_breadth_sma200'""")
        # rows only from the earlier min_stocks=2 run; the floor added none
        before = cur.fetchone()[0]
    assert derive_series(world, "breadth_sma200", min_stocks=50) == 0
    with world.cursor() as cur:
        cur.execute("""SELECT count(*) FROM computed_features cf
            JOIN feature_definitions fd ON fd.id = cf.feature_id
            WHERE fd.name='macro_breadth_sma200'""")
        assert cur.fetchone()[0] == before


def test_idempotent_and_incremental(world):
    from gefion.macro.derived import derive_series
    first = derive_series(world, "dispersion_20", min_stocks=2)
    again = derive_series(world, "dispersion_20", min_stocks=2)
    assert again == 0 or again < first          # incremental: nothing new


def test_cli_and_mcp_surfaces():
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, ["macro", "derive", "--help"])
    assert r.exit_code == 0
    assert "--series" in r.output
    import pathlib
    server = (pathlib.Path(__file__).parent.parent / "mcp-server"
              / "server.py").read_text()
    assert 'name="macro_derive"' in server
