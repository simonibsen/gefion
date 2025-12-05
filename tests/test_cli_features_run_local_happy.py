import json
import os
from pathlib import Path
from datetime import date

import psycopg
import pytest
from typer.testing import CliRunner

from g2 import cli
from g2.db import schema
from g2.db.ingest import ensure_all_indicator_feature_definitions

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


def test_features_run_local_succeeds(monkeypatch, tmp_path):
    conn = require_db()
    conn.autocommit = True
    schema.create_stocks_table(conn)
    schema.create_stock_ohlcv_table(conn)
    schema.create_feature_definitions_table(conn)
    schema.create_computed_features_table(conn)
    ensure_all_indicator_feature_definitions(conn, indicators=["adx"])
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stocks (symbol) VALUES ('AAA') RETURNING id;")
        stock_id = cur.fetchone()[0]
        # Minimal price history with high/low/close
        rows = []
        for i in range(1, 20):
            rows.append(
                (
                    stock_id,
                    date(2025, 1, i),
                    10.0 + i,
                    11.0 + i,
                    9.0 + i,
                    10.5 + i,
                    10.5 + i,
                    1000 + i,
                    "test",
                )
            )
        args = []
        for r in rows:
            args.extend(r)
        placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(rows))
        cur.execute(
            f"INSERT INTO stock_ohlcv (data_id, date, open, high, low, close, adjusted_close, volume, source) VALUES {placeholders}",
            args,
        )
    conn.close()

    # listings file for offline symbol resolution
    listings_file = tmp_path / "listings.json"
    listings_file.write_text(
        json.dumps({"data": [{"symbol": "AAA", "status": "Active", "exchange": "nasdaq"}]})
    )

    res = runner.invoke(
        cli.app,
        [
            "features-run",
            "--features",
            "indicator_adx_14",
            "--listings-file",
            str(listings_file),
            "--json",
        ],
    )
    assert res.exit_code == 0
    # take last JSON line
    payload = json.loads([ln for ln in res.stdout.splitlines() if ln.strip()][-1])
    assert payload["status"] == "ok"
    assert payload.get("inserted", 0) > 0
