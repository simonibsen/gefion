import json

from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


def test_features_compute_starts_at_max_workers(monkeypatch):
    # Stub DB and dependencies
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return []

        def fetchone(self):
            return (1,)

    class FakeConn:
        def __init__(self):
            self.autocommit = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli.psycopg, "connect", lambda *a, **kw: FakeConn())
    monkeypatch.setattr(cli.schema, "create_feature_definitions_table", lambda conn: None)
    monkeypatch.setattr(cli.schema, "create_computed_features_table", lambda conn: None)
    monkeypatch.setattr(cli, "get_available_connections", lambda url: (20,))

    # Capture AdaptiveLimiter start value
    captured = {}

    class FakeLimiter:
        def __init__(self, start_workers, max_workers):
            captured["start"] = start_workers
            captured["max"] = max_workers
            self.current = start_workers

        def value(self):
            return self.current

        def record_batch(self, errors):
            return self.current

    monkeypatch.setattr(cli, "AdaptiveLimiter", FakeLimiter)

    def fake_compute(
        conn,
        data_id,
        function_names=None,
        feature_names=None,
        incremental=True,
        full_refresh=False,
        update_existing=False,
        feature_batch_size=2000,
    ):
        captured["feature_names"] = feature_names
        captured["data_id"] = data_id
        captured["feature_batch_size"] = feature_batch_size
        return {"summary": {"total_inserted": 0, "total_errors": 0}}

    # Patch dispatcher compute_features used inside CLI
    monkeypatch.setattr("g2.features.dispatcher.compute_features", fake_compute)

    res = runner.invoke(
        cli.app,
        ["features-compute", "--symbols", "AAA", "--features", "feat1", "--max-workers", "5", "--batch-size", "3000", "--json"],
    )

    assert res.exit_code == 0, res.stdout
    # JSON output might be a single payload line
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert payload.get("success") is True
    # Start should be a fraction of max (half-rounded-up, min 2)
    assert captured["start"] == 3
    assert captured["max"] == 5
    assert captured["feature_names"] == ["feat1"]
    assert captured["feature_batch_size"] == 3000
