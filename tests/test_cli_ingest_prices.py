import os
from pathlib import Path

import pytest
import psycopg
from typer.testing import CliRunner

from g2 import cli
from g2.db import schema

runner = CliRunner()
fixture_path = Path(__file__).parent / "fixtures" / "demo_time_series_daily_adjusted.json"


def require_db():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        conn = psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError:
        pytest.skip("DB not available")
    return conn


@pytest.fixture(autouse=True)
def clean_db():
    conn = require_db()
    conn.autocommit = True
    with conn.cursor() as cur:
        # Drop tables but keep extension intact
        cur.execute("""
            DO $$ DECLARE
                r RECORD;
            BEGIN
                FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                    EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                END LOOP;
            END $$;
        """)
        # Ensure TimescaleDB extension is available
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except psycopg.errors.DuplicateObject:
            pass
    conn.close()
    yield


def test_cli_ingest_prices_inserts_rows():
    env = {"DATABASE_URL": schema.test_db_url()}
    result = runner.invoke(
        cli.app,
        [
            "prices-ingest",
            "--symbol",
            "IBM",
            "--input",
            str(fixture_path),
        ],
        env=env,
    )

    assert result.exit_code == 0, result.stdout

    conn = require_db()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM stock_ohlcv;")
        count = cur.fetchone()[0]
    conn.close()
    assert count > 0
