#!/usr/bin/env python3
"""
Fix stock_ohlcv table for TimescaleDB compatibility.

Run this if you see errors like:
"cannot create a unique index without the column 'date' (used in partitioning)"

This script will drop and recreate the stock_ohlcv table properly.
"""
import os
import sys

import psycopg

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from g2.db import schema
from g2.config import load_settings


def main():
    print("🔧 Fixing stock_ohlcv table for TimescaleDB compatibility...")

    # Load database URL
    try:
        settings = load_settings()
        db_url = settings.database_url
    except Exception:
        db_url = schema.test_db_url()

    print(f"Connecting to: {db_url}")

    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True

            # Check current state
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'stock_ohlcv'
                    );
                """)
                table_exists = cur.fetchone()[0]

                if table_exists:
                    try:
                        cur.execute("""
                            SELECT EXISTS (
                                SELECT FROM timescaledb_information.hypertables
                                WHERE hypertable_schema = 'public' AND hypertable_name = 'stock_ohlcv'
                            );
                        """)
                        is_hypertable = cur.fetchone()[0]

                        if is_hypertable:
                            print("✅ Table is already a properly configured hypertable!")
                            return 0
                        else:
                            print("⚠️  Table exists but is not a hypertable. Dropping...")
                            cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")
                            print("✅ Dropped old table")
                    except Exception as e:
                        print(f"⚠️  Could not check hypertable status: {e}")
                        print("Dropping table anyway...")
                        cur.execute("DROP TABLE IF EXISTS stock_ohlcv CASCADE;")
                else:
                    print("ℹ️  Table doesn't exist yet")

            # Recreate table properly
            print("Creating stock_ohlcv table as hypertable...")
            schema.create_stocks_table(conn)
            schema.create_stock_ohlcv_table(conn)
            print("✅ Table created successfully!")

            # Verify
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM timescaledb_information.hypertables
                        WHERE hypertable_schema = 'public' AND hypertable_name = 'stock_ohlcv'
                    );
                """)
                is_hypertable = cur.fetchone()[0]

                if is_hypertable:
                    print("✅ Verified: Table is now a proper hypertable!")
                else:
                    print("❌ Warning: Table was created but is not a hypertable")
                    return 1

        print("\n🎉 Migration complete! You can now run g2 commands.")
        return 0

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
