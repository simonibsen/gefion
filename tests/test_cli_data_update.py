import json
import os
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from g2 import cli
from g2.db import schema

runner = CliRunner()
DB_TESTS_ENABLED = os.getenv("ENABLE_DB_TESTS", "0") == "1"


def require_db():
    if not DB_TESTS_ENABLED:
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError:
        pytest.skip("DB not available")
    return conn


@pytest.fixture(autouse=True)
def clean_db(monkeypatch, tmp_path):
    conn = require_db()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.close()
    monkeypatch.setenv("DATABASE_URL", schema.test_db_url())
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "demo")
    yield


def test_data_update_infers_symbols(monkeypatch):
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol) VALUES ('AAA'),('BBB');")
    conn.close()

    called = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    def dummy_price_ingest(**kwargs):
        called["prices"] = kwargs
        return 2

    def dummy_feature_ingest(**kwargs):
        called["features"] = kwargs
        return 3

    monkeypatch.setattr(cli, "AlphaVantageClient", DummyClient)
    monkeypatch.setattr(cli, "ingest_prices_for_symbols", dummy_price_ingest)
    monkeypatch.setattr(cli, "ingest_indicators_for_symbols", dummy_feature_ingest)

    res = runner.invoke(cli.app, ["data-update", "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    assert "AAA" in called["prices"]["symbols"]
    assert "BBB" in called["features"]["symbols"]


def test_data_update_no_symbols_errors(monkeypatch, tmp_path):
    # No stocks, empty listings file -> should error
    listings_file = tmp_path / "listings.json"
    listings_file.write_text(json.dumps({"data": []}))

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(cli, "AlphaVantageClient", DummyClient)
    monkeypatch.setattr(cli, "ingest_prices_for_symbols", lambda **kwargs: 0)
    monkeypatch.setattr(cli, "ingest_indicators_for_symbols", lambda **kwargs: 0)

    res = runner.invoke(cli.app, ["data-update", "--json", "--listings-file", str(listings_file)])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["status"] == "error"
    assert "nothing to ingest" in payload["message"].lower()
