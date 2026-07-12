"""The model_predictions discovery rung (spec 012, US2/US3 — FR-1204..1207).

TDD: written FIRST. A hunt may declare signal_source=model_predictions and
name prediction-derived series; every existing guarantee applies unchanged,
plus the rung's own honesty rules: explicit model-derived signals only (no
silent degradation to indicator hunts), the run records the model identity
and training cutoff, windows touching the cutoff refuse (lookahead by
construction), thin coverage refuses naming the fixing command, and the
conservative entanglement rule refuses atoms conditioning on ANY feature the
model consumed.
"""
import datetime as dt
import json
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date
CUTOFF = D(2023, 6, 30)
MODEL, VERSION, HORIZON = "mds_model", "v1", 7
SIGNALS = ["macro_model_outlook_q50", "macro_model_confidence_width"]
PRED_FEATURES = [f"pred_{q}_h{HORIZON}__{MODEL}_{VERSION}"
                 for q in ("q10", "q50", "q90")]
MARKET_FNS = ["model_outlook_q50", "model_confidence_width"]


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
    cur.execute("DELETE FROM regime_discovery_runs WHERE name LIKE 'mds-%'")
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name = ANY(%s))",
                (PRED_FEATURES + SIGNALS,))
    cur.execute("DELETE FROM feature_definitions WHERE name = ANY(%s)",
                (PRED_FEATURES + SIGNALS,))
    cur.execute("DELETE FROM feature_functions WHERE name = ANY(%s)",
                (MARKET_FNS,))
    cur.execute("DELETE FROM macro_series WHERE name = ANY(%s)", (MARKET_FNS,))
    cur.execute("DELETE FROM predictions WHERE model_id IN "
                "(SELECT id FROM ml_models WHERE name = %s)", (MODEL,))
    cur.execute("DELETE FROM ml_models WHERE name = %s", (MODEL,))
    cur.execute("DELETE FROM ml_runs WHERE dataset_id IN "
                "(SELECT id FROM ml_datasets WHERE name = 'mds')")
    cur.execute("DELETE FROM ml_datasets WHERE name = 'mds'")
    cur.execute("DELETE FROM computed_features WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'MDS%')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'MDS%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'MDS%'")


def _ensure_feature(cur, name):
    cur.execute("SELECT id FROM feature_definitions WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO feature_definitions (name, function_name, "
                "entity_table) VALUES (%s,'indicator','stocks') RETURNING id",
                (name,))
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """2 stocks x 500 days; vintage model at CUTOFF; predictions backfilled,
    materialized, and derived — a complete post-cutoff signal span.

    indicator_rsi_14 is the model's INPUT feature (entangled by the
    conservative rule); indicator_adx_14 is NOT fed to the model (usable)."""
    import numpy as np
    from typer.testing import CliRunner
    from gefion.cli import app
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    rng = np.random.default_rng(7)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO stocks (symbol, asset_type) VALUES "
                    "('MDS1','Stock'),('MDS2','Stock') RETURNING id")
        ids = [r[0] for r in cur.fetchall()]
        rsi_id = _ensure_feature(cur, "indicator_rsi_14")
        adx_id = _ensure_feature(cur, "indicator_adx_14")
        # weekday-only calendar — prod OHLCV never has weekend rows, and
        # ml predict rightly skips weekends
        days, d = [], D(2023, 1, 2)
        while len(days) < 500:
            if d.weekday() < 5:
                days.append(d)
            d += dt.timedelta(days=1)
        for i, d in enumerate(days):
            for j, sid in enumerate(ids):
                close = 100.0 * (1.0 + 0.0002 * i) + j + float(rng.normal(0, 1))
                cur.execute("""INSERT INTO stock_ohlcv (data_id, date, open, high,
                    low, close, adjusted_close, volume)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,1000) ON CONFLICT DO NOTHING""",
                            (sid, d, close, close, close, close, close))
                for fid, val in ((rsi_id, 50.0 + float(rng.normal(0, 8))),
                                 (adx_id, 25.0 + float(rng.normal(0, 6)))):
                    cur.execute("""INSERT INTO computed_features (data_id, date,
                        feature_id, value) VALUES (%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING""", (sid, d, fid, val))
    runner = CliRunner()
    out_dir = tmp_path_factory.mktemp("mds")
    for cmd in (
        ["ml", "dataset-build", "--name", "mds", "--version", "v1",
         "--symbols", "MDS1,MDS2", "--horizons", str(HORIZON),
         "--features", "indicator_rsi_14",
         "--weak-thresholds", "0.02", "--strong-thresholds", "0.05",
         "--end-date", CUTOFF.isoformat(),
         "--out-dir", str(out_dir), "--export",
         "--db-url", schema.test_db_url()],
        ["ml", "train", "--dataset-name", "mds", "--dataset-version", "v1",
         "--model-name", MODEL, "--model-version", VERSION,
         "--algorithm", "quantile_regression",
         "--out-dir", str(out_dir / "models"),
         "--db-url", schema.test_db_url()],
        ["ml", "predict-backfill", "--model-name", MODEL,
         "--model-version", VERSION, "--db-url", schema.test_db_url()],
        ["ml", "materialize-signals", "--model-name", MODEL,
         "--model-version", VERSION, "--db-url", schema.test_db_url()],
        ["macro", "derive", "--series", ",".join(MARKET_FNS),
         "--min-stocks", "2", "--db-url", schema.test_db_url()],
    ):
        r = runner.invoke(app, cmd)
        assert r.exit_code == 0, f"{cmd[:3]}: {r.output}"
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


@pytest.fixture()
def atoms_file(tmp_path):
    p = tmp_path / "atoms.json"
    p.write_text(json.dumps({"atoms": [
        {"feature": "indicator_adx_14", "form": "tercile"},
        {"feature": "indicator_rsi_14", "form": "tercile"},
    ]}))
    return p


def _start(runner, name, atoms_path, *extra):
    from gefion.cli import app
    return runner.invoke(app, [
        "regime", "discover", "start", "--name", name,
        "--atoms", str(atoms_path),
        "--signal-source", "model_predictions",
        "--signal", SIGNALS[0], "--signal", SIGNALS[1],
        "--horizon-days", str(HORIZON), "--holdout-weeks", "8",
        "--min-effective-n", "5",
        "--universe-filter", "passthrough",
        "--dataset", "mds-synth",
        "--db-url", schema.test_db_url(), *extra])


def test_rung_end_to_end_records_provenance_and_entanglement(world, atoms_file):
    """SC-1203: a full synthetic hunt on model signals completes; the run row
    records signal_source + model identity + cutoff; the model's own input
    feature is refused as a conditioning atom (conservative rule)."""
    from typer.testing import CliRunner
    r = _start(CliRunner(), "mds-e2e", atoms_file)
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        cur.execute("""SELECT id, search_space FROM regime_discovery_runs
                       WHERE name = 'mds-e2e'""")
        run_id, space = cur.fetchone()
        assert space["signal_source"] == "model_predictions"
        model = space["model"]
        assert model["model_name"] == MODEL
        assert model["model_version"] == VERSION
        assert model["training_cutoff"] == CUTOFF.isoformat()
        assert "indicator_rsi_14" in model["input_features"]
        cur.execute("""SELECT detail FROM discovery_diagnostics
                       WHERE run_id = %s AND kind = 'entangled'""", (run_id,))
        entangled = [row[0] for row in cur.fetchall()]
        assert any(d.get("feature") == "indicator_rsi_14" for d in entangled), \
            "the model's input feature must be refused as a conditioning atom"
        assert not any(d.get("feature") == "indicator_adx_14" for d in entangled)


def test_rung_requires_model_derived_signals(world, atoms_file):
    """No silent degradation: indicator signals under the model rung refuse."""
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, [
        "regime", "discover", "start", "--name", "mds-wrongsig",
        "--atoms", str(atoms_file),
        "--signal-source", "model_predictions",
        "--signal", "indicator_adx_14",
        "--universe-filter", "passthrough",
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 1
    assert "materialize-signals" in r.output or "model-derived" in r.output


def test_rung_requires_explicit_signals(world, atoms_file):
    """Defaulting to 'all active features' is meaningless for this rung."""
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, [
        "regime", "discover", "start", "--name", "mds-nosig",
        "--atoms", str(atoms_file),
        "--signal-source", "model_predictions",
        "--universe-filter", "passthrough",
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 1
    assert "--signal" in r.output


def test_rung_refuses_lookahead_window(world, atoms_file):
    """A signal value at or before the cutoff is lookahead by construction
    (it can only mean corrupted materialization) — the run must refuse."""
    from typer.testing import CliRunner
    with world.cursor() as cur:
        cur.execute("""SELECT cf.feature_id, cf.data_id FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = %s LIMIT 1""", (SIGNALS[0],))
        fid, did = cur.fetchone()
        cur.execute("""INSERT INTO computed_features (data_id, date, feature_id,
                       value) VALUES (%s, %s, %s, 0.01)
                       ON CONFLICT (data_id, feature_id, date) DO NOTHING""",
                    (did, CUTOFF, fid))
    try:
        r = _start(CliRunner(), "mds-lookahead", atoms_file)
        assert r.exit_code == 1
        assert "cutoff" in r.output.lower() or "lookahead" in r.output.lower()
    finally:
        with world.cursor() as cur:
            cur.execute("""DELETE FROM computed_features WHERE data_id = %s
                           AND feature_id = %s AND date = %s""",
                        (did, fid, CUTOFF))


def test_rung_refuses_thin_coverage(world, atoms_file):
    """Coverage below the declared floor refuses, naming the backfill door."""
    from typer.testing import CliRunner
    with world.cursor() as cur:
        cur.execute("""SELECT cf.feature_id, cf.data_id FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = %s LIMIT 1""", (SIGNALS[0],))
        fid, did = cur.fetchone()
        # punch a >5% hole in the middle of the signal span
        cur.execute("""DELETE FROM computed_features WHERE feature_id = %s
                       AND date BETWEEN %s AND %s""",
                    (fid, CUTOFF + dt.timedelta(days=60),
                     CUTOFF + dt.timedelta(days=110)))
    try:
        r = _start(CliRunner(), "mds-thin", atoms_file)
        assert r.exit_code == 1
        assert "covers" in r.output.lower()
        assert "predict-backfill" in r.output
    finally:
        from typer.testing import CliRunner as CR
        from gefion.cli import app
        rr = CR().invoke(app, ["macro", "derive", "--series", MARKET_FNS[0],
                               "--full", "--min-stocks", "2",
                               "--db-url", schema.test_db_url()])
        assert rr.exit_code == 0, rr.output


def test_rung_refuses_unknown_signal_source(world, atoms_file):
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, [
        "regime", "discover", "start", "--name", "mds-badsource",
        "--atoms", str(atoms_file),
        "--signal-source", "strategy_backtests",
        "--universe-filter", "passthrough",
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 1
    assert "signal" in r.output.lower()
