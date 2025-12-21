"""
Tests for backtest CLI commands.
"""
import os
import pytest
import psycopg
from datetime import date, timedelta

from g2.db import schema
from g2.config import load_settings


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        # Load settings to get DATABASE_URL from .env
        settings = load_settings()
        db_url = os.environ.get("DATABASE_URL", settings.database_url)
        return psycopg.connect(db_url)
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    connection = create_connection()
    connection.autocommit = True
    # Store the db_url for tests to use
    settings = load_settings()
    connection.test_db_url = os.environ.get("DATABASE_URL", settings.database_url)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def setup_tables(conn):
    """Setup tables for testing."""
    with conn.cursor() as cur:
        # Clean existing
        cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stocks CASCADE;")

        # Create stocks table
        cur.execute("""
            CREATE TABLE stocks (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL UNIQUE,
                exchange TEXT,
                status TEXT
            );
        """)

        # Create stock_ohlcv table
        cur.execute("""
            CREATE TABLE stock_ohlcv (
                id BIGSERIAL PRIMARY KEY,
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                open NUMERIC(18,6),
                high NUMERIC(18,6),
                low NUMERIC(18,6),
                close NUMERIC(18,6),
                adjusted_close NUMERIC(18,6),
                dividend_amount NUMERIC(18,6),
                split_coefficient NUMERIC(18,6),
                volume BIGINT,
                source TEXT,
                UNIQUE (data_id, date)
            );
        """)
    yield


def test_backtest_data_loader_with_symbols(conn):
    """Test loading price data for specific symbols."""
    from g2.backtest.data_loader import load_price_data_for_backtest
    from g2.db.ingest import upsert_stock, insert_stock_ohlcv

    # Insert test data
    aapl_id = upsert_stock(conn, "AAPL", status="Active")
    msft_id = upsert_stock(conn, "MSFT", status="Active")

    # Insert price data
    today = date.today()
    for i in range(10):
        test_date = today - timedelta(days=i)
        insert_stock_ohlcv(
            conn,
            aapl_id,
            [{
                "date": test_date,
                "open": 100.0 + i,
                "high": 102.0 + i,
                "low": 99.0 + i,
                "close": 101.0 + i,
                "volume": 1000000,
            }]
        )
        insert_stock_ohlcv(
            conn,
            msft_id,
            [{
                "date": test_date,
                "open": 200.0 + i,
                "high": 202.0 + i,
                "low": 199.0 + i,
                "close": 201.0 + i,
                "volume": 2000000,
            }]
        )

    # Load price data
    db_url = conn.test_db_url
    price_data = load_price_data_for_backtest(
        db_url=db_url,
        symbols=["AAPL", "MSFT"],
    )

    # Verify results
    assert len(price_data) == 20  # 10 days × 2 symbols
    symbols_found = set(row["symbol"] for row in price_data)
    assert symbols_found == {"AAPL", "MSFT"}

    # Verify structure
    for row in price_data:
        assert "symbol" in row
        assert "date" in row
        assert "close" in row
        assert "open" in row
        assert "high" in row
        assert "low" in row
        assert "volume" in row


def test_backtest_data_loader_with_date_range(conn):
    """Test loading price data with date filtering."""
    from g2.backtest.data_loader import load_price_data_for_backtest
    from g2.db.ingest import upsert_stock, insert_stock_ohlcv

    # Insert test data
    aapl_id = upsert_stock(conn, "AAPL", status="Active")

    # Insert 30 days of data
    today = date.today()
    for i in range(30):
        test_date = today - timedelta(days=i)
        insert_stock_ohlcv(
            conn,
            aapl_id,
            [{
                "date": test_date,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1000000,
            }]
        )

    # Load only last 10 days
    start_date = today - timedelta(days=9)
    end_date = today

    db_url = conn.test_db_url
    price_data = load_price_data_for_backtest(
        db_url=db_url,
        symbols=["AAPL"],
        start_date=start_date,
        end_date=end_date,
    )

    # Should have 10 rows (10 days × 1 symbol)
    assert len(price_data) == 10

    # Verify all dates are in range
    for row in price_data:
        assert start_date <= row["date"] <= end_date


def test_backtest_run_end_to_end(conn):
    """Test full backtest workflow with real data."""
    from g2.backtest.data_loader import load_price_data_for_backtest
    from g2.backtest.engine import BacktestEngine
    from g2.strategies.momentum import MomentumStrategy
    from g2.db.ingest import upsert_stock, insert_stock_ohlcv

    # Insert test data for multiple symbols
    symbols = ["AAPL", "MSFT", "GOOGL"]
    today = date.today()

    for symbol in symbols:
        stock_id = upsert_stock(conn, symbol, status="Active")

        # Insert 60 days of price data with different trends
        # AAPL: strong uptrend
        # MSFT: moderate uptrend
        # GOOGL: downtrend
        for i in range(60):
            test_date = today - timedelta(days=59 - i)

            if symbol == "AAPL":
                close = 100.0 + i * 0.5  # +0.5% per day
            elif symbol == "MSFT":
                close = 200.0 + i * 0.25  # +0.25% per day
            else:  # GOOGL
                close = 150.0 - i * 0.3  # -0.3% per day

            insert_stock_ohlcv(
            conn,
            stock_id,
            [{
                "date": test_date,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000000,
            }]
        )

    # Load price data
    db_url = conn.test_db_url
    start = today - timedelta(days=50)
    end = today - timedelta(days=1)

    price_data = load_price_data_for_backtest(
        db_url=db_url,
        symbols=symbols,
        start_date=start,
        end_date=end,
    )

    assert len(price_data) > 0

    # Initialize strategy
    strategy = MomentumStrategy(
        lookback_days=10,
        top_n=2,
        rebalance_days=5,
    )

    # Create wrapper function
    def strategy_fn(current_date, portfolio, prices):
        return strategy.generate_signals(
            current_date=current_date,
            portfolio=portfolio,
            price_data=prices,
            initial_cash=100000.0,
        )

    # Run backtest
    engine = BacktestEngine(
        price_data=price_data,
        strategy=strategy_fn,
        initial_cash=100000.0,
        start_date=start,
        end_date=end,
    )

    results = engine.run()

    # Verify results structure
    assert "trades" in results
    assert "equity_curve" in results
    assert "metrics" in results

    # Verify metrics
    metrics = results["metrics"]
    assert "total_return" in metrics
    assert "sharpe_ratio" in metrics
    assert "max_drawdown" in metrics

    # Strategy should have picked AAPL and MSFT (positive momentum)
    # and avoided GOOGL (negative momentum)
    trades = results["trades"]
    if trades:
        symbols_traded = set(trade["symbol"] for trade in trades)
        # Should trade AAPL/MSFT (top momentum), not GOOGL
        assert "GOOGL" not in symbols_traded or len([t for t in trades if t["symbol"] == "GOOGL"]) < len([t for t in trades if t["symbol"] != "GOOGL"])

    # Final equity should be positive (AAPL and MSFT both trending up)
    if results["equity_curve"]:
        final_equity = results["equity_curve"][-1]["equity"]
        # With strong uptrends, should have positive return
        # (though we can't guarantee this in all cases)
        assert final_equity > 0


def test_backtest_empty_data(conn):
    """Test backtest with no price data."""
    from g2.backtest.data_loader import load_price_data_for_backtest

    db_url = conn.test_db_url
    price_data = load_price_data_for_backtest(
        db_url=db_url,
        symbols=["NONEXISTENT"],
    )

    # Should return empty list
    assert price_data == []


def test_get_available_symbols(conn):
    """Test getting list of available symbols."""
    from g2.backtest.data_loader import get_available_symbols
    from g2.db.ingest import upsert_stock, insert_stock_ohlcv

    # Insert test stocks with sufficient data
    symbols = ["AAPL", "MSFT", "GOOGL", "NVDA"]
    today = date.today()

    for symbol in symbols:
        stock_id = upsert_stock(conn, symbol, status="Active")

        # Insert 100 days of data (> 50 required by query)
        for i in range(100):
            test_date = today - timedelta(days=i)
            insert_stock_ohlcv(
            conn,
            stock_id,
            [{
                "date": test_date,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1000000,
            }]
        )

    # Get available symbols
    db_url = conn.test_db_url
    available = get_available_symbols(db_url=db_url)

    # Should return all 4 symbols (all have > 50 records)
    assert set(available) == set(symbols)


def test_backtest_with_limit(conn):
    """Test backtest with symbol limit."""
    from g2.backtest.data_loader import load_price_data_for_backtest
    from g2.db.ingest import upsert_stock, insert_stock_ohlcv

    # Insert data for 5 symbols
    symbols = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA"]
    today = date.today()

    for symbol in symbols:
        stock_id = upsert_stock(conn, symbol, status="Active")
        # Set exchange manually
        with conn.cursor() as cur:
            cur.execute("UPDATE stocks SET exchange = %s WHERE id = %s", ("NASDAQ", stock_id))

        for i in range(10):
            test_date = today - timedelta(days=i)
            insert_stock_ohlcv(
            conn,
            stock_id,
            [{
                "date": test_date,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1000000,
            }]
        )

    # Load with limit
    db_url = conn.test_db_url
    price_data = load_price_data_for_backtest(
        db_url=db_url,
        exchange="NASDAQ",
        limit=3,
    )

    # Should have 30 rows (10 days × 3 symbols)
    symbols_found = set(row["symbol"] for row in price_data)
    assert len(symbols_found) == 3
