"""Tests for stocks_fundamentals table and data-update integration."""
import os
from datetime import date

import psycopg
import pytest

from gefion.db import schema


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture
def conn():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    connection = create_connection()
    connection.autocommit = True
    with connection.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except psycopg.errors.DuplicateObject:
            pass
    schema.create_stocks_table(connection)
    yield connection
    # Clean up in dependency order
    with connection.cursor() as cur:
        try:
            cur.execute(
                "DELETE FROM stocks_fundamentals WHERE data_id IN "
                "(SELECT id FROM stocks WHERE symbol LIKE 'FUND_TEST_%')"
            )
        except Exception:
            pass
        cur.execute("DELETE FROM stocks WHERE symbol LIKE 'FUND_TEST_%'")
    connection.close()


class TestStocksFundamentalsTable:

    def test_create_table(self, conn):
        schema.create_stocks_fundamentals_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'stocks_fundamentals'")
            assert cur.fetchone() is not None

    def test_is_hypertable(self, conn):
        schema.create_stocks_fundamentals_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'stocks_fundamentals'")
            assert cur.fetchone() is not None

    def test_insert_and_query(self, conn):
        schema.create_stocks_fundamentals_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stocks (symbol) VALUES ('FUND_TEST_A') "
                "ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol RETURNING id"
            )
            stock_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO stocks_fundamentals (data_id, date, market_cap, pe_ratio, beta) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (data_id, date) DO UPDATE SET market_cap = EXCLUDED.market_cap",
                (stock_id, date(2026, 3, 29), 3000000000000, 28.5, 1.21),
            )
            cur.execute("SELECT market_cap, pe_ratio, beta FROM stocks_fundamentals WHERE data_id = %s", (stock_id,))
            row = cur.fetchone()
            assert row[0] == 3000000000000
            assert float(row[1]) == pytest.approx(28.5)
            assert float(row[2]) == pytest.approx(1.21)


class TestStocksExchangeColumn:

    def test_exchange_column_exists(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'stocks' AND column_name = 'exchange'")
            assert cur.fetchone() is not None

    def test_asset_type_column_exists(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'stocks' AND column_name = 'asset_type'")
            assert cur.fetchone() is not None


class TestFundamentalsParser:

    def test_parse_overview_extracts_fields(self):
        from gefion.alphavantage.catalog import parse_overview
        overview = {
            "Symbol": "AAPL", "Name": "Apple Inc", "Sector": "Technology",
            "Industry": "Consumer Electronics", "MarketCapitalization": "3000000000000",
            "PERatio": "28.5", "ForwardPE": "25.1", "PEGRatio": "2.1",
            "BookValue": "4.25", "DividendYield": "0.0055", "EPS": "6.42",
            "RevenuePerShareTTM": "24.32", "ProfitMargin": "0.265",
            "OperatingMarginTTM": "0.302", "ReturnOnEquityTTM": "1.56",
            "Beta": "1.21", "EVToEBITDA": "22.5", "SharesOutstanding": "15500000000",
        }
        result = parse_overview(overview)
        assert result["name"] == "Apple Inc"
        assert result["sector"] == "Technology"
        assert result["market_cap"] == 3000000000000
        assert result["pe_ratio"] == pytest.approx(28.5)
        assert result["operating_margin"] == pytest.approx(0.302)

    def test_parse_overview_handles_none_values(self):
        from gefion.alphavantage.catalog import parse_overview
        result = parse_overview({"Symbol": "AAPL", "MarketCapitalization": "None"})
        assert result["market_cap"] is None

    def test_parse_overview_handles_missing_keys(self):
        from gefion.alphavantage.catalog import parse_overview
        result = parse_overview({"Symbol": "AAPL"})
        assert result["name"] is None
        assert result["market_cap"] is None
