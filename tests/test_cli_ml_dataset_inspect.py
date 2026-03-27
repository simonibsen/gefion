"""Tests for g2 ml dataset-inspect command."""

import json
from typer.testing import CliRunner

from gefion import cli


def test_ml_dataset_inspect_shows_dataset_info(tmp_path, monkeypatch):
    """Should display dataset metadata and dependent models."""

    class DummyCursor:
        def __init__(self):
            self._query_count = 0

        def execute(self, sql, params=None):
            self._last_sql = sql
            self._last_params = params
            self._query_count += 1

        def fetchone(self):
            # Return dataset info for first query
            if "FROM ml_datasets" in self._last_sql and "SELECT" in self._last_sql:
                return (
                    1,  # id
                    "test_dataset",  # name
                    "v1",  # version
                    "2025-01-01 00:00:00",  # created_at
                    {"exchange": "NASDAQ", "limit": 10},  # universe
                    ["indicator_rsi_14", "indicator_sma_20"],  # feature_names
                    [7, 30, 90],  # horizons_days
                    {"thresholds": {"7": {"weak": 0.02, "strong": 0.05}}},  # label_spec
                    "/path/to/manifest.json",  # artifact_uri
                )
            return None

        def fetchall(self):
            # Return models for the models query
            if "FROM ml_models" in self._last_sql:
                return [
                    ("model_a", "v1", "quantile_regression", "2025-01-02 00:00:00"),
                    ("model_b", "v2", "xgboost", "2025-01-03 00:00:00"),
                ]
            return []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class DummyConn:
        def __init__(self):
            self._cursor = DummyCursor()

        def cursor(self):
            return self._cursor

    class DummyCtx:
        def __enter__(self):
            return DummyConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyCtx())
    monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

    runner = CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "ml",
            "dataset-inspect",
            "--name",
            "test_dataset",
            "--version",
            "v1",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output

    # Parse JSON output
    data = json.loads(res.output)
    assert data["status"] == "ok"
    assert data["name"] == "test_dataset"
    assert data["version"] == "v1"
    assert "universe" in data
    assert data["horizons_days"] == [7, 30, 90]
    assert len(data["feature_names"]) == 2

    # Should include dependent models
    assert "models" in data
    assert len(data["models"]) == 2
    assert data["models"][0]["name"] == "model_a"


def test_ml_dataset_inspect_not_found(monkeypatch):
    """Should error when dataset doesn't exist."""

    class DummyCursor:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return None  # Dataset not found

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class DummyConn:
        def cursor(self):
            return DummyCursor()

    class DummyCtx:
        def __enter__(self):
            return DummyConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyCtx())
    monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

    runner = CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "ml",
            "dataset-inspect",
            "--name",
            "nonexistent",
            "--version",
            "v1",
            "--json",
        ],
    )
    assert res.exit_code != 0
    assert "not found" in res.output.lower()
