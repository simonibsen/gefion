import json

from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


def test_features_compute_uses_pool(monkeypatch):
    calls = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            calls["query"] = query
            calls["params"] = params

        def fetchone(self):
            return (1,)

        def fetchall(self):
            return [("feat1",)]

    class FakeConn:
        def __init__(self):
            self.autocommit = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class DummyPool:
        def close(self):
            calls["closed"] = True

    def fake_init_pool(url, min_size=2, max_size=10, timeout=30.0, prepare_statements=True):
        calls["init_pool"] = {
            "url": url,
            "min_size": min_size,
            "max_size": max_size,
            "prepare": prepare_statements,
        }
        dummy = DummyPool()
        dummy._g2_prepare_statements = True
        cli.db_pool._pool = dummy
        return dummy

    def fake_get_pool():
        return None

    monkeypatch.setattr(cli.db_pool, "get_pool", fake_get_pool)
    monkeypatch.setattr(cli.db_pool, "init_pool", fake_init_pool)

    def fake_get_connection():
        calls["used_pool"] = True
        conn = FakeConn()
        return conn

    monkeypatch.setattr(cli.db_pool, "get_connection", fake_get_connection)
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
        sync_commit=None,
    ):
        calls["compute_features"] = {
            "data_id": data_id,
            "feature_names": feature_names,
            "batch": feature_batch_size,
            "writer_workers": writer_workers,
        }
        return {"summary": {"total_inserted": 0, "total_errors": 0}}

    monkeypatch.setattr("g2.cli.compute_features", fake_compute)

    res = runner.invoke(
        cli.app,
        ["feat-compute", "--symbols", "AAA", "--features", "feat1", "--json"],
    )

    # Test verifies that feat-compute uses connection pool
    # Core assertions: pool was initialized and used
    assert calls.get("used_pool") is True, "Connection pool should be used"
    assert "init_pool" in calls, "Pool should be initialized"
    assert calls["init_pool"]["prepare"] is True, "Prepared statements should be enabled"
    assert calls["init_pool"]["min_size"] >= 2, "Pool should have minimum size"
    assert "closed" in calls, "Pool should be closed after use"
