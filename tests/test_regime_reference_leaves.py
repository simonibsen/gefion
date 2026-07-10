"""Reference-leaf resolution (issue #86 item 2; 005 FR-019/020).

TDD: written FIRST. A reference leaf resolves a STORED regime's computed
labels as a boolean sub-condition — compose regimes by name. v1 scope:
market-scope composites; multi-bucket references name their bucket; dates
where the referenced regime is undefined or missing drop out (intersection —
an unknown state is not evidence either way); unknown/uncomputed/mixed-scope
references refuse honestly.
"""
import datetime as dt
import os

import psycopg
import pytest

from gefion.db import schema
from gefion.regimes.definitions import RegimeDefinition

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
    cur.execute("DELETE FROM regime_labels WHERE regime_id IN "
                "(SELECT id FROM regime_definitions WHERE name LIKE 'refl-%')")
    cur.execute("DELETE FROM regime_definitions WHERE name LIKE 'refl-%'")
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name LIKE 'refl_%')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'refl_%'")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'REFL%'")


@pytest.fixture(scope="module")
def world():
    """One stock, two features over 4 days: f = [+,+,-,-], g = [+,-,+,-]."""
    c = _conn()
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO stocks (symbol, name, asset_type) VALUES "
                    "('REFL1','A','Common Stock') RETURNING id")
        sid = cur.fetchone()[0]
        fids = {}
        for feat in ("refl_f", "refl_g"):
            cur.execute("INSERT INTO feature_definitions (name, function_name, "
                        "entity_table) VALUES (%s, 'indicator', 'stocks') "
                        "RETURNING id", (feat,))
            fids[feat] = cur.fetchone()[0]
        base = D(2024, 1, 1)
        series = {"refl_f": [1.0, 1.0, -1.0, -1.0], "refl_g": [1.0, -1.0, 1.0, -1.0]}
        for feat, vals in series.items():
            for i, v in enumerate(vals):
                cur.execute("INSERT INTO computed_features (data_id, date, "
                            "feature_id, value) VALUES (%s, %s, %s, %s) "
                            "ON CONFLICT DO NOTHING",
                            (sid, base + dt.timedelta(days=i), fids[feat], v))
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _store(conn, name, expression, scope="market"):
    from gefion.regimes.definitions import store_definition
    store_definition(conn, RegimeDefinition(
        name=name, scope=scope, expression=expression,
        bucketing={"kind": "threshold", "labels": ["false", "true"]},
        persistence=None))


def _compute(name):
    from typer.testing import CliRunner
    from gefion.cli import app
    return CliRunner().invoke(app, ["regime", "compute", name,
                                    "--db-url", schema.test_db_url()])


def _cmp(feature):
    return {"leaf": "comparison", "feature": feature, "cmp": ">", "value": 0.0,
            "scope": "market"}


def test_composite_of_reference_and_comparison(world):
    conn = world
    _store(conn, "refl-base", _cmp("refl_f"))
    assert _compute("refl-base").exit_code == 0
    _store(conn, "refl-and", {"op": "AND", "children": [
        {"leaf": "reference", "regime": "refl-base"}, _cmp("refl_g")]})
    r = _compute("refl-and")
    assert r.exit_code == 0, r.output
    with conn.cursor() as cur:
        cur.execute("""SELECT l.date, l.label FROM regime_labels l
                       JOIN regime_definitions d ON d.id = l.regime_id
                       WHERE d.name = 'refl-and' ORDER BY l.date""")
        labs = {d: lab for d, lab in cur.fetchall()}
    base = D(2024, 1, 1)
    assert labs[base] == "true"                       # f>0 AND g>0
    assert labs[base + dt.timedelta(days=1)] == "false"
    assert labs[base + dt.timedelta(days=2)] == "false"


def test_reference_dates_intersect_not_pad(world):
    """Referenced labels missing on some dates -> those dates drop out."""
    conn = world
    _store(conn, "refl-short", _cmp("refl_f"))
    assert _compute("refl-short").exit_code == 0
    with conn.cursor() as cur:                        # amputate the last 2 days
        cur.execute("""DELETE FROM regime_labels WHERE date > '2024-01-02' AND
                       regime_id = (SELECT id FROM regime_definitions
                                    WHERE name='refl-short')""")
    _store(conn, "refl-ixn", {"op": "AND", "children": [
        {"leaf": "reference", "regime": "refl-short"}, _cmp("refl_g")]})
    assert _compute("refl-ixn").exit_code == 0
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM regime_labels l
                       JOIN regime_definitions d ON d.id = l.regime_id
                       WHERE d.name = 'refl-ixn'""")
        assert cur.fetchone()[0] == 2                 # intersection only


def test_unknown_reference_refuses(world):
    conn = world
    _store(conn, "refl-bad", {"op": "AND", "children": [
        {"leaf": "reference", "regime": "refl-nonexistent"}, _cmp("refl_g")]})
    r = _compute("refl-bad")
    assert r.exit_code == 1
    assert "refl-nonexistent" in r.output


def test_uncomputed_reference_refuses_naming_compute(world):
    conn = world
    _store(conn, "refl-uncomputed", _cmp("refl_f"))   # stored, never computed
    _store(conn, "refl-needs", {"op": "AND", "children": [
        {"leaf": "reference", "regime": "refl-uncomputed"}, _cmp("refl_g")]})
    r = _compute("refl-needs")
    assert r.exit_code == 1
    assert "compute" in r.output.lower()              # names the fix


def test_per_entity_composite_with_reference_refuses(world):
    conn = world
    _store(conn, "refl-base2", _cmp("refl_f"))
    assert _compute("refl-base2").exit_code == 0
    _store(conn, "refl-asset", {"op": "AND", "children": [
        {"leaf": "reference", "regime": "refl-base2"},
        {"leaf": "comparison", "feature": "refl_g", "cmp": ">", "value": 0.0,
         "scope": "asset"}]}, scope="asset")
    r = _compute("refl-asset")
    assert r.exit_code == 1
    assert "market" in r.output.lower()               # v1 limitation named
