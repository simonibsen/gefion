"""
DDL helpers for stock tables.

We keep DDL as simple SQL strings executed via psycopg2. Hypertable creation
assumes TimescaleDB is installed/enabled (see docker-compose).
"""

from __future__ import annotations

import os

import psycopg
from psycopg import Connection
from psycopg import sql


def test_db_url() -> str:
    """Return DATABASE_URL for tests, defaulting to local Timescale compose."""
    return os.environ.get("DATABASE_URL", "postgresql://g2:g2pass@localhost:5432/g2")


def _ensure_timescaledb(conn: Connection) -> None:
    """Ensure TimescaleDB extension is enabled, handling version conflicts gracefully."""
    with conn.cursor() as cur:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except psycopg.errors.DuplicateObject:
            # Extension already loaded with different version - this is fine
            pass
    conn.commit()


def create_stocks_table(conn: Connection) -> None:
    """Create stocks dimension table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS stocks (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL UNIQUE,
                status TEXT
            );
            """
        )
    conn.commit()


def create_stock_prices_table(conn: Connection) -> None:
    """Create stock_prices hypertable with unique stock/date constraint."""
    _ensure_timescaledb(conn)

    # Check if table exists but is not a hypertable - if so, drop and recreate
    # This fixes issues where the table was created before TimescaleDB was enabled
    with conn.cursor() as cur:
        try:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'stock_prices'
                );
            """)
            table_exists = cur.fetchone()[0]

            if table_exists:
                # Check if it's already a hypertable
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM timescaledb_information.hypertables
                        WHERE hypertable_schema = 'public' AND hypertable_name = 'stock_prices'
                    );
                """)
                is_hypertable = cur.fetchone()[0]

                if not is_hypertable:
                    # Table exists but isn't a hypertable - drop and recreate
                    print("Dropping existing stock_prices table to recreate as hypertable...")
                    cur.execute("DROP TABLE IF EXISTS stock_prices CASCADE;")
                    conn.commit()
        except Exception as e:
            # If TimescaleDB queries fail, just try to continue
            pass

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_prices (
                id BIGSERIAL,
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                open NUMERIC(18,6),
                high NUMERIC(18,6),
                low NUMERIC(18,6),
                close NUMERIC(18,6),
                adjusted_close NUMERIC(18,6),
                volume BIGINT,
                source TEXT,
                PRIMARY KEY (id, date),
                UNIQUE (data_id, date)
            );
            """
        )
        cur.execute(
            """
            SELECT create_hypertable('stock_prices', 'date', if_not_exists => TRUE);
            """
        )
        # Performance helpers: chunk interval and BRIN on date for large scans
        try:
            cur.execute("SELECT set_chunk_time_interval('stock_prices', INTERVAL '30 days');")
        except Exception:
            pass
        cur.execute("CREATE INDEX IF NOT EXISTS stock_prices_brin ON stock_prices USING BRIN(date);")
        # Composite B-tree index for efficient single-stock time-series queries
        # Optimized for "SELECT ... WHERE data_id = X AND date BETWEEN Y AND Z ORDER BY date DESC"
        cur.execute("""
            CREATE INDEX IF NOT EXISTS stock_prices_data_id_date_idx
                ON stock_prices(data_id, date DESC);
        """)
    conn.commit()


def create_company_fundamentals_history_table(conn: Connection) -> None:
    """Create wide fundamentals history table."""
    _ensure_timescaledb(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS company_fundamentals_history (
                id BIGSERIAL,
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                market_cap DOUBLE PRECISION,
                pe_ratio DOUBLE PRECISION,
                peg_ratio DOUBLE PRECISION,
                dividend_yield DOUBLE PRECISION,
                eps DOUBLE PRECISION,
                revenue_per_share DOUBLE PRECISION,
                profit_margin DOUBLE PRECISION,
                operating_margin DOUBLE PRECISION,
                roe DOUBLE PRECISION,
                roa DOUBLE PRECISION,
                beta DOUBLE PRECISION,
                shares_outstanding BIGINT,
                source TEXT,
                PRIMARY KEY (id, date),
                UNIQUE (data_id, date)
            );
            """
        )
        cur.execute(
            """
            SELECT create_hypertable('company_fundamentals_history', 'date', if_not_exists => TRUE);
            """
        )
        try:
            cur.execute("SELECT set_chunk_time_interval('company_fundamentals_history', INTERVAL '30 days');")
        except Exception:
            pass
        cur.execute(
            "CREATE INDEX IF NOT EXISTS company_fundamentals_history_brin ON company_fundamentals_history USING BRIN(date);"
        )
    conn.commit()


def create_feature_definitions_table(conn: Connection) -> None:
    """Descriptor table for computed features."""
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
            """
        )
    conn.commit()


def create_computed_features_table(conn: Connection) -> None:
    """Tall table for computed feature values."""
    _ensure_timescaledb(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS computed_features (
                data_id INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                feature_id INTEGER NOT NULL REFERENCES feature_definitions(id),
                value DOUBLE PRECISION,
                source TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (feature_id, data_id, date)
            );
            """
        )
        cur.execute(
            """
            SELECT create_hypertable('computed_features', 'date', if_not_exists => TRUE);
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS computed_features_idx ON computed_features(feature_id, data_id, date);")
        try:
            cur.execute("SELECT set_chunk_time_interval('computed_features', INTERVAL '30 days');")
        except Exception:
            pass
        cur.execute("CREATE INDEX IF NOT EXISTS computed_features_brin ON computed_features USING BRIN(date);")
        # Composite B-tree index optimized for feature-specific queries with DESC date ordering
        # Optimized for "SELECT ... WHERE feature_id = X AND data_id = Y AND date BETWEEN ... ORDER BY date DESC"
        cur.execute("""
            CREATE INDEX IF NOT EXISTS computed_features_feature_data_date_idx
                ON computed_features(feature_id, data_id, date DESC);
        """)
    conn.commit()


def migrate_stock_tables_to_data_id(conn: Connection) -> None:
    """
    Rename legacy stock_id columns to data_id if they exist.

    Safe to run repeatedly; no-op when already migrated.
    """
    tables = ["stock_prices", "company_fundamentals_history"]
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = %s AND column_name = 'stock_id';
                """,
                (table,),
            )
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER TABLE {} RENAME COLUMN stock_id TO data_id;").format(sql.Identifier(table))
                )
    conn.commit()


def drop_legacy_stock_indicators(conn: Connection) -> None:
    """Drop legacy wide stock_indicators table if present."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS stock_indicators CASCADE;")
    conn.commit()
