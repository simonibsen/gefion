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


def test_features_run_batch_size_passed(monkeypatch):
    # Minimal feature definition and stock so feature resolution passes
    conn = require_db()
    conn.autocommit = True
    with conn.cursor() as cur:
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
            VALUES ('indicator_adx_14', 'indicator', '{"indicator":"adx"}', 'computed_features', 'value', true);
            """
        )
    conn.close()

    captured = {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    def dummy_fetch_listings(client):
        return [{"symbol": "AAA", "exchange": "nasdaq", "status": "Active"}]

    def dummy_ingest(**kwargs):
        captured["batch_size"] = kwargs.get("batch_size")
        captured["symbols"] = kwargs.get("symbols")
        return 0

    monkeypatch.setattr(cli, "AlphaVantageClient", DummyClient)
    monkeypatch.setattr(cli, "fetch_listings", dummy_fetch_listings)
    monkeypatch.setattr(cli, "ingest_indicators_for_symbols", dummy_ingest)

    res = runner.invoke(
        cli.app,
        [
            "features-run",
            "--features",
            "indicator_adx_14",
            "--exchange",
            "nasdaq",
            "--batch-size",
            "50",
            "--json",
        ],
    )
    assert res.exit_code == 0
    assert captured["batch_size"] == 50
    assert captured["symbols"] == ["AAA"]
    payload = json.loads([ln for ln in res.stdout.splitlines() if ln.strip()][-1])
    assert payload["status"] == "ok"
