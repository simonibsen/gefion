import json
from pathlib import Path

from typer.testing import CliRunner

from g2 import cli


def test_ml_dataset_build_writes_manifest_and_upserts_db(tmp_path, monkeypatch):
    calls = {}

    class DummyConn:
        pass

    class DummyCtx:
        def __enter__(self):
            return DummyConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_db_connection(db_url, autocommit=True):
        calls["db_url"] = db_url
        calls["autocommit"] = autocommit
        return DummyCtx()

    def fake_upsert_ml_dataset(conn, payload):
        calls["payload"] = payload
        return 123

    monkeypatch.setattr(cli, "db_connection", fake_db_connection)
    monkeypatch.setattr(cli, "init_schema_tables", lambda conn, tables: calls.setdefault("tables", tables))
    import g2.ml.store as store

    monkeypatch.setattr(store, "upsert_ml_dataset", fake_upsert_ml_dataset)

    out_dir = tmp_path / "datasets"
    runner = CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "ml",
            "dataset-build",
            "--name",
            "mvp",
            "--version",
            "v1",
            "--exchange",
            "NASDAQ",
            "--limit",
            "10",
            "--lookback-days",
            "200",
            "--horizons",
            "7,30,90",
            "--weak-thresholds",
            "0.02,0.05,0.10",
            "--strong-thresholds",
            "0.05,0.10,0.20",
            "--out-dir",
            str(out_dir),
            "--db-url",
            "postgresql://example",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output

    # Dataset is in a subdirectory: out_dir/mvp_v1/manifest.json
    dataset_dir = out_dir / "mvp_v1"
    manifest_path = dataset_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["name"] == "mvp"
    assert manifest["version"] == "v1"
    assert manifest["universe"]["exchange"] == "NASDAQ"
    assert manifest["universe"]["limit"] == 10
    assert manifest["label_spec"]["thresholds"]["30"]["strong"] == 0.10

    payload = calls["payload"]
    assert payload["name"] == "mvp"
    assert payload["version"] == "v1"
    assert payload["artifact_uri"] == str(manifest_path)
    assert payload["horizons_days"] == [7, 30, 90]


def test_ml_dataset_build_rejects_threshold_mismatch(tmp_path):
    runner = CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "ml",
            "dataset-build",
            "--name",
            "mvp",
            "--version",
            "v1",
            "--symbols",
            "IBM,MSFT",
            "--horizons",
            "7,30,90",
            "--weak-thresholds",
            "0.02,0.05",
            "--out-dir",
            str(tmp_path),
            "--json",
        ],
    )
    assert res.exit_code != 0
    assert "threshold" in res.output.lower()


def test_ml_dataset_build_export_flag_calls_exporter(tmp_path, monkeypatch):
    called = {"export": 0}

    class DummyConn:
        pass

    class DummyCtx:
        def __enter__(self):
            return DummyConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli, "db_connection", lambda *a, **k: DummyCtx())
    monkeypatch.setattr(cli, "init_schema_tables", lambda *a, **k: None)

    import g2.ml.store as store
    monkeypatch.setattr(store, "upsert_ml_dataset", lambda *a, **k: 1)

    import g2.ml.dataset as ds

    def fake_export(conn, *, manifest, out_dir):
        called["export"] += 1

    monkeypatch.setattr(ds, "export_dataset_artifacts", fake_export)

    runner = CliRunner()
    res = runner.invoke(
        cli.app,
        [
            "ml",
            "dataset-build",
            "--name",
            "mvp",
            "--version",
            "v1",
            "--symbols",
            "IBM",
            "--horizons",
            "7,30,90",
            "--weak-thresholds",
            "0.02,0.05,0.10",
            "--strong-thresholds",
            "0.05,0.10,0.20",
            "--out-dir",
            str(tmp_path),
            "--export",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    assert called["export"] == 1
