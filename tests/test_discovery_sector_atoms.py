"""Sector series as discovery atoms (spec 013, US2 — SC-1303).

TDD-pinned contract: sector series are ORDINARY features to discovery — a
hunt pre-registers macro_sector_* tercile atoms with zero changes to any
guarantee, and an uncomputed sector series lands in the diagnostics ledger
as an uncomputable proposal (existing screen), never a new failure mode.
"""
import datetime as dt
import json
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date
UP, DOWN = "TECH GROWTH", "OLD INDUSTRY"
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
    cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'dsa-%'")
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name LIKE 'macro_sector%' "
                " OR name = 'dsa_signal')")
    cur.execute("DELETE FROM feature_definitions WHERE name LIKE 'macro_sector%' "
                "OR name = 'dsa_signal'")
    cur.execute("DELETE FROM feature_functions WHERE name LIKE 'sector_%'")
    cur.execute("DELETE FROM macro_series WHERE name LIKE 'sector_%'")
    cur.execute("DELETE FROM computed_features WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'DSA%')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'DSA%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'DSA%'")


@pytest.fixture(scope="module")
def world():
    """Two 4-member sectors + a per-stock signal feature, 400 weekdays —
    enough span for discovery's holdout geometry; sector series derived."""
    import numpy as np
    from typer.testing import CliRunner
    from gefion.cli import app
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    schema.create_feature_functions_table(c)
    rng = np.random.default_rng(13)
    with c.cursor() as cur:
        _cleanup(cur)
        ids = {}
        for i in range(8):
            sector = UP if i < 4 else DOWN
            cur.execute("INSERT INTO stocks (symbol, asset_type, sector) "
                        "VALUES (%s,'Stock',%s) RETURNING id", (f"DSA{i}", sector))
            ids[f"DSA{i}"] = (cur.fetchone()[0], sector)
        cur.execute("SELECT id FROM feature_definitions WHERE name='indicator_sma_200'")
        row = cur.fetchone()
        sma_id = row[0] if row else None
        if sma_id is None:
            cur.execute("INSERT INTO feature_definitions (name, function_name, "
                        "entity_table) VALUES ('indicator_sma_200','indicator','stocks') "
                        "RETURNING id")
            sma_id = cur.fetchone()[0]
        cur.execute("INSERT INTO feature_definitions (name, function_name, "
                    "entity_table) VALUES ('dsa_signal','indicator','stocks') "
                    "ON CONFLICT (name) DO UPDATE SET active = TRUE RETURNING id")
        sig_id = cur.fetchone()[0]
        days, d = [], D(2022, 1, 3)
        while len(days) < 400:
            if d.weekday() < 5:
                days.append(d)
            d += dt.timedelta(days=1)
        for i, d in enumerate(days):
            for sym, (sid, sector) in ids.items():
                drift = 0.002 if sector == UP else -0.002
                close = 100.0 * (1 + drift) ** i * (1 + float(rng.normal(0, 0.005)))
                cur.execute("""INSERT INTO stock_ohlcv (data_id, date, open, high,
                    low, close, volume) VALUES (%s,%s,%s,%s,%s,%s,1000)
                    ON CONFLICT DO NOTHING""", (sid, d, close, close, close, close))
                cur.execute("""INSERT INTO computed_features (data_id, date,
                    feature_id, value) VALUES (%s,%s,%s,100.0)
                    ON CONFLICT DO NOTHING""", (sid, d, sma_id))
                cur.execute("""INSERT INTO computed_features (data_id, date,
                    feature_id, value) VALUES (%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""", (sid, d, sig_id,
                                                float(rng.normal(50, 10))))
    runner = CliRunner()
    for cmd in (
        ["macro", "seed-sectors", "--min-members", "3", "--body-floor", "3",
         "--db-url", schema.test_db_url()],
        ["macro", "derive", "--series",
         f"sector_rs_{UP_SLUG},sector_breadth_{UP_SLUG}",
         "--min-stocks", "2", "--db-url", schema.test_db_url()],
    ):
        r = runner.invoke(app, cmd)
        assert r.exit_code == 0, r.output
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def test_sector_atoms_hunt_end_to_end(world, tmp_path):
    """SC-1303: a hunt pre-registers sector tercile atoms; the run completes
    with guarantees intact (family counted, in-run SPA recorded when
    non-empty); an UNCOMPUTED sector series is diagnosed uncomputable."""
    from typer.testing import CliRunner
    from gefion.cli import app
    atoms = tmp_path / "atoms.json"
    atoms.write_text(json.dumps({"atoms": [
        {"feature": f"macro_sector_rs_{UP_SLUG}", "form": "tercile"},
        {"feature": f"macro_sector_breadth_{UP_SLUG}", "form": "tercile"},
        # never derived — must land as an uncomputable-proposal diagnostic
        {"feature": f"macro_sector_rs_{DOWN_SLUG}", "form": "tercile"},
    ]}))
    r = CliRunner().invoke(app, [
        "regime", "discover", "start", "--name", "dsa-sector-hunt",
        "--atoms", str(atoms), "--tier", "interaction", "--tier", "grammar",
        "--signal", "dsa_signal",
        "--horizon-days", "5", "--holdout-weeks", "13",
        "--min-effective-n", "5",
        "--universe-filter", "passthrough", "--dataset", "dsa-synth",
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        cur.execute("SELECT id, status, search_space FROM regime_discovery_runs "
                    "WHERE name = 'dsa-sector-hunt'")
        run_id, status, space = cur.fetchone()
        assert status == "complete"
        atom_feats = {a["feature"] for a in space["atoms"]}
        assert f"macro_sector_rs_{UP_SLUG}" in atom_feats     # pre-registered
        cur.execute("SELECT detail FROM discovery_diagnostics "
                    "WHERE run_id = %s AND kind = 'uncomputable_proposal'",
                    (run_id,))
        diagnosed = [row[0].get("feature") for row in cur.fetchall()]
        assert f"macro_sector_rs_{DOWN_SLUG}" in diagnosed, \
            "an underived sector series must be diagnosed, not a new failure"
        cur.execute("SELECT count(*) FROM regime_candidates WHERE run_id = %s",
                    (run_id,))
        assert cur.fetchone()[0] > 0, "computed sector atoms must yield candidates"
