from datetime import date

from g2.db.ingest import insert_computed_features


def test_insert_computed_features_copy_uses_copy_expert(monkeypatch):
    called = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def copy_expert(self, sql, file):
            called["sql"] = sql
            called["content"] = file.read()

        def execute(self, stmt, params=None, prepare=False):
            called["execute_fallback"] = True

    class FakeConn:
        def __init__(self):
            self.autocommit = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            called["committed"] = True

    rows = [
        {"date": date(2025, 1, 1), "col": float(i), "source": "fx"} for i in range(500)
    ]

    inserted = insert_computed_features(
        FakeConn(), data_id=5, rows=rows, feature_map={"col": 10}, use_copy=True
    )

    assert inserted == 500
    assert "COPY computed_features" in called["sql"]
    # Ensure all lines were written
    assert called["content"].count("\n") == 500
    assert "10\t5\t2025-01-01" in called["content"]


def test_insert_computed_features_copy_skips_when_update_existing(monkeypatch):
    called = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, stmt, params=None, prepare=False):
            called["executed"] = True

    class FakeConn:
        def __init__(self):
            self.autocommit = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            called["committed"] = True

    rows = [{"date": date(2025, 1, 1), "col": 1.0}]

    inserted = insert_computed_features(
        FakeConn(), data_id=5, rows=rows, feature_map={"col": 10}, use_copy=True, update_existing=True
    )

    assert inserted == 1
    assert called.get("executed") is True
