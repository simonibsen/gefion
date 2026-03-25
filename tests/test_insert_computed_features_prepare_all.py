from datetime import date

from gefion.db.ingest import insert_computed_features
from gefion.db import pool as db_pool


def test_insert_always_prepares_when_enabled(monkeypatch):
    called = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, stmt, params=None, prepare=False):
            # Only track prepare flags for INSERT statements, not SET/RESET
            stmt_str = str(stmt).strip().upper()
            if stmt_str.startswith("INSERT"):
                called.setdefault("prepare_flags", []).append(prepare)

    class FakeConn:
        def __init__(self):
            self.autocommit = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            called["committed"] = True

    monkeypatch.setattr(db_pool, "should_prepare_statements", lambda: True)

    rows = [{"date": date(2025, 1, 1), "col": 1.0}, {"date": date(2025, 1, 2), "col": 2.0}]
    inserted = insert_computed_features(FakeConn(), data_id=1, rows=rows, feature_map={"col": 10}, batch_size=300)

    assert inserted == 2
    assert all(called.get("prepare_flags", []))
