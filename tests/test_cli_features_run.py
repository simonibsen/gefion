import json
import os

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
def clean_db(monkeypatch):
    conn = require_db()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.close()
    monkeypatch.setenv("DATABASE_URL", schema.test_db_url())
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "demo")
    yield


def test_features_run_infers_symbols_and_runs(monkeypatch):
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol) VALUES ('AAA'),('BBB');")
        cur.execute(
            """
            CREATE TABLE feature_definitions (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                function_name TEXT NOT NULL,
                params JSONB,
                source_table TEXT,
                source_column TEXT,
                store_table TEXT DEFAULT 'computed_features',
                store_column TEXT,
                store_type TEXT DEFAULT 'double precision',
                active BOOLEAN DEFAULT TRUE,
                version TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            INSERT INTO feature_definitions (name, function_name, params, store_table, store_column, active)
            VALUES ('indicator_rsi_14', 'indicator', '{"indicator":"rsi"}', 'computed_features', 'value', true);
            """
        )
    conn.close()

    called = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    def dummy_ingest(**kwargs):
        called["symbols"] = kwargs["symbols"]
        return 1

    monkeypatch.setattr(cli, "AlphaVantageClient", DummyClient)
    monkeypatch.setattr(cli, "ingest_indicators_for_symbols", dummy_ingest)
    # no listings fetch needed; symbols from DB

    res = runner.invoke(cli.app, ["features-run", "--features", "indicator_rsi_14", "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    assert set(called["symbols"]) == {"AAA", "BBB"}


def test_features_run_requires_feature_names(monkeypatch):
    # No features and --all-features not provided -> error
    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(cli, "AlphaVantageClient", DummyClient)
    res = runner.invoke(cli.app, ["features-run", "--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["status"] == "error"
    assert "provide --features" in payload["message"].lower()
