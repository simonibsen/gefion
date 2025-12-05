from g2.db import migrate


class FakeCursor:
    def __init__(self):
        self.rowcount = 0
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.executed.append(query.strip())
        # Pretend a few rows were updated
        self.rowcount = 3


class FakeConn:
    def __init__(self):
        self.cur = FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True


def test_migrate_feature_definitions_source_table_updates_rows():
    conn = FakeConn()
    updated = migrate.migrate_feature_definitions_source_table(conn)

    assert updated == 3
    assert conn.committed is True
    assert any("UPDATE feature_definitions" in q for q in conn.cur.executed)
