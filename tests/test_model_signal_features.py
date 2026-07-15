"""Prediction-derived signals (spec 012, US2 — materialize + derive).

TDD: written FIRST. Stored predictions become (1) per-stock features named
with the model identity (`pred_q50_h7__<model>_<version>`) so vintages can
never silently mix, and (2) market-level series through the spec-011
DB-resident dispatcher (`macro_model_outlook_q50`, median cross-sectional
q50; `macro_model_confidence_width`, median q90−q10). Gaps stay gaps.
"""
import datetime as dt
import json
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date
CUTOFF = D(2024, 2, 15)
MODEL, VERSION, HORIZON = "msf_model", "vsig", 7
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
    cur.execute("DELETE FROM computed_features WHERE feature_id IN "
                "(SELECT id FROM feature_definitions WHERE name = ANY(%s))",
                (PRED_FEATURES + [f"macro_{n}" for n in MARKET_FNS],))
    cur.execute("DELETE FROM feature_definitions WHERE name = ANY(%s)",
                (PRED_FEATURES + [f"macro_{n}" for n in MARKET_FNS],))
    cur.execute("DELETE FROM feature_functions WHERE name = ANY(%s)",
                (MARKET_FNS,))
    cur.execute("DELETE FROM macro_series WHERE name = ANY(%s)", (MARKET_FNS,))
    cur.execute("DELETE FROM predictions WHERE model_id IN "
                "(SELECT id FROM ml_models WHERE name = %s)", (MODEL,))
    cur.execute("DELETE FROM ml_models WHERE name = %s", (MODEL,))
    cur.execute("DELETE FROM ml_runs WHERE dataset_id IN "
                "(SELECT id FROM ml_datasets WHERE name = 'msf')")
    cur.execute("DELETE FROM ml_datasets WHERE name = 'msf'")
    cur.execute("DELETE FROM computed_features WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'MSF%')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'MSF%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'MSF%'")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """2 stocks x 120 days spanning the cutoff; vintage model trained and
    predictions backfilled — the increment-(b) door this increment builds on."""
    from typer.testing import CliRunner
    from gefion.cli import app
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO stocks (symbol, asset_type) VALUES "
                    "('MSF1','Stock'),('MSF2','Stock') RETURNING id")
        ids = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT id FROM feature_definitions WHERE name='indicator_rsi_14'")
        row = cur.fetchone()
        if row:
            fid = row[0]
        else:
            cur.execute("INSERT INTO feature_definitions (name, function_name, "
                        "entity_table) VALUES ('indicator_rsi_14','indicator','stocks') "
                        "RETURNING id")
            fid = cur.fetchone()[0]
        base = D(2024, 1, 1)
        for i in range(120):
            d = base + dt.timedelta(days=i)
            for j, sid in enumerate(ids):
                close = 100.0 + i * 0.1 + j
                cur.execute("""INSERT INTO stock_ohlcv (data_id, date, open, high,
                    low, close, adjusted_close, volume)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,1000) ON CONFLICT DO NOTHING""",
                            (sid, d, close, close, close, close, close))
                cur.execute("""INSERT INTO computed_features (data_id, date,
                    feature_id, value) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (sid, d, fid, 50.0 + i * 0.1))
    runner = CliRunner()
    out_dir = tmp_path_factory.mktemp("msf")
    for cmd in (
        ["ml", "dataset-build", "--name", "msf", "--version", "v1",
         "--symbols", "MSF1,MSF2", "--horizons", str(HORIZON),
         "--weak-thresholds", "0.02", "--strong-thresholds", "0.05",
         "--end-date", CUTOFF.isoformat(),
         "--out-dir", str(out_dir), "--export",
         "--db-url", schema.test_db_url()],
        ["ml", "train", "--dataset-name", "msf", "--dataset-version", "v1",
         "--model-name", MODEL, "--model-version", VERSION,
         "--algorithm", "quantile_regression",
         "--out-dir", str(out_dir / "models"),
         "--db-url", schema.test_db_url()],
        ["ml", "predict-backfill", "--model-name", MODEL,
         "--model-version", VERSION, "--db-url", schema.test_db_url()],
    ):
        r = runner.invoke(app, cmd)
        assert r.exit_code == 0, r.output
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _materialize(runner):
    from gefion.cli import app
    return runner.invoke(app, ["ml", "materialize-signals",
                               "--model-name", MODEL,
                               "--model-version", VERSION,
                               "--db-url", schema.test_db_url()])


def test_backfill_materializes_its_own_signals(world):
    """predict-backfill must leave the pred_* FEATURE rows current, not just
    the predictions table — the nightly chain derives the model series from
    the features, and a backfill that stops at the predictions table lets
    them silently go stale (prod, 2026-07-14: predictions through 07-13,
    features through 07-10, model series wrote 0). This test runs FIRST in
    the file: no explicit materialize-signals has happened yet."""
    with world.cursor() as cur:
        cur.execute("""SELECT count(*) FROM predictions p
                       JOIN ml_models m ON m.id = p.model_id
                       WHERE m.name = %s AND m.version = %s""", (MODEL, VERSION))
        n_preds = cur.fetchone()[0]
        assert n_preds > 0
        cur.execute("""SELECT count(*) FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = ANY(%s)""", (PRED_FEATURES,))
        assert cur.fetchone()[0] == 3 * n_preds


def test_materialize_creates_prediction_features(world):
    """Predictions become per-stock features named with the model identity;
    values match the stored quantiles row-for-row (nothing fabricated)."""
    from typer.testing import CliRunner
    r = _materialize(CliRunner())
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        for feat in PRED_FEATURES:
            cur.execute("SELECT entity_table, params FROM feature_definitions "
                        "WHERE name = %s", (feat,))
            row = cur.fetchone()
            assert row is not None, f"missing registry row for {feat}"
            assert row[0] == "stocks"
            params = row[1] if isinstance(row[1], dict) else json.loads(row[1])
            assert params["model_name"] == MODEL
            assert params["training_cutoff"] == CUTOFF.isoformat()
        # every prediction row materialized, for each quantile, no extras
        cur.execute("""SELECT count(*) FROM predictions p
                       JOIN ml_models m ON m.id = p.model_id
                       WHERE m.name = %s AND m.version = %s""", (MODEL, VERSION))
        n_preds = cur.fetchone()[0]
        assert n_preds > 0
        cur.execute("""SELECT count(*), min(cf.date) FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = ANY(%s)""", (PRED_FEATURES,))
        n_vals, min_date = cur.fetchone()
        assert n_vals == 3 * n_preds
        assert min_date > CUTOFF                     # strictly out-of-sample
        # spot-check: q50 value equals the stored prediction's q50
        cur.execute("""SELECT cf.value, (p.prediction_values->>'q50')::float
                       FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       JOIN predictions p ON p.data_id = cf.data_id
                            AND p.prediction_date = cf.date
                       WHERE fd.name = %s LIMIT 5""", (PRED_FEATURES[1],))
        rows = cur.fetchall()
        assert rows and all(abs(a - b) < 1e-9 for a, b in rows)


def test_materialize_is_idempotent(world):
    from typer.testing import CliRunner
    runner = CliRunner()
    assert _materialize(runner).exit_code == 0
    with world.cursor() as cur:
        cur.execute("""SELECT count(*) FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = ANY(%s)""", (PRED_FEATURES,))
        before = cur.fetchone()[0]
    assert _materialize(runner).exit_code == 0
    with world.cursor() as cur:
        cur.execute("""SELECT count(*) FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = ANY(%s)""", (PRED_FEATURES,))
        assert cur.fetchone()[0] == before


def test_materialize_seeds_market_bodies_db_wins(world):
    """The two market bodies land in feature_functions (scope='market') with
    the model identity in provenance — and a later materialize does NOT
    overwrite an operator-edited body (the 011 DB-wins rule)."""
    from typer.testing import CliRunner
    runner = CliRunner()
    assert _materialize(runner).exit_code == 0
    with world.cursor() as cur:
        for fn in MARKET_FNS:
            cur.execute("SELECT scope, function_body, description "
                        "FROM feature_functions WHERE name = %s", (fn,))
            row = cur.fetchone()
            assert row is not None, f"missing market function {fn}"
            assert row[0] == "market"
            assert f"__{MODEL}_{VERSION}" in row[1]   # body reads the vintage
            assert CUTOFF.isoformat() in row[2]       # provenance in description
        cur.execute("UPDATE feature_functions SET function_body = %s "
                    "WHERE name = %s", ("def compute(rows):\n    return 42.0\n",
                                        MARKET_FNS[0]))
    assert _materialize(runner).exit_code == 0
    with world.cursor() as cur:
        cur.execute("SELECT function_body FROM feature_functions WHERE name = %s",
                    (MARKET_FNS[0],))
        assert "42.0" in cur.fetchone()[0]            # DB wins, seed did not clobber
        # restore for the derive test
        cur.execute("DELETE FROM feature_functions WHERE name = %s",
                    (MARKET_FNS[0],))
    assert _materialize(runner).exit_code == 0


def test_derive_computes_model_series(world):
    """`macro derive` executes the seeded bodies over the cross-section:
    outlook = median q50, width = median (q90−q10), post-cutoff only."""
    from typer.testing import CliRunner
    from gefion.cli import app
    assert _materialize(CliRunner()).exit_code == 0    # independent of test order
    r = CliRunner().invoke(app, ["macro", "derive",
                                 "--series", ",".join(MARKET_FNS),
                                 "--min-stocks", "2",
                                 "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        cur.execute("""SELECT cf.date, cf.value FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = 'macro_model_outlook_q50'
                       ORDER BY cf.date LIMIT 1""")
        row = cur.fetchone()
        assert row is not None, "no derived outlook values"
        d0, outlook = row
        assert d0 > CUTOFF
        # median of 2 stocks' q50 == their mean
        cur.execute("""SELECT avg((p.prediction_values->>'q50')::float)
                       FROM predictions p JOIN ml_models m ON m.id = p.model_id
                       WHERE m.name = %s AND m.version = %s
                         AND p.prediction_date = %s""", (MODEL, VERSION, d0))
        assert abs(outlook - cur.fetchone()[0]) < 1e-9
        cur.execute("""SELECT count(*) FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = 'macro_model_confidence_width'""")
        assert cur.fetchone()[0] > 0


def test_materialize_refuses_unknown_and_unvintaged(world):
    from typer.testing import CliRunner
    from gefion.cli import app
    runner = CliRunner()
    r = runner.invoke(app, ["ml", "materialize-signals",
                            "--model-name", "nope", "--model-version", "v0",
                            "--db-url", schema.test_db_url()])
    assert r.exit_code == 1
    assert "ml train" in r.output                     # names the fixing command
    with world.cursor() as cur:
        cur.execute("""INSERT INTO ml_models (name, version, algorithm,
                       hyperparams, artifact_uri)
                       VALUES ('msf_novintage','v0','quantile', '{}', '/tmp/x')
                       ON CONFLICT DO NOTHING""")
    try:
        r = runner.invoke(app, ["ml", "materialize-signals",
                                "--model-name", "msf_novintage",
                                "--model-version", "v0",
                                "--db-url", schema.test_db_url()])
        assert r.exit_code == 1
        assert "cutoff" in r.output.lower()
    finally:
        with world.cursor() as cur:
            cur.execute("DELETE FROM ml_models WHERE name = 'msf_novintage'")


def test_materialize_incremental_starts_at_last_month(world):
    """Nightly materialize must not rescan all of prediction history (#120:
    the full scan became an hour-plus nightly tail). Incremental (default)
    starts at the month of the last materialized feature row — sound because
    predict-backfill appends strictly forward; full=True remains the
    deliberate full rescan. Runs LAST: it mutates old feature rows."""
    from gefion.ml.signal_features import materialize_prediction_features

    # ensure fully materialized, then hollow out the FIRST month
    materialize_prediction_features(world, MODEL, VERSION)
    with world.cursor() as cur:
        cur.execute("""SELECT date_trunc('month', min(cf.date))::date,
                              date_trunc('month', max(cf.date))::date
                       FROM computed_features cf
                       JOIN feature_definitions fd ON fd.id = cf.feature_id
                       WHERE fd.name = ANY(%s)""", (PRED_FEATURES,))
        first_month, last_month = cur.fetchone()
        assert first_month < last_month  # world spans multiple months
        cur.execute("""DELETE FROM computed_features cf
                       USING feature_definitions fd
                       WHERE fd.id = cf.feature_id AND fd.name = ANY(%s)
                         AND cf.date < %s""",
                    (PRED_FEATURES, first_month + dt.timedelta(days=31)))

    def _first_month_rows():
        with world.cursor() as cur:
            cur.execute("""SELECT count(*) FROM computed_features cf
                           JOIN feature_definitions fd ON fd.id = cf.feature_id
                           WHERE fd.name = ANY(%s) AND cf.date
                                 < %s""",
                        (PRED_FEATURES, first_month + dt.timedelta(days=31)))
            return cur.fetchone()[0]

    assert _first_month_rows() == 0
    # incremental: scan starts at the last materialized month — the hole
    # in an EARLIER month is deliberately out of scope
    materialize_prediction_features(world, MODEL, VERSION)
    assert _first_month_rows() == 0
    # full: the deliberate rescan refills it
    materialize_prediction_features(world, MODEL, VERSION, full=True)
    assert _first_month_rows() > 0
