import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from g2 import cli

runner = CliRunner()


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return (None,)

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self):
        self.autocommit = True

    def cursor(self):
        return FakeCursor()

    def commit(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def stub_psycopg(monkeypatch):
    # Avoid real database connections in CLI tests
    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: FakeConn())


def _listings_file(tmp_path: Path) -> Path:
    payload = {
        "data": [
            {"symbol": "AAA", "exchange": "NASDAQ", "status": "Active"},
            {"symbol": "BBB", "exchange": "NASDAQ", "status": "Active"},
        ]
    }
    path = tmp_path / "listings.json"
    path.write_text(json.dumps(payload))
    return path


def test_data_update_uses_planned_workers(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "demo")

    listings_file = _listings_file(tmp_path)
    calls = {"plan": []}

    def fake_get_available(url):
        calls["avail_url"] = url
        return (8, 10, 2)

    def fake_plan(avail, req_fetch, req_writer, default_fetch, default_writer, reserve=2):
        calls["plan"].append((avail, req_fetch, req_writer, default_fetch, default_writer, reserve))
        return (3, 2) if len(calls["plan"]) == 1 else (4, 1)

    def fake_price_ingest(**kwargs):
        calls["prices"] = kwargs
        return 10

    def fake_indicator_ingest(**kwargs):
        calls["indicators"] = kwargs
        return 20

    monkeypatch.setattr(cli, "get_available_connections", fake_get_available)
    monkeypatch.setattr(cli, "plan_workers", fake_plan)
    monkeypatch.setattr(cli, "ingest_prices_for_symbols", fake_price_ingest)
    monkeypatch.setattr(cli, "ingest_indicators_for_symbols", fake_indicator_ingest)
    monkeypatch.setattr(cli, "AlphaVantageClient", lambda *a, **kw: object())

    res = runner.invoke(
        cli.app,
        ["data-update", "--json", "--listings-file", str(listings_file)],
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"

    # First planner call drives price ingest, second drives indicators
    assert calls["plan"][0][0] == 8
    assert calls["plan"][1][0] == 8
    assert calls["prices"]["max_workers"] == 3
    assert calls["prices"]["writer_workers"] == 2
    assert calls["indicators"]["fetch_workers"] == 4
    assert calls["indicators"]["writer_workers"] == 1
    assert payload["price_fetch_workers"] == 3
    assert payload["price_writer_workers"] == 2
    assert payload["feature_fetch_workers"] == 4
    assert payload["feature_writer_workers"] == 1


def test_universe_ingest_uses_planned_workers(monkeypatch, tmp_path):
    db_url = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:6432/testdb")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "demo")

    listings_file = _listings_file(tmp_path)
    calls = {"plan": None}

    def fake_get_available(url):
        calls["avail_url"] = url
        return (6, 12, 6)

    def fake_plan(avail, req_fetch, req_writer, default_fetch, default_writer, reserve=2):
        calls["plan"] = (avail, req_fetch, req_writer, default_fetch, default_writer, reserve)
        return (5, 2)

    def fake_price_ingest(**kwargs):
        calls["prices"] = kwargs
        return 5

    monkeypatch.setattr(cli, "get_available_connections", fake_get_available)
    monkeypatch.setattr(cli, "plan_workers", fake_plan)
    monkeypatch.setattr(cli, "ingest_prices_for_symbols", fake_price_ingest)
    monkeypatch.setattr("g2.ingest.universe.filter_symbols_needing_update", lambda conn, syms, target: list(syms))
    monkeypatch.setattr(cli, "AlphaVantageClient", lambda *a, **kw: object())

    res = runner.invoke(
        cli.app,
        [
            "universe-ingest",
            "--exchange",
            "nasdaq",
            "--json",
            "--listings-file",
            str(listings_file),
        ],
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"

    assert calls["plan"][0] == 6
    assert calls["prices"]["max_workers"] == 5
    assert calls["prices"]["writer_workers"] == 2
    assert payload["fetch_workers"] == 5
    assert payload["writer_workers"] == 2
