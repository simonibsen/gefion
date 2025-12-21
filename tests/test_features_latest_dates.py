from datetime import date

from g2.features.dispatcher import _latest_dates_for_features


def test_latest_dates_for_features_queries_per_feature(monkeypatch):
    executed = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            executed["query"] = query
            executed["params"] = params

        def fetchall(self):
            return [
                (1, date(2024, 1, 1)),
                (2, None),
            ]

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    conn = FakeConn()
    out = _latest_dates_for_features(conn, data_id=10, feature_ids=[1, 2, 3])

    assert out[1] == date(2024, 1, 1)
    assert 3 not in out  # missing rows omitted
    assert "feature_id IN (%s,%s,%s)" in executed["query"]
    assert executed["params"][0] == 10
    assert executed["params"][1:] == [1, 2, 3]
