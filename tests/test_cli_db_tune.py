import json
from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


class FakeCursor:
    def __init__(self):
        self.statements = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        sql_text = str(sql).strip()
        self.statements.append((sql_text, params))
        # simulate table existence checks and compression settings
        if "to_regclass" in sql_text:
            # pretend both tables exist
            self.rows = [("public.stock_ohlcv",)]
        elif "timescaledb_information.compression_settings" in sql_text:
            # pretend compression not set yet
            self.rows = []
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, cur):
        self.cur = cur
        self.autocommit = False

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_db_tune_runs_and_reports(monkeypatch):
    cur = FakeCursor()

    def fake_connect(url):
        return FakeConn(cur)

    monkeypatch.setattr(cli.psycopg, "connect", fake_connect)

    res = runner.invoke(cli.app, ["db-tune", "--json", "--chunk-days", "30", "--compress-after-days", "60"])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    # verify chunk interval and compression policy statements were attempted
    joined = "\n".join(sql for sql, _ in cur.statements)
    assert "set_chunk_time_interval" in joined
    assert "add_compression_policy" in joined
    assert "table_status" in payload
