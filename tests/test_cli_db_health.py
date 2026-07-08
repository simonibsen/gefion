import json
from typer.testing import CliRunner

from gefion import cli

runner = CliRunner()


class FakeCursor:
    def __init__(self, seq):
        self.seq = seq
        self.calls = 0
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        # Serve canned responses in order
        self.calls += 1
        if "to_regclass" in query:
            # Return something exists
            self._rows = [("public.stock_ohlcv",)]
        elif "timescaledb_information.hypertables" in query:
            self._rows = [("stock_ohlcv", 2592000000000)]  # 30 days in microseconds
        elif "pg_indexes" in query:
            self._rows = [("stock_ohlcv", "CREATE INDEX stock_ohlcv_brin ON stock_ohlcv USING BRIN (date)")]
        elif "count(sector)" in query:
            # 100 stocks: sector/industry empty, asset_type full — the silent
            # prod gap this check exists to surface
            self._rows = [(100, 0, 0, 100)]
        elif "stocks_fundamentals" in query:
            self._rows = [(None,)]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_db_health_reports(monkeypatch):
    cur = FakeCursor([])

    def fake_connect(url):
        return FakeConn(cur)

    monkeypatch.setattr(cli.psycopg, "connect", fake_connect)
    monkeypatch.setattr(cli, "get_available_connections", lambda url: (5, 10, 5))

    res = runner.invoke(cli.app, ["db-health", "--json"])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    assert payload["available_connections"] == 5
    assert payload["tables"]["stock_ohlcv"] is True
    assert payload["brin_indexes"]["stock_ohlcv"] is True


def test_db_health_surfaces_dimension_coverage_gaps(monkeypatch):
    """The silent-metadata problem (prod ran for weeks with sector/industry/
    asset_type entirely NULL): db-health must report coverage and name the
    command that fixes each gap."""
    cur = FakeCursor([])
    monkeypatch.setattr(cli.psycopg, "connect", lambda url: FakeConn(cur))
    monkeypatch.setattr(cli, "get_available_connections", lambda url: (5, 10, 5))

    res = runner.invoke(cli.app, ["db-health", "--json"])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    cov = payload["dimension_coverage"]
    assert cov["stocks_total"] == 100
    assert cov["sector_pct"] == 0.0
    assert cov["industry_pct"] == 0.0
    assert cov["asset_type_pct"] == 100.0
    assert cov["fundamentals_latest"] is None
    # actionable: the warnings name the fixing command
    warnings = " ".join(payload["warnings"])
    assert "fundamentals-update" in warnings
    assert "listing-meta" not in warnings  # asset_type is fine in this fixture
