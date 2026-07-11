"""Per-entity (sector/industry/asset) regime labels (issue #86 item 1; 005 FR-002).

TDD: written FIRST. Asset scope labels each stock from its OWN series;
sector/industry scopes label every member of a group identically from the
group's aggregate (median) series — two stocks in different sectors on the
same date can carry different labels (spec 005 acceptance #2). Persistence
is applied per group series, and honest errors replace silent empties.
"""
import datetime as dt
import os

import psycopg
import pytest

from gefion.db import schema
from gefion.regimes.definitions import RegimeDefinition

D = dt.date


def _defn(name="ent-vol", scope="asset", expression=None, persistence=None):
    return RegimeDefinition(
        name=name, scope=scope,
        expression=expression or {"leaf": "comparison", "feature": "ent_f",
                                  "cmp": ">", "value": 0.0, "scope": scope},
        bucketing={"kind": "threshold", "labels": ["false", "true"]},
        persistence=persistence)


def _series(vals, start=D(2024, 1, 1)):
    return [(start + dt.timedelta(days=i), float(v)) for i, v in enumerate(vals)]


# --- pure: group labeling -------------------------------------------------------------

def test_asset_scope_labels_each_entity_from_its_own_series():
    from gefion.regimes.labels import compute_entity_labels
    defn = _defn(scope="asset")
    groups = {"A": {"ent_f": _series([1.0, -1.0, 1.0])},
              "B": {"ent_f": _series([-1.0, 1.0, -1.0])}}
    members = {"A": [101], "B": [202]}
    rows = compute_entity_labels(defn, groups, members)
    by = {(d, e): lab for d, e, lab in rows}
    d0 = D(2024, 1, 1)
    assert by[(d0, 101)] == "true" and by[(d0, 202)] == "false"   # same date, differ
    assert by[(d0 + dt.timedelta(days=1), 101)] == "false"


def test_sector_scope_labels_all_members_identically():
    from gefion.regimes.labels import compute_entity_labels
    defn = _defn(scope="sector")
    groups = {"Tech": {"ent_f": _series([1.0, 1.0])},
              "Energy": {"ent_f": _series([-1.0, -1.0])}}
    members = {"Tech": [11, 12], "Energy": [21]}
    rows = compute_entity_labels(defn, groups, members)
    by = {(d, e): lab for d, e, lab in rows}
    d0 = D(2024, 1, 1)
    assert by[(d0, 11)] == by[(d0, 12)] == "true"                 # shared label
    assert by[(d0, 21)] == "false"                                # other sector differs


def test_persistence_applied_per_group():
    from gefion.regimes.labels import compute_entity_labels
    defn = _defn(scope="asset", persistence={"min_dwell": 2})
    groups = {"A": {"ent_f": _series([1.0, -1.0, 1.0, 1.0])}}     # 1-day flicker
    members = {"A": [7]}
    rows = compute_entity_labels(defn, groups, members)
    labs = [lab for _, _, lab in sorted(rows)]
    assert labs[1] == "true"                                      # flicker absorbed


# --- DB: loaders + end-to-end ---------------------------------------------------------

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
    cur.execute("DELETE FROM regime_labels WHERE regime_id IN "
                "(SELECT id FROM regime_definitions WHERE name LIKE 'entl-%')")
    cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'entl-%'")
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name LIKE 'entl_%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'entl_%'")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'ENTL%'")


@pytest.fixture(scope="module")
def world():
    """3 stocks: two in Tech (opposite-signed series), one in Energy; one
    macro-entity feature to prove the honest per-entity refusal."""
    c = _conn()
    schema.create_stocks_table(c)              # full columns, any test order
    schema.create_feature_definitions_table(c)  # self-heals entity_table
    schema.create_computed_features_table(c)   # lazy table; don't rely on test order
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("""INSERT INTO stocks (symbol, sector, asset_type) VALUES
            ('ENTL1','Tech','Common Stock'),
            ('ENTL2','Tech','Common Stock'),
            ('ENTL3','Energy','Common Stock') RETURNING id""")
        ids = [r[0] for r in cur.fetchall()]
        cur.execute("INSERT INTO feature_definitions (name, function_name, entity_table) "
                    "VALUES ('entl_f', 'indicator', 'stocks') RETURNING id")
        fid = cur.fetchone()[0]
        cur.execute("INSERT INTO feature_definitions (name, function_name, entity_table) "
                    "VALUES ('entl_macro', 'indicator', 'macro_series') RETURNING id")
        base = D(2024, 1, 1)
        vals = {ids[0]: 5.0, ids[1]: 3.0, ids[2]: -4.0}           # Tech +, Energy -
        for i in range(5):
            for sid, v in vals.items():
                cur.execute("INSERT INTO computed_features (data_id, date, feature_id, "
                            "value) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                            (sid, base + dt.timedelta(days=i), fid, v))
    yield c, ids
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _store_defn(conn, name, scope, feature="entl_f"):
    from gefion.regimes.definitions import store_definition
    store_definition(conn, _defn(name=name, scope=scope,
                                 expression={"leaf": "comparison",
                                             "feature": feature,
                                             "cmp": ">", "value": 0.0,
                                             "scope": scope}))


def test_cli_compute_asset_scope_stores_per_stock_labels(world):
    from typer.testing import CliRunner
    from gefion.cli import app
    conn, ids = world
    _store_defn(conn, "entl-asset", "asset")
    r = CliRunner().invoke(app, ["regime", "compute", "entl-asset",
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    with conn.cursor() as cur:
        cur.execute("""SELECT l.entity_id, l.label FROM regime_labels l
                       JOIN regime_definitions d ON d.id = l.regime_id
                       WHERE d.name = 'entl-asset' AND l.date = '2024-01-03'""")
        by = dict(cur.fetchall())
    assert by[ids[0]] == "true" and by[ids[2]] == "false"         # per-stock
    assert 0 not in by                                            # no market sentinel


def test_cli_compute_sector_scope_groups_members(world):
    from typer.testing import CliRunner
    from gefion.cli import app
    conn, ids = world
    _store_defn(conn, "entl-sector", "sector")
    r = CliRunner().invoke(app, ["regime", "compute", "entl-sector",
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    with conn.cursor() as cur:
        cur.execute("""SELECT l.entity_id, l.label FROM regime_labels l
                       JOIN regime_definitions d ON d.id = l.regime_id
                       WHERE d.name = 'entl-sector' AND l.date = '2024-01-03'""")
        by = dict(cur.fetchall())
    # Tech members share their sector's label (median of +5,+3 > 0)
    assert by[ids[0]] == by[ids[1]] == "true"
    assert by[ids[2]] == "false"                                  # Energy


def test_market_scope_unchanged_entity_zero(world):
    from typer.testing import CliRunner
    from gefion.cli import app
    conn, _ = world
    _store_defn(conn, "entl-mkt", "market")
    r = CliRunner().invoke(app, ["regime", "compute", "entl-mkt",
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT l.entity_id FROM regime_labels l
                       JOIN regime_definitions d ON d.id = l.regime_id
                       WHERE d.name = 'entl-mkt'""")
        assert [row[0] for row in cur.fetchall()] == [0]


def test_macro_feature_refuses_per_entity_scope(world):
    from typer.testing import CliRunner
    from gefion.cli import app
    conn, _ = world
    _store_defn(conn, "entl-macro", "asset", feature="entl_macro")
    r = CliRunner().invoke(app, ["regime", "compute", "entl-macro",
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 1
    assert "entity" in r.output.lower()                           # names the reason
