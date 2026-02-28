"""
TDD tests for cross-sectional features.

These tests will initially fail and drive the implementation of
cross-sectional (market-relative) feature computation.

Requires ENABLE_DB_TESTS=1 to run.
"""
import os
import pytest
import psycopg
from g2.config import load_settings
from g2.db import schema


pytestmark = pytest.mark.skipif(
    os.getenv("ENABLE_DB_TESTS") != "1",
    reason="Database tests disabled. Set ENABLE_DB_TESTS=1 to run."
)


def get_db_url():
    """Get database URL for tests."""
    return schema.test_db_url()


@pytest.fixture
def db_conn():
    """Create a test database connection."""
    url = get_db_url()
    with psycopg.connect(url) as conn:
        conn.autocommit = True
        yield conn


@pytest.fixture
def setup_db(db_conn):
    """Set up test database schema and cross_sectional_features table."""
    schema.create_stocks_table(db_conn)

    # Create cross_sectional_features table with comparison_group
    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cross_sectional_features (
                data_id INTEGER NOT NULL,
                date DATE NOT NULL,
                feature_name TEXT NOT NULL,
                comparison_group TEXT NOT NULL DEFAULT 'market',
                value DOUBLE PRECISION,
                rank INTEGER,
                percentile DOUBLE PRECISION,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (data_id, date, feature_name, comparison_group)
            );
        """)

    yield

    # Cleanup
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS cross_sectional_features CASCADE;")
        cur.execute("DROP TABLE IF EXISTS stocks CASCADE;")


def test_compute_return_vs_market():
    """Test computing stock returns relative to market average."""
    from g2.compute.cross_sectional import compute_return_vs_market

    # Sample data: 3 stocks with different returns
    price_data = [
        {"symbol": "AAPL", "date": "2024-01-01", "close": 100.0},
        {"symbol": "AAPL", "date": "2024-01-02", "close": 105.0},  # +5 percent
        {"symbol": "MSFT", "date": "2024-01-01", "close": 200.0},
        {"symbol": "MSFT", "date": "2024-01-02", "close": 202.0},  # +1 percent
        {"symbol": "GOOGL", "date": "2024-01-01", "close": 150.0},
        {"symbol": "GOOGL", "date": "2024-01-02", "close": 147.0},  # -2 percent
    ]

    # Compute market-relative returns
    results = compute_return_vs_market(price_data, date="2024-01-02")

    # Market average return = (5 + 1 - 2) / 3 = 1.33 percent
    # Verify results
    assert len(results) == 3

    # AAPL: 5 - 1.33 = 3.67 percent above market
    aapl = next(r for r in results if r["symbol"] == "AAPL")
    assert abs(aapl["return_vs_market"] - 3.67) < 0.1

    # MSFT: 1 - 1.33 = -0.33 percent below market
    msft = next(r for r in results if r["symbol"] == "MSFT")
    assert abs(msft["return_vs_market"] + 0.33) < 0.1

    # GOOGL: -2 - 1.33 = -3.33 percent below market
    googl = next(r for r in results if r["symbol"] == "GOOGL")
    assert abs(googl["return_vs_market"] + 3.33) < 0.1


def test_compute_market_rankings():
    """Test computing market rankings based on returns."""
    from g2.compute.cross_sectional import compute_market_rankings

    # Sample data with clear rankings
    returns_data = [
        {"symbol": "AAPL", "date": "2024-01-02", "return": 0.05},  # Rank 1
        {"symbol": "MSFT", "date": "2024-01-02", "return": 0.01},  # Rank 2
        {"symbol": "GOOGL", "date": "2024-01-02", "return": -0.02},  # Rank 3
    ]

    results = compute_market_rankings(returns_data, date="2024-01-02")

    # Verify rankings
    assert len(results) == 3

    aapl = next(r for r in results if r["symbol"] == "AAPL")
    assert aapl["rank"] == 1
    assert abs(aapl["percentile"] - 1.0) < 0.01  # Top percentile

    msft = next(r for r in results if r["symbol"] == "MSFT")
    assert msft["rank"] == 2
    assert abs(msft["percentile"] - 0.5) < 0.1

    googl = next(r for r in results if r["symbol"] == "GOOGL")
    assert googl["rank"] == 3
    assert abs(googl["percentile"] - 0.0) < 0.01  # Bottom percentile


def test_cross_sectional_table_exists(db_conn, setup_db):
    """Test that cross_sectional_features table exists in database."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'cross_sectional_features'
            );
            """
        )
        exists = cur.fetchone()[0]
        assert exists, "cross_sectional_features table should exist"


def test_insert_cross_sectional_features(db_conn, setup_db):
    """Test inserting cross-sectional features into database."""
    from g2.db.cross_sectional import insert_cross_sectional_features
    from g2.db.ingest import upsert_stock

    # Insert test stocks first
    upsert_stock(db_conn, "AAPL")
    upsert_stock(db_conn, "MSFT")

    # Sample cross-sectional features
    features = [
        {
            "symbol": "AAPL",
            "date": "2024-01-02",
            "feature_name": "return_vs_market",
            "value": 3.67,
            "rank": 1,
            "percentile": 1.0,
        },
        {
            "symbol": "MSFT",
            "date": "2024-01-02",
            "feature_name": "return_vs_market",
            "value": -0.33,
            "rank": 2,
            "percentile": 0.5,
        },
    ]

    # Insert features
    inserted = insert_cross_sectional_features(db_conn, features)

    assert inserted == 2

    # Verify data was inserted
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, date, feature_name, value, rank, percentile
            FROM cross_sectional_features csf
            JOIN stocks s ON csf.data_id = s.id
            WHERE date = '2024-01-02'
            ORDER BY rank;
            """
        )
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "AAPL"  # symbol
        assert abs(rows[0][3] - 3.67) < 0.01  # value


def test_compute_percentiles():
    """Test percentile computation for cross-sectional ranking."""
    from g2.compute.cross_sectional import compute_percentiles

    data = [
        {"symbol": "A", "value": 10.0},
        {"symbol": "B", "value": 20.0},
        {"symbol": "C", "value": 30.0},
        {"symbol": "D", "value": 40.0},
        {"symbol": "E", "value": 50.0},
    ]

    results = compute_percentiles(data, value_key="value")

    # Verify percentiles (0-1 range)
    assert len(results) == 5

    # Highest value should be 100th percentile (1.0)
    highest = next(r for r in results if r["symbol"] == "E")
    assert abs(highest["percentile"] - 1.0) < 0.01

    # Lowest value should be 0th percentile (0.0)
    lowest = next(r for r in results if r["symbol"] == "A")
    assert abs(lowest["percentile"] - 0.0) < 0.01

    # Middle value should be around 50th percentile (0.5)
    middle = next(r for r in results if r["symbol"] == "C")
    assert abs(middle["percentile"] - 0.5) < 0.1
