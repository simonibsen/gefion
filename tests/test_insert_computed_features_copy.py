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

    class FakeConn:
        def __init__(self):
            self.autocommit = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            called["committed"] = True

    rows = [
        {"date": date(2025, 1, 1), "col": 1.0, "source": "fx"},
        {"date": date(2025, 1, 2), "col": 2.0, "source": "fx"},
    ]

    inserted = insert_computed_features(
        FakeConn(), data_id=5, rows=rows, feature_map={"col": 10}, use_copy=True
    )

    assert inserted == 2
    assert "COPY computed_features" in called["sql"]
    # Ensure both lines were written
    assert called["content"].count("\n") == 2
    assert "10\t5\t2025-01-01" in called["content"]
