import warnings

from g2.features import dispatcher


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.exec_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        self.exec_calls += 1

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class FakeConn:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj


def test_db_function_overrides_code(monkeypatch):
    # Arrange globals
    original = dispatcher.COMPUTE_FUNCTIONS.get("foo")
    dispatcher.COMPUTE_FUNCTIONS["foo"] = lambda *a, **k: "code"
    dispatcher._FUNCTION_CACHE.clear()
    dispatcher._FUNCTION_CACHE_SOURCE.clear()
    rows = [("python_expr", "def compute(*args, **kwargs):\n    return 'db'", "1.0.0")]
    conn = FakeConn(rows)

    with warnings.catch_warnings(record=True) as caught:
        fn = dispatcher._resolve_compute_function(conn, "foo")

    try:
        assert fn([], [{"name": "foo", "feature_id": 1}]) == "db"
        assert any("overriding" in str(w.message) for w in caught)
        # Cached: no additional DB calls on second resolve
        fn2 = dispatcher._resolve_compute_function(conn, "foo")
        assert fn2 is fn
        assert conn.cursor_obj.exec_calls == 1
    finally:
        # Cleanup
        dispatcher._FUNCTION_CACHE.clear()
        dispatcher._FUNCTION_CACHE_SOURCE.clear()
        if original is None:
            dispatcher.COMPUTE_FUNCTIONS.pop("foo", None)
        else:
            dispatcher.COMPUTE_FUNCTIONS["foo"] = original
