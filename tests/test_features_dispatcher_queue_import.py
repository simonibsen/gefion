from gefion.features import dispatcher
from gefion.db import pool as db_pool


def test_compute_features_with_writer_queue_uses_queue(monkeypatch):
    # stub out DB-dependent helpers
    monkeypatch.setattr(dispatcher, "_fetch_feature_definitions", lambda conn, function_names=None, feature_names=None: [
        (1, "feat1", "indicator", {}, "stock_ohlcv", "close", "computed_features", "value")
    ])
    monkeypatch.setattr(dispatcher, "_group_by_function_name", lambda defs: {"indicator": defs})
    monkeypatch.setattr(dispatcher, "_latest_dates_for_features", lambda conn, data_id, feature_ids: {})
    monkeypatch.setattr(dispatcher, "_fetch_source_data", lambda conn, data_id, source_key, features, start_date=None: [
        {"date": "2025-01-01", "feat1": 1.0}
    ])
    monkeypatch.setattr(dispatcher, "_resolve_compute_function", lambda conn, fn: lambda rows, specs: rows)

    calls = {}

    def fake_insert(conn, data_id, rows, feature_map, update_existing=False, batch_size=2000, sync_commit=False):
        calls["insert"] = True
        return len(rows)

    monkeypatch.setattr(dispatcher, "insert_computed_features", fake_insert)

    class FakeConn:
        def __init__(self):
            self.autocommit = False
        def cursor(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, *args):
            pass
        def fetchone(self):
            return None
        def fetchall(self):
            return []
        def close(self):
            pass
        def commit(self):
            pass

    # Mock connection pool
    def fake_get_connection():
        return FakeConn()

    def fake_should_prepare_statements():
        return False

    monkeypatch.setattr(db_pool, "get_connection", fake_get_connection)
    monkeypatch.setattr(db_pool, "should_prepare_statements", fake_should_prepare_statements)

    result = dispatcher.compute_features(
        FakeConn(),
        data_id=1,
        incremental=True,
        writer_workers=1,
    )

    assert result["summary"]["total_inserted"] == 1
    assert calls.get("insert") is True
