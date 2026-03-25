import os

import pytest

from gefion.db.ingest import insert_computed_features
from gefion.db import pool as db_pool


def test_insert_computed_features_uses_prepared_for_large_batches(monkeypatch):
    called = {}
    all_calls = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, stmt, params=None, prepare=False):
            all_calls.append({"stmt": stmt, "prepare": prepare, "params_len": len(params or [])})
            # Capture the INSERT statement specifically
            if "INSERT" in stmt:
                called["prepare"] = prepare
                called["stmt"] = stmt
                called["params_len"] = len(params or [])

    class FakeConn:
        def __init__(self):
            self.autocommit = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            called["committed"] = True

    monkeypatch.setattr(db_pool, "should_prepare_statements", lambda: True)

    rows = [{"date": "2025-01-01", "col": 1.0} for _ in range(500)]
    inserted = insert_computed_features(FakeConn(), data_id=1, rows=rows, feature_map={"col": 1}, batch_size=500)

    assert inserted == 500
    assert called.get("prepare") is True
    # params should be feature_id,data_id,date,value,source per row
    assert called.get("params_len") == 500 * 5
    assert "committed" in called


def test_insert_computed_features_skips_prepared_when_disabled(monkeypatch):
    called = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, stmt, params=None, prepare=False):
            called["prepare"] = prepare

    class FakeConn:
        def __init__(self):
            self.autocommit = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            called["committed"] = True

    monkeypatch.setattr(db_pool, "should_prepare_statements", lambda: False)

    rows = [{"date": "2025-01-01", "col": 1.0} for _ in range(500)]
    inserted = insert_computed_features(FakeConn(), data_id=1, rows=rows, feature_map={"col": 1}, batch_size=500)

    assert inserted == 500
    assert called.get("prepare") is False
    assert "committed" in called
