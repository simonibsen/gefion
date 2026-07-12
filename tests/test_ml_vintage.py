"""Vintage-training causality (spec 012, US1).

TDD: written FIRST. A dataset built with --end-date (the training cutoff)
must contain NOTHING the model shouldn't see: prices/features bounded at
t <= cutoff, and labels bounded at t <= cutoff - horizon (a label's outcome
window must not peek past the cutoff). The manifest records the cutoff so
the trained model's provenance carries it.
"""
import csv
import datetime as dt
import json
import os

import psycopg
import pytest

from gefion.db import schema

D = dt.date
CUTOFF = D(2024, 2, 15)


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
    cur.execute("DELETE FROM ml_models WHERE name = 'mlv_model'")
    cur.execute("DELETE FROM ml_runs WHERE dataset_id IN "
                "(SELECT id FROM ml_datasets WHERE name = 'mlv')")
    cur.execute("DELETE FROM ml_datasets WHERE name = 'mlv'")
    cur.execute("DELETE FROM computed_features WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'MLV%')")
    cur.execute("DELETE FROM stock_ohlcv WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'MLV%')")
    cur.execute("DELETE FROM stocks WHERE symbol LIKE 'MLV%'")


@pytest.fixture(scope="module")
def world():
    """2 stocks x 120 days spanning the cutoff, one feature."""
    c = _conn()
    schema.create_stocks_table(c)
    schema.create_stock_ohlcv_table(c)
    schema.create_feature_definitions_table(c)
    schema.create_computed_features_table(c)
    with c.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO stocks (symbol, asset_type) VALUES "
                    "('MLV1','Stock'),('MLV2','Stock') RETURNING id")
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
    yield c
    with c.cursor() as cur:
        _cleanup(cur)
    c.close()


def _max_date(path, col="date"):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    assert rows, f"empty export: {path}"
    return max(dt.date.fromisoformat(r[col]) for r in rows), rows


def test_dataset_end_date_bounds_everything(world, tmp_path):
    from typer.testing import CliRunner
    from gefion.cli import app
    r = CliRunner().invoke(app, [
        "ml", "dataset-build", "--name", "mlv", "--version", "vtest",
        "--symbols", "MLV1,MLV2", "--horizons", "7",
        "--weak-thresholds", "0.02", "--strong-thresholds", "0.05",
        "--end-date", CUTOFF.isoformat(),
        "--out-dir", str(tmp_path), "--export",
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    manifest_path = next(tmp_path.rglob("manifest.json"))
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    assert manifest["end_date"] == CUTOFF.isoformat()      # cutoff recorded

    max_price, _ = _max_date(root / "prices.csv")
    assert max_price <= CUTOFF                              # prices bounded
    max_feat, _ = _max_date(root / "features.csv")
    assert max_feat <= CUTOFF                               # features bounded
    max_label, rows = _max_date(root / "labels.csv")
    horizon = 7
    assert max_label <= CUTOFF - dt.timedelta(days=horizon), (
        "a label's outcome window must not peek past the cutoff")
    # and labels near the boundary genuinely exist (not over-truncated)
    assert max_label >= CUTOFF - dt.timedelta(days=horizon + 5)


def test_train_records_cutoff_in_model_metadata(world, tmp_path):
    """The trained model's stored provenance carries the training cutoff —
    every downstream door (backfill, discovery rung) validates against it."""
    from typer.testing import CliRunner
    from gefion.cli import app
    runner = CliRunner()
    r = runner.invoke(app, [
        "ml", "dataset-build", "--name", "mlv", "--version", "vtrain",
        "--symbols", "MLV1,MLV2", "--horizons", "7",
        "--weak-thresholds", "0.02", "--strong-thresholds", "0.05",
        "--end-date", CUTOFF.isoformat(),
        "--out-dir", str(tmp_path), "--export",
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, [
        "ml", "train", "--dataset-name", "mlv", "--dataset-version", "vtrain",
        "--model-name", "mlv_model", "--model-version", "vtest",
        "--algorithm", "quantile_regression",
        "--out-dir", str(tmp_path / "models"),
        "--db-url", schema.test_db_url()])
    assert r.exit_code == 0, r.output
    with world.cursor() as cur:
        cur.execute("SELECT hyperparams->>'training_cutoff' FROM ml_models "
                    "WHERE name='mlv_model' AND version='vtest'")
        assert cur.fetchone()[0] == CUTOFF.isoformat()
        cur.execute("DELETE FROM ml_models WHERE name='mlv_model'")
