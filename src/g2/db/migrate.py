"""
Database migration helpers for schema updates.
"""
from __future__ import annotations

import psycopg
from psycopg import Connection


def fix_stock_prices_hypertable(conn: Connection) -> None:
    """
    Fix stock_prices table for TimescaleDB compatibility.

    TimescaleDB requires that UNIQUE constraints on hypertables include
    the partitioning column (date). This migration ensures the schema
    is correctly set up.
    """
    with conn.cursor() as cur:
        # Check if table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'stock_prices'
            );
        """)
        table_exists = cur.fetchone()[0]

        if not table_exists:
            return  # Nothing to migrate

        # Check if it's already a hypertable
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM timescaledb_information.hypertables
                WHERE hypertable_name = 'stock_prices'
            );
        """)
        is_hypertable = cur.fetchone()[0]

        if is_hypertable:
            return  # Already properly configured

        # Table exists but is not a hypertable - need to recreate
        # This is safe because we're in development/setup phase
        cur.execute("DROP TABLE IF EXISTS stock_prices CASCADE;")

    conn.commit()


def ensure_clean_schema(conn: Connection) -> None:
    """
    Ensure schema is in a clean state for TimescaleDB.

    Run this before create_stock_prices_table() if you encounter
    unique constraint errors.
    """
    with conn.cursor() as cur:
        # Drop and recreate if there are constraint issues
        try:
            cur.execute("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_name = 'stock_prices'
                AND constraint_type = 'UNIQUE';
            """)
            constraints = cur.fetchall()

            # If table exists but can't be converted to hypertable, drop it
            if constraints:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM timescaledb_information.hypertables
                        WHERE hypertable_name = 'stock_prices'
                    );
                """)
                is_hypertable = cur.fetchone()[0]

                if not is_hypertable:
                    # Table has constraints but isn't a hypertable - needs recreation
                    cur.execute("DROP TABLE IF EXISTS stock_prices CASCADE;")
        except Exception:
            # If anything fails, just drop and recreate
            cur.execute("DROP TABLE IF EXISTS stock_prices CASCADE;")

    conn.commit()
