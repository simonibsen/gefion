import time
from datetime import date

from g2.features import dispatcher
from g2.db import pool as db_pool


def test_compute_features_timings_include_queue_and_writer(monkeypatch):
    # Mock connection
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

    fake_conn = FakeConn()

    monkeypatch.setattr(dispatcher, "_fetch_feature_definitions", lambda conn, function_names=None, feature_names=None: [
        (1, "feat1", "indicator", {}, "stock_ohlcv", "close", "computed_features", "value")
    ])
    monkeypatch.setattr(dispatcher, "_group_by_function_name", lambda defs: {"indicator": defs})
    monkeypatch.setattr(dispatcher, "_latest_dates_for_features", lambda conn, data_id, feature_ids: {})
    monkeypatch.setattr(dispatcher, "_fetch_source_data", lambda conn, data_id, source_key, features, start_date=None: [
        {"date": date(2025, 1, 1), "feat1": 1.0}
    ])

    def fake_compute(rows, specs):
        return rows

    monkeypatch.setattr(dispatcher, "_resolve_compute_function", lambda conn, fn: fake_compute)

    def fake_insert(conn, data_id, rows, feature_map, update_existing=False, batch_size=2000, sync_commit=False):
        time.sleep(0.01)
        return len(rows)

    monkeypatch.setattr(dispatcher, "insert_computed_features", fake_insert)

    # Mock connection pool
    def fake_get_connection():
        return FakeConn()

    def fake_should_prepare_statements():
        return False

    monkeypatch.setattr(db_pool, "get_connection", fake_get_connection)
    monkeypatch.setattr(db_pool, "should_prepare_statements", fake_should_prepare_statements)

    res = dispatcher.compute_features(
        conn=fake_conn,
        data_id=1,
        profile=True,
        writer_workers=1,
    )

    timing = res["summary"]["timing"]
    assert timing["writer"] >= 0.01
    assert timing["queue_wait"] >= 0
    assert timing["writer_wait"] > 0
