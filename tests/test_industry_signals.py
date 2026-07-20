"""Industry-level derived series (016 — the 013 sector pattern, one level
finer).

TDD: written FIRST. Industries reuse the sector machinery: census-driven
seeding of generated market bodies (relative strength + internal breadth per
industry), same sandbox, same lifecycle. Two differences are load-bearing:

1. The cross-section rows handed to market functions must carry `industry`.
2. The census counts CURRENT MODELING-UNIVERSE MEMBERS, not raw stocks —
   "SHELL COMPANIES" is itself an industry and must never earn a series,
   and penny-priced members must not pad a thin industry over the floor.
"""
import datetime as dt
import os
from datetime import date as D

import psycopg
import pytest

from gefion.db import schema

UP = "QIN UP GROUP"
DOWN = "QIN DOWN GROUP"
UP_SLUG = "qin_up_group"
DOWN_SLUG = "qin_down_group"
N_DAYS = 60


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
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'industry_%qin_%'")
    # derived values live under macro_series data_ids — remove them BEFORE
    # their feature_definitions (FK)
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions "
                " WHERE name LIKE 'macro_industry_%qin_%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_industry_%qin_%'")
    cur.execute("DELETE FROM macro_series WHERE name LIKE 'industry_%qin_%'")
    cur.execute("DELETE FROM computed_features WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'QIN%')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'QIN%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'QIN%'")


@pytest.fixture(scope="module")
def world():
    """Planted industries: UP (+1%/day, 4 members + 1 penny member), DOWN
    (-1%/day, 4 members), and 4 'SHELL COMPANIES' — with the default
    universe seeded AND refreshed so the gate is live."""
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    schema.create_macro_series_tables(c)
    schema.create_universe_definitions_table(c)
    schema.create_universe_exclusions_table(c)
    from gefion.universe.definitions import seed_default_universe
    seed_default_universe(c)
    with c.cursor() as cur:
        _cleanup(cur)
        ids = {}
        plants = (
            [(f"QINU{i}", UP, 100.0, 0.01) for i in range(4)]
            + [("QINUPENNY", UP, 0.50, 0.0)]            # sub-dollar member
            + [(f"QIND{i}", DOWN, 100.0, -0.01) for i in range(4)]
            + [(f"QINS{i}", "SHELL COMPANIES", 10.0, 0.0) for i in range(4)]
        )
        for sym, industry, base, drift in plants:
            cur.execute(
                "INSERT INTO stocks (symbol, status, asset_type, sector, industry) "
                "VALUES (%s, 'Active', 'Stock', 'QIN SECTOR', %s) RETURNING id",
                (sym, industry))
            ids[sym] = (cur.fetchone()[0], base, drift)
        cur.execute("SELECT id FROM feature_definitions WHERE name='indicator_sma_200'")
        row = cur.fetchone()
        if row:
            sma_id = row[0]
        else:
            cur.execute("INSERT INTO feature_definitions (name, function_name, "
                        "entity_table) VALUES ('indicator_sma_200','indicator','stocks') "
                        "RETURNING id")
            sma_id = cur.fetchone()[0]
        days, d = [], D(2024, 1, 1)
        while len(days) < N_DAYS:
            if d.weekday() < 5:
                days.append(d)
            d += dt.timedelta(days=1)
        for i, d in enumerate(days):
            for sym, (sid, base, drift) in ids.items():
                close = base * (1 + drift) ** i
                cur.execute("""INSERT INTO stock_ohlcv (data_id, date, open, high,
                    low, close, volume) VALUES (%s,%s,%s,%s,%s,%s,1000)
                    ON CONFLICT DO NOTHING""", (sid, d, close, close, close, close))
                cur.execute("""INSERT INTO computed_features (data_id, date,
                    feature_id, value) VALUES (%s,%s,%s,100.0)
                    ON CONFLICT DO NOTHING""", (sid, d, sma_id))
    from gefion.universe.membership import refresh_universe
    refresh_universe(c, "modeling_default", force=True)
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    refresh_universe(c, "modeling_default", force=True)  # drop QIN intervals
    c.close()


# --- bodies (pure) ----------------------------------------------------------

def test_industry_bodies_generated_with_floor():
    from gefion.macro.market_bodies import industry_signal_bodies
    bodies = industry_signal_bodies(UP)
    assert set(bodies) == {f"industry_rs_{UP_SLUG}",
                          f"industry_breadth_{UP_SLUG}"}
    assert "MIN_MEMBERS = 30" in bodies[f"industry_rs_{UP_SLUG}"]["body"]
    bodies3 = industry_signal_bodies(UP, min_members=3)
    assert "MIN_MEMBERS = 3" in bodies3[f"industry_breadth_{UP_SLUG}"]["body"]
    for spec in bodies.values():
        assert UP in spec["description"]
        assert '"industry"' in spec["body"]      # keyed off industry, not sector


# --- dispatcher: cross-section rows carry industry --------------------------

def test_market_rows_include_industry(world):
    from gefion.features.dispatcher import run_market_function
    fn_row = {
        "id": 999998, "name": "qin_probe",
        "function_body": ("def compute(rows):\n"
                          "    return float(sum(1 for r in rows "
                          f"if r.get('industry') == {UP!r}))"),
        "inputs": {},
    }
    by_date = dict(run_market_function(world, fn_row, min_stocks=2)["values"])
    # 4 healthy UP members; the penny member is excluded by the universe gate
    assert by_date[D(2024, 1, 1)] == 4.0


# --- seeding: census through the universe gate ------------------------------

def _seed(runner, *extra):
    from gefion.cli import app
    return runner.invoke(app, ["macro", "seed-industries",
                               "--min-members", "3", "--body-floor", "3",
                               "--db-url", schema.test_db_url(), *extra])


def test_seed_industries_census_gated(world):
    """SHELL COMPANIES (4 raw members, all universe-excluded) must never
    seed; the penny member must not count toward UP's census (4, not 5)."""
    from typer.testing import CliRunner
    runner = CliRunner()
    r = _seed(runner, "--industries", f"{UP},{DOWN}")
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        cur.execute("SELECT name FROM feature_functions WHERE scope='market' "
                    "AND name LIKE 'industry_%' ORDER BY name")
        names = [x[0] for x in cur.fetchall()]
    assert f"industry_rs_{UP_SLUG}" in names
    assert f"industry_breadth_{DOWN_SLUG}" in names
    assert not any("shell" in n for n in names)
    # census counts gated members: UP has 4 (not 5) — a floor of 5 skips it
    r = _seed(runner, "--industries", UP, "--min-members", "5")
    assert r.exit_code == 0, r.output
    assert "skip" in r.output.lower() or "0 industry" in r.output

    # SHELL COMPANIES is not in the gated census at all: naming it refuses
    r = _seed(runner, "--industries", "SHELL COMPANIES")
    assert r.exit_code == 1

    # deleting one and re-running restores it (create-if-absent)
    with world.cursor() as cur:
        cur.execute("UPDATE feature_functions SET function_body = %s "
                    "WHERE name = %s",
                    ("def compute(rows):\n    return 7.0\n",
                     f"industry_rs_{UP_SLUG}"))
    assert _seed(runner, "--industries", f"{UP},{DOWN}").exit_code == 0
    with world.cursor() as cur:
        cur.execute("SELECT function_body FROM feature_functions "
                    "WHERE name = %s", (f"industry_rs_{UP_SLUG}",))
        assert "7.0" in cur.fetchone()[0]     # DB wins, never clobbered
        cur.execute("DELETE FROM feature_functions WHERE name = %s",
                    (f"industry_rs_{UP_SLUG}",))
    assert _seed(runner, "--industries", f"{UP},{DOWN}").exit_code == 0


# --- derive: values ---------------------------------------------------------

def _derive(runner, series):
    from gefion.cli import app
    return runner.invoke(app, ["macro", "derive", "--series", series,
                               "--min-stocks", "2",
                               "--db-url", schema.test_db_url()])


def _series_values(cur, feature):
    cur.execute("""SELECT cf.date, cf.value FROM computed_features cf
                   JOIN feature_definitions fd ON fd.id = cf.feature_id
                   WHERE fd.name = %s ORDER BY cf.date""", (feature,))
    return cur.fetchall()


def test_industry_rs_signs_and_breadth(world):
    """Planted +1%/-1% drifts: rs positive for UP, negative for DOWN (after
    ret_20 warmup); breadth 100% / 0% against the flat sma200 stand-in."""
    from typer.testing import CliRunner
    runner = CliRunner()
    assert _seed(runner, "--industries", f"{UP},{DOWN}").exit_code == 0
    r = _derive(runner, ",".join([
        f"industry_rs_{UP_SLUG}", f"industry_rs_{DOWN_SLUG}",
        f"industry_breadth_{UP_SLUG}", f"industry_breadth_{DOWN_SLUG}"]))
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        up_rs = _series_values(cur, f"macro_industry_rs_{UP_SLUG}")
        down_rs = _series_values(cur, f"macro_industry_rs_{DOWN_SLUG}")
        up_br = _series_values(cur, f"macro_industry_breadth_{UP_SLUG}")
        down_br = _series_values(cur, f"macro_industry_breadth_{DOWN_SLUG}")
    assert up_rs and down_rs, "rs series must have values after warmup"
    assert all(v > 0 for _, v in up_rs), "UP industry must beat the market"
    assert all(v < 0 for _, v in down_rs), "DOWN industry must lag it"
    assert all(v == 100.0 for _, v in up_br[1:])   # day 0: close == flat SMA
    assert all(v == 0.0 for _, v in down_br)
