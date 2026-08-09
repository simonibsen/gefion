"""Parallel-vs-serial correctness test for `ml predict` (#213).

`ml predict` parallelizes its per-symbol prediction/write loop across a
bounded worker pool (mirroring gefion.features.dispatcher's per-symbol
pattern). The one thing that must never change is the OUTPUT: running with
--max-workers 1 (serial) and --max-workers N>1 (parallel) on the same
fixture must produce byte-identical predictions, in the same (symbol,
horizon) order.
"""
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

import gefion.cli as cli
from gefion.db import schema
from gefion.ml.models import train_quantile_model, save_model_artifact

pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS", "0") != "1",
    reason="DB tests disabled (set ENABLE_DB_TESTS=1 to enable)",
)

SYMBOL_PREFIX = "ZZPARTEST"
N_SYMBOLS = 12
FEATURE_NAMES = ["zzpartest_feat_0", "zzpartest_feat_1", "zzpartest_feat_2"]
HORIZONS = [7, 30]
PREDICTION_DATE = "2024-03-15"


def _db_url():
    try:
        import psycopg
        conn = psycopg.connect(schema.test_db_url())
        conn.close()
    except Exception as exc:
        pytest.skip(f"DB not available: {exc}")
    return schema.test_db_url()


@pytest.fixture
def fixture_env():
    """Real DB fixture: stocks + features + a trained model + dataset/model rows.

    Non-destructive — only touches rows scoped to SYMBOL_PREFIX / the test
    model name, cleaned up on teardown.
    """
    import psycopg

    url = _db_url()
    conn = psycopg.connect(url)
    conn.autocommit = True

    schema.create_stocks_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)
    schema.create_ml_datasets_table(conn)
    schema.create_ml_runs_table(conn)
    schema.create_ml_models_table(conn)
    schema.create_predictions_table(conn)

    symbols = [f"{SYMBOL_PREFIX}{i:03d}" for i in range(N_SYMBOLS)]
    data_ids = []
    with conn.cursor() as cur:
        for sym in symbols:
            cur.execute(
                "INSERT INTO stocks (symbol) VALUES (%s) "
                "ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol "
                "RETURNING id",
                (sym,),
            )
            data_ids.append(cur.fetchone()[0])

        feature_ids = {}
        for name in FEATURE_NAMES:
            cur.execute(
                "INSERT INTO feature_definitions "
                "(name, function_name, source_table, source_column, active) "
                "VALUES (%s, 'indicator', 'stock_ohlcv', 'close', TRUE) "
                "ON CONFLICT (name) DO UPDATE SET active = TRUE "
                "RETURNING id",
                (name,),
            )
            feature_ids[name] = cur.fetchone()[0]

        # Deterministic, distinct feature values per symbol so per-symbol
        # predictions differ (a bug that scrambled symbol<->row alignment
        # under parallel execution would show up as mismatched values).
        for idx, data_id in enumerate(data_ids):
            for f_idx, name in enumerate(FEATURE_NAMES):
                value = float(idx) * 0.37 + float(f_idx) * 1.5
                cur.execute(
                    "INSERT INTO computed_features (data_id, date, feature_id, value, source) "
                    "VALUES (%s, %s, %s, %s, 'test') "
                    "ON CONFLICT (feature_id, data_id, date) DO UPDATE SET value = EXCLUDED.value",
                    (data_id, PREDICTION_DATE, feature_ids[name], value),
                )

    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_base = Path(tmpdir) / "zzpartest_model"

        np.random.seed(13)
        X = pd.DataFrame(
            np.random.randn(80, len(FEATURE_NAMES)), columns=FEATURE_NAMES
        )
        y = pd.Series(np.random.randn(80))
        model_data = train_quantile_model(X, y, algorithm="quantile_regression")

        for horizon in HORIZONS:
            save_model_artifact(
                model_data,
                Path(f"{artifact_base}_h{horizon}"),
                metadata={"horizon_days": horizon},
            )

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ml_datasets "
                "(name, version, feature_names, lookback_days, horizons_days, "
                " label_spec, split_spec, artifact_uri) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (name, version) DO UPDATE SET feature_names = EXCLUDED.feature_names "
                "RETURNING id",
                (
                    "zzpartest_dataset", "v1", FEATURE_NAMES, 1, HORIZONS,
                    psycopg.types.json.Json({}), psycopg.types.json.Json({}),
                    str(artifact_base),
                ),
            )
            dataset_id = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO ml_models "
                "(name, version, dataset_id, algorithm, artifact_uri, active) "
                "VALUES (%s, %s, %s, %s, %s, TRUE) "
                "ON CONFLICT (name, version) DO UPDATE SET dataset_id = EXCLUDED.dataset_id "
                "RETURNING id",
                ("zzpartest_model", "v1", dataset_id, "quantile_regression", str(artifact_base)),
            )
            model_id = cur.fetchone()[0]

        yield {
            "url": url,
            "symbols": symbols,
            "data_ids": data_ids,
            "model_id": model_id,
            "dataset_id": dataset_id,
        }

    with conn.cursor() as cur:
        cur.execute("DELETE FROM predictions WHERE model_id = %s", (model_id,))
        cur.execute("DELETE FROM ml_models WHERE id = %s", (model_id,))
        cur.execute("DELETE FROM ml_runs WHERE dataset_id = %s", (dataset_id,))
        cur.execute("DELETE FROM ml_datasets WHERE id = %s", (dataset_id,))
        cur.execute(
            "DELETE FROM computed_features WHERE feature_id = ANY(%s)",
            (list(feature_ids.values()),),
        )
        cur.execute(
            "DELETE FROM feature_definitions WHERE name = ANY(%s)", (FEATURE_NAMES,)
        )
        cur.execute("DELETE FROM stocks WHERE symbol = ANY(%s)", (symbols,))
    conn.close()


def _run_predict(fixture_env, max_workers):
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "ml", "predict",
            "--model-name", "zzpartest_model",
            "--model-version", "v1",
            "--prediction-date", PREDICTION_DATE,
            "--symbols", ",".join(fixture_env["symbols"]),
            "--max-workers", str(max_workers),
            "--db-url", fixture_env["url"],
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return result


def _fetch_predictions(fixture_env):
    import psycopg

    with psycopg.connect(fixture_env["url"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_id, horizon_days, prediction_values "
                "FROM predictions WHERE model_id = %s AND prediction_date = %s "
                "ORDER BY data_id, horizon_days",
                (fixture_env["model_id"], PREDICTION_DATE),
            )
            return cur.fetchall()


def test_predict_parallel_matches_serial_output(fixture_env):
    """--max-workers 1 (serial) and --max-workers 4 (parallel) must produce
    identical predictions, in the same (data_id, horizon) order."""
    _run_predict(fixture_env, max_workers=1)
    serial_rows = _fetch_predictions(fixture_env)

    _run_predict(fixture_env, max_workers=4)
    parallel_rows = _fetch_predictions(fixture_env)

    expected_count = len(fixture_env["symbols"]) * len(HORIZONS)
    assert len(serial_rows) == expected_count
    assert len(parallel_rows) == expected_count

    assert [(r[0], r[1]) for r in serial_rows] == [(r[0], r[1]) for r in parallel_rows]
    for (s_id, s_h, s_vals), (p_id, p_h, p_vals) in zip(serial_rows, parallel_rows):
        assert s_id == p_id
        assert s_h == p_h
        assert s_vals == p_vals


def test_predict_help_documents_max_workers():
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ml", "predict", "--help"])
    assert result.exit_code == 0
    assert "--max-workers" in result.output


def test_predict_auto_workers_capped_by_available_connections(fixture_env, monkeypatch):
    """When --max-workers is unset, the resolved worker count must be capped
    by actually-available DB connections, not just cpu_count.

    A cpu_count-only bound can ask the pool for more connections than the DB
    has headroom for — this is what caused a PoolTimeout in CI's DB-backed
    suite (predict-backfill on a many-core runner against a small test DB,
    #213 fix round 1).
    """
    import multiprocessing

    from gefion.db import pool as db_pool_module
    from gefion.utils import db_load

    monkeypatch.setattr(multiprocessing, "cpu_count", lambda: 33)
    monkeypatch.setattr(db_load, "get_available_connections", lambda url: (10, 100, 90))

    captured = {}
    real_init_pool = db_pool_module.init_pool

    def spy_init_pool(*args, **kwargs):
        captured["max_size"] = kwargs.get("max_size")
        return real_init_pool(*args, **kwargs)

    monkeypatch.setattr(db_pool_module, "init_pool", spy_init_pool)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "ml", "predict",
            "--model-name", "zzpartest_model",
            "--model-version", "v1",
            "--prediction-date", PREDICTION_DATE,
            "--symbols", ",".join(fixture_env["symbols"]),
            "--db-url", fixture_env["url"],
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    # available=10, buffer=5 -> capped worker count = 5, pool max_size = 5+2=7
    # (cpu_count=33 alone would otherwise yield workers=32, max_size=34)
    assert captured["max_size"] == 7


def test_predict_closes_pool_on_mid_run_exception(fixture_env, monkeypatch):
    """A failure partway through the (parallel) prediction loop must still
    close the pool `ml predict` owns.

    Without this, one bad run leaves the global db_pool singleton open and
    every later command/test in the same process silently reuses that
    stale, possibly undersized pool instead of sizing a fresh one for its
    own needs — this is what caused the CI PoolTimeout in an unrelated,
    later test (#213 fix round 2): a prior run's pool leaked because the
    close only ran on the happy path, not on exceptions.
    """
    from gefion.db import pool as db_pool_module
    import gefion.ml.models as ml_models

    assert db_pool_module.get_pool() is None

    def boom(*args, **kwargs):
        raise RuntimeError("simulated prediction failure")

    monkeypatch.setattr(ml_models, "predict_quantiles", boom)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "ml", "predict",
            "--model-name", "zzpartest_model",
            "--model-version", "v1",
            "--prediction-date", PREDICTION_DATE,
            "--symbols", ",".join(fixture_env["symbols"]),
            "--max-workers", "4",
            "--db-url", fixture_env["url"],
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert db_pool_module.get_pool() is None, (
        "predict must close the pool it owns even when the run raises"
    )
