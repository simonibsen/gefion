import os
import json
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
    yield


def test_features_list_and_show():
    conn = require_db()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_definitions (
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
            INSERT INTO feature_definitions (name, function_name, params, store_table, store_column)
            VALUES ('test_feature', 'indicator', '{"indicator":"rsi"}', 'computed_features', 'value');
            """
        )
    conn.close()

    res_list = runner.invoke(cli.app, ["feat-def-list", "--json"])
    assert res_list.exit_code == 0
    payload = json.loads(res_list.stdout)
    assert payload["status"] == "ok"
    assert any(f["name"] == "test_feature" for f in payload["features"])

    # non-json list should succeed and show table
    res_list_txt = runner.invoke(cli.app, ["feat-def-list"])
    assert res_list_txt.exit_code == 0
    # Table output may truncate names, just verify command succeeds
    assert "Features" in res_list_txt.stdout or "indicator" in res_list_txt.stdout

    res_show = runner.invoke(cli.app, ["feat-def-show", "--feature", "test_feature", "--json"])
    assert res_show.exit_code == 0
    payload = json.loads(res_show.stdout)
    assert payload["status"] == "ok"
    assert payload["name"] == "test_feature"
