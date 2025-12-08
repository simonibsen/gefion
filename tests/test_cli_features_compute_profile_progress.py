import json

from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


def test_profile_includes_latest_symbol_timing_in_progress(monkeypatch):
    calls = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            pass

        def fetchone(self):
            return (1,)

        def fetchall(self):
            return [("feat1",)]

    class FakeConn:
        def __init__(self):
            self.autocommit = False

        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class DummyPool:
        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(cli.db_pool, "get_pool", lambda: None)

    def fake_init_pool(url, min_size=2, max_size=10, timeout=30.0, prepare_statements=True):
        dummy = DummyPool()
        dummy._g2_prepare_statements = True
        cli.db_pool._pool = dummy
        return dummy

    monkeypatch.setattr(cli.db_pool, "init_pool", fake_init_pool)
    monkeypatch.setattr(cli.db_pool, "get_connection", lambda: FakeConn())
    monkeypatch.setattr(cli.psycopg, "connect", lambda *a, **kw: FakeConn())
    monkeypatch.setattr(cli.schema, "create_feature_definitions_table", lambda conn: None)
    monkeypatch.setattr(cli.schema, "create_computed_features_table", lambda conn: None)
    monkeypatch.setattr(cli, "get_available_connections", lambda url: (10,))

    def fake_compute(
        conn,
        data_id,
        function_names=None,
        feature_names=None,
        incremental=True,
        full_refresh=False,
        update_existing=False,
        feature_batch_size=2000,
        writer_workers=2,
        profile=False,
    ):
        return {
            "summary": {
                "total_inserted": 1,
                "total_errors": 0,
                "timing": {"fetch": 0.1, "compute": 0.2, "write": 0.3},
            }
        }

    monkeypatch.setattr("g2.features.dispatcher.compute_features", fake_compute)

    res = runner.invoke(
        cli.app,
        ["features-compute", "--symbols", "AAA", "--features", "feat1", "--json", "--profile"],
    )
    assert res.exit_code == 0, res.stdout
    lines = res.stdout.strip().splitlines()
    # First line is progress, last line is final payload
    progress = json.loads(lines[0])
    payload = json.loads(lines[-1])
    assert progress["timing"]["compute"] == 0.2
    assert payload["profiles"][0]["timing"]["compute"] == 0.2
