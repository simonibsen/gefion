from datetime import date

from g2.features.dispatcher import _latest_dates_for_features


def test_latest_dates_for_features_returns_correct_dict(monkeypatch):
    """Verify function returns {feature_id: date} dict from query results."""
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
                (3, None),
            ]

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    conn = FakeConn()
    out = _latest_dates_for_features(conn, data_id=10, feature_ids=[1, 2, 3])

    assert out[1] == date(2024, 1, 1)
    assert out[2] is None
    assert out[3] is None


def test_latest_dates_uses_lateral_join(monkeypatch):
    """Verify the query uses LATERAL join for chunk-efficient lookups."""
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
            return [(1, date(2024, 1, 1))]

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    conn = FakeConn()
    _latest_dates_for_features(conn, data_id=10, feature_ids=[1, 2, 3])

    query = executed["query"].upper()
    assert "LATERAL" in query, "Query should use LATERAL join for chunk-efficient lookups"
    assert "UNNEST" in query, "Query should use unnest(ARRAY[...]) to feed feature IDs"
    assert "LIMIT 1" in query, "Query should LIMIT 1 per feature for index skip-scan"
    assert "ORDER BY" in query, "Query should ORDER BY date DESC for latest"
    # Params: feature_ids first, then data_id
    assert executed["params"] == [1, 2, 3, 10]
