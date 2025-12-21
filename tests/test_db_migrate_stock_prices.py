import psycopg

from g2.db import migrate


class FakeCursor:
    def __init__(self, has_legacy=True):
        self.has_legacy = has_legacy
        self.rowcount = 0
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        q = query.strip().lower()
        self.executed.append(q)
        if "select exists" in q and "stock_prices" in q:
            self._rows = [(self.has_legacy,)]
        elif "insert into stock_ohlcv" in q:
            self.rowcount = 10
        else:
            self.rowcount = 0
        self._rows = getattr(self, "_rows", [(None,)])

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, has_legacy=True):
        self.cur = FakeCursor(has_legacy=has_legacy)
        self.autocommit = False

    def cursor(self):
        return self.cur

    def commit(self):
        return None


def test_migrate_stock_prices_copies_when_present(monkeypatch):
    conn = FakeConn(has_legacy=True)

    def fake_create(_conn):
        return None

    monkeypatch.setattr("g2.db.schema.create_stock_ohlcv_table", fake_create)

    copied, dropped = migrate.migrate_stock_prices_to_ohlcv(conn, drop_old=False)

    assert copied == 10
    assert dropped == 0
    assert any("insert into stock_ohlcv" in q for q in conn.cur.executed)


def test_migrate_stock_prices_noop_when_missing(monkeypatch):
    conn = FakeConn(has_legacy=False)

    monkeypatch.setattr("g2.db.schema.create_stock_ohlcv_table", lambda _c: None)

    copied, dropped = migrate.migrate_stock_prices_to_ohlcv(conn, drop_old=True)

    assert copied == 0
    assert dropped == 0
    assert any("select exists" in q for q in conn.cur.executed)
