"""Listing-metadata backfill tests (T047 follow-up, learning 2).

TDD: written FIRST. The AlphaVantage LISTING_STATUS payload has always
carried exchange/assetType, and parse_listing_status extracts them — but the
ingest dropped them, leaving stocks.asset_type NULL on prod, which (correctly)
blocks discovery's default quality universe chain. `update_listing_metadata`
writes them back; `gefion data listing-meta` is the operator door.
"""
import os

import psycopg
import pytest
from typer.testing import CliRunner

from gefion.cli import app
from gefion.db import schema
from gefion.ingest.universe import update_listing_metadata

runner = CliRunner()


def _conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        c = psycopg.connect(schema.test_db_url())
        c.autocommit = True
        return c
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    c = _conn()
    with c.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'LMETA%'")
        cur.execute(
            """INSERT INTO stocks (symbol, name) VALUES
               ('LMETA1', NULL),
               ('LMETA2', 'Existing Rich Name Inc'),
               ('LMETA3', NULL)"""
        )
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'LMETA%'")
    c.close()


LISTINGS = [
    {"symbol": "LMETA1", "name": "Alpha Co", "exchange": "NASDAQ",
     "asset_type": "Common Stock", "status": "Active"},
    {"symbol": "LMETA2", "name": "Beta Fund", "exchange": "NYSE",
     "asset_type": "ETF", "status": "Active"},
    {"symbol": "LMETAX", "name": "Unknown Co", "exchange": "NASDAQ",
     "asset_type": "Common Stock", "status": "Active"},
]


def test_update_listing_metadata_fills_exchange_and_asset_type(conn):
    counts = update_listing_metadata(conn, LISTINGS)
    assert counts == {"updated": 2, "skipped_unknown": 1}
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, name, exchange, asset_type FROM stocks "
                    "WHERE symbol LIKE 'LMETA%' ORDER BY symbol")
        rows = {r[0]: r[1:] for r in cur.fetchall()}
    assert rows["LMETA1"] == ("Alpha Co", "NASDAQ", "Common Stock")
    # exchange/asset_type are authoritative; an existing name is not clobbered
    assert rows["LMETA2"] == ("Existing Rich Name Inc", "NYSE", "ETF")
    # symbols absent from the listing are untouched
    assert rows["LMETA3"] == (None, None, None)


def test_update_listing_metadata_is_idempotent(conn):
    update_listing_metadata(conn, LISTINGS)
    counts = update_listing_metadata(conn, LISTINGS)
    assert counts["updated"] == 2


def test_cli_listing_meta_from_file(conn, tmp_path):
    csv_path = tmp_path / "listing.csv"
    csv_path.write_text(
        "symbol,name,exchange,assetType,ipoDate,delistingDate,status\n"
        "LMETA1,Alpha Co,NASDAQ,Common Stock,1999-01-01,null,Active\n"
        "LMETA2,Beta Fund,NYSE,ETF,2005-01-01,null,Active\n"
    )
    result = runner.invoke(app, [
        "data", "listing-meta", "--file", str(csv_path),
        "--db-url", schema.test_db_url(), "--json"])
    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    data = payload.get("data", payload)
    assert data["updated"] == 2
    with conn.cursor() as cur:
        cur.execute("SELECT asset_type FROM stocks WHERE symbol = 'LMETA2'")
        assert cur.fetchone()[0] == "ETF"


def test_cli_listing_meta_help_declares_source_options():
    result = runner.invoke(app, ["data", "listing-meta", "--help"])
    assert result.exit_code == 0
    assert "--file" in result.output
