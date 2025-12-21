"""
Database migration helpers for schema updates.
"""
from __future__ import annotations

import psycopg
from psycopg import Connection


def fix_stock_ohlcv_hypertable(conn: Connection) -> None:
    """
    Fix stock_ohlcv table for TimescaleDB compatibility.

    TimescaleDB requires that UNIQUE constraints on hypertables include
    the partitioning column (date). This migration ensures the schema
    is correctly set up.
    """
    with conn.cursor() as cur:
        # Check if table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'stock_ohlcv'
            );
        """)
        table_exists = cur.fetchone()[0]

        if not table_exists:
            return  # Nothing to migrate

        # Check if it's already a hypertable
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM timescaledb_information.hypertables
                WHERE hypertable_name = 'stock_ohlcv'
            );
        """)
        is_hypertable = cur.fetchone()[0]

        if is_hypertable:
            return  # Already properly configured

        # Table exists but is not a hypertable - need to recreate
        # This is safe because we're in development/setup phase
        cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")

    conn.commit()


def ensure_clean_schema(conn: Connection) -> None:
    """
    Ensure schema is in a clean state for TimescaleDB.

    Run this before create_stock_ohlcv_table() if you encounter
    unique constraint errors.
    """
    with conn.cursor() as cur:
        # Drop and recreate if there are constraint issues
        try:
            cur.execute("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_name = 'stock_ohlcv'
                AND constraint_type = 'UNIQUE';
            """)
            constraints = cur.fetchall()

            # If table exists but can't be converted to hypertable, drop it
            if constraints:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM timescaledb_information.hypertables
                        WHERE hypertable_name = 'stock_ohlcv'
                    );
                """)
                is_hypertable = cur.fetchone()[0]

                if not is_hypertable:
                    # Table has constraints but isn't a hypertable - needs recreation
                    cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")
        except Exception:
            # If anything fails, just drop and recreate
            cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")

    conn.commit()


def migrate_feature_definitions_source_table(conn: Connection) -> int:
    """
    Update feature_definitions to point to the renamed source table.

    Returns number of rows updated.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE feature_definitions
            SET source_table = 'stock_ohlcv'
            WHERE source_table = 'stock_prices';
            """
        )
        updated = cur.rowcount
    conn.commit()
    return updated


def migrate_stock_prices_to_ohlcv(conn: Connection, drop_old: bool = False) -> tuple[int, int]:
    """
    Copy rows from legacy stock_prices into stock_ohlcv.

    Returns (copied_rows, dropped_flag). Idempotent: skip copy if stock_prices missing.
    """
    copied = 0
    dropped = 0
    with conn.cursor() as cur:
        # Ensure destination exists
        from g2.db import schema

        schema.create_stock_ohlcv_table(conn)

        # If legacy table is missing, nothing to do
        cur.execute(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'stock_prices'
            );
            """
        )
        if not cur.fetchone()[0]:
            conn.commit()
            return copied, dropped

        # Copy with conflict ignore
        cur.execute(
            """
            INSERT INTO stock_ohlcv
                (data_id, date, open, high, low, close, adjusted_close, dividend_amount, split_coefficient, volume, source)
            SELECT data_id, date, open, high, low, close, adjusted_close, NULL, NULL, volume, source
            FROM stock_prices
            ON CONFLICT (data_id, date) DO NOTHING;
            """
        )
        copied = cur.rowcount

        if drop_old:
            cur.execute("DROP TABLE IF EXISTS stock_prices CASCADE;")
            dropped = 1

    conn.commit()
    return copied, dropped
