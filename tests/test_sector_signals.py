"""Sector-state signals (spec 013, US1 — FR-1301..1305).

TDD: written FIRST. A two-sector synthetic world with planted opposite
drifts must produce relative-strength series with the correct signs
(SC-1301); a (sector, date) thinner than the body's MIN_MEMBERS floor is a
gap; NULL-sector stocks belong to no sector but stay in the market
baseline; sector names normalize to stable slugs; seeding is census-driven,
create-if-absent, and refuses unknown sectors listing the known ones;
`derive --series all` covers every enabled DB market function.
"""
import datetime as dt
import json
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date
N_DAYS = 60
UP, DOWN = "TECH GROWTH", "OLD INDUSTRY"   # spaces exercise slug normalization
UP_SLUG, DOWN_SLUG = "tech_growth", "old_industry"


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
                "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_sector%' "
                " OR name LIKE 'macro_ssx%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_sector%' "
                "OR name LIKE 'macro_ssx%'")
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'sector_%' "
                "OR name LIKE 'ssx%'")
    cur.execute("DELETE FROM macro_series WHERE name LIKE 'sector_%' "
                "OR name LIKE 'ssx%'")
    cur.execute("DELETE FROM computed_features WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'SSX%')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'SSX%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'SSX%'")


@pytest.fixture(scope="module")
def world():
    """Two planted sectors x 4 members + 2 NULL-sector stocks, 60 weekdays.

    UP sector drifts +1%/day, DOWN drifts -1%/day; NULL stocks flat. sma200
    stand-in flat at 100 so breadth is exact: UP members end far above,
    DOWN members far below.
    """
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        ids = {}
        for i in range(10):
            sym = f"SSX{i}"
            sector = UP if i < 4 else (DOWN if i < 8 else None)
            cur.execute("INSERT INTO stocks (symbol, asset_type, sector) "
                        "VALUES (%s,'Stock',%s) RETURNING id", (sym, sector))
            ids[sym] = (cur.fetchone()[0], sector)
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
            for sym, (sid, sector) in ids.items():
                drift = 0.01 if sector == UP else (-0.01 if sector == DOWN else 0.0)
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


# --- naming (pure) ---------------------------------------------------------

def test_sector_slug_normalization():
    from gefion.macro.market_bodies import sector_slug
    assert sector_slug("FINANCIAL SERVICES") == "financial_services"
    assert sector_slug("TECH GROWTH") == "tech_growth"
    assert sector_slug(" Consumer-Cyclical ") == "consumer_cyclical"
    assert sector_slug("REAL ESTATE & REITS") == "real_estate_reits"


def test_sector_bodies_generated_with_floor():
    """The floor is a declared parameter (default 30) written INTO the body
    — operator-visible law, adjustable per deployment at seeding time."""
    from gefion.macro.market_bodies import sector_signal_bodies
    bodies = sector_signal_bodies(UP)
    assert "MIN_MEMBERS = 30" in bodies[f"sector_rs_{UP_SLUG}"]["body"]
    bodies3 = sector_signal_bodies(UP, min_members=3)
    assert "MIN_MEMBERS = 3" in bodies3[f"sector_rs_{UP_SLUG}"]["body"]
    assert set(bodies) == {f"sector_rs_{UP_SLUG}", f"sector_breadth_{UP_SLUG}"}
    for spec in bodies.values():
        assert "MIN_MEMBERS" in spec["body"]        # declared law, in the body
        assert UP in spec["description"]            # provenance human-readable


# --- seeding door ----------------------------------------------------------

def _seed(runner, *extra):
    from gefion.cli import app
    return runner.invoke(app, ["macro", "seed-sectors",
                               "--min-members", "3", "--body-floor", "3",
                               "--db-url", schema.test_db_url(), *extra])


def test_seed_sectors_census_and_floor(world):
    """Both planted sectors (4 members each) seed; NULL is never a sector;
    a --min-members above 4 seeds nothing and says so."""
    from typer.testing import CliRunner
    runner = CliRunner()
    r = _seed(runner)
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        cur.execute("SELECT name FROM feature_functions WHERE scope='market' "
                    "AND name LIKE 'sector_%' ORDER BY name")
        names = [x[0] for x in cur.fetchall()]
    assert f"sector_rs_{UP_SLUG}" in names
    assert f"sector_breadth_{DOWN_SLUG}" in names
    assert not any("none" in n or "null" in n for n in names)
    r = _seed(runner, "--min-members", "99")
    assert r.exit_code == 0
    assert "0" in r.output or "skip" in r.output.lower()


def test_seed_sectors_db_wins_on_rerun(world):
    from typer.testing import CliRunner
    runner = CliRunner()
    assert _seed(runner).exit_code == 0
    with world.cursor() as cur:
        cur.execute("UPDATE feature_functions SET function_body = %s "
                    "WHERE name = %s",
                    ("def compute(rows):\n    return 7.0\n", f"sector_rs_{UP_SLUG}"))
    assert _seed(runner).exit_code == 0
    with world.cursor() as cur:
        cur.execute("SELECT function_body FROM feature_functions WHERE name = %s",
                    (f"sector_rs_{UP_SLUG}",))
        assert "7.0" in cur.fetchone()[0]           # create-if-absent never clobbers
        cur.execute("DELETE FROM feature_functions WHERE name = %s",
                    (f"sector_rs_{UP_SLUG}",))
    assert _seed(runner).exit_code == 0             # restored for later tests


def test_seed_sectors_unknown_refuses_listing_census(world):
    from typer.testing import CliRunner
    r = _seed(CliRunner(), "--sectors", "NOSUCH SECTOR")
    assert r.exit_code == 1
    assert UP in r.output or UP_SLUG in r.output    # census listed in the refusal


# --- derive: values --------------------------------------------------------

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


def test_relative_strength_signs_and_breadth(world):
    """SC-1301: planted +1%/-1% drifts → rs positive for UP, negative for
    DOWN (after ret_20 warmup); breadth 100% / 0% against the flat sma200."""
    from typer.testing import CliRunner
    runner = CliRunner()
    assert _seed(runner).exit_code == 0
    r = _derive(runner, ",".join([f"sector_rs_{UP_SLUG}", f"sector_rs_{DOWN_SLUG}",
                                  f"sector_breadth_{UP_SLUG}",
                                  f"sector_breadth_{DOWN_SLUG}"]))
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        up_rs = _series_values(cur, f"macro_sector_rs_{UP_SLUG}")
        down_rs = _series_values(cur, f"macro_sector_rs_{DOWN_SLUG}")
        up_br = _series_values(cur, f"macro_sector_breadth_{UP_SLUG}")
        down_br = _series_values(cur, f"macro_sector_breadth_{DOWN_SLUG}")
    assert up_rs and down_rs, "rs series must have values after warmup"
    assert all(v > 0 for _, v in up_rs), "UP sector must beat the market"
    assert all(v < 0 for _, v in down_rs), "DOWN sector must lag the market"
    # day 0: close == the flat SMA exactly (not above) — correct 0%, skip it
    assert all(v == 100.0 for _, v in up_br[1:])
    assert all(v == 0.0 for _, v in down_br)


def test_min_members_floor_yields_gap(world):
    """A body whose sector has fewer members than MIN_MEMBERS returns None
    for every date — a gap, not a value. (Bodies ship MIN_MEMBERS=30; the
    planted sectors have 4, so an UNEDITED body must produce zero values.)"""
    from gefion.macro.market_bodies import sector_signal_bodies
    from gefion.features.dispatcher import run_market_function
    bodies = sector_signal_bodies(UP)
    name = f"sector_rs_{UP_SLUG}"
    fn = {"id": 0, "name": name, "function_body": bodies[name]["body"],
          "inputs": bodies[name]["inputs"], "enabled": True}
    result = run_market_function(world, fn, min_stocks=2)
    assert result["values"] == []                   # floor 30 > 4 members
    assert result["gaps"] > 0


def test_derive_is_idempotent(world):
    from typer.testing import CliRunner
    runner = CliRunner()
    series = f"sector_rs_{UP_SLUG}"
    assert _derive(runner, series).exit_code == 0
    with world.cursor() as cur:
        before = len(_series_values(cur, f"macro_{series}"))
    assert _derive(runner, series).exit_code == 0
    with world.cursor() as cur:
        assert len(_series_values(cur, f"macro_{series}")) == before


# --- derive: 'all' covers the DB (FR via R4) --------------------------------

def test_derive_all_covers_enabled_db_functions(world):
    """'all' = SEED_BODIES plus every enabled DB market function — a planted
    function outside the repo seeds is derived; a disabled one is skipped."""
    from typer.testing import CliRunner
    runner = CliRunner()
    with world.cursor() as cur:
        cur.execute("""INSERT INTO feature_functions
                       (name, version, status, enabled, language,
                        function_body, scope)
                       VALUES ('ssx_dbonly', 'v1', 'active', TRUE, 'python',
                               'def compute(rows):\n    return 1.0\n', 'market'),
                              ('ssx_disabled', 'v1', 'active', FALSE, 'python',
                               'def compute(rows):\n    return 2.0\n', 'market')
                       ON CONFLICT DO NOTHING""")
    r = _derive(runner, "all")
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        assert _series_values(cur, "macro_ssx_dbonly"), \
            "'all' must include enabled DB-resident market functions"
        assert not _series_values(cur, "macro_ssx_disabled")
    assert "ssx_disabled" in r.output               # skipped-and-reported
