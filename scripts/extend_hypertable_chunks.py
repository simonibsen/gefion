#!/usr/bin/env python3
"""
Script to extend hypertable chunk ranges by creating missing chunks.

This script helps resolve "chunk not found" errors by creating missing chunks
in TimescaleDB hypertables. It inserts dummy rows to force chunk creation,
then deletes them.

Usage:
    python scripts/extend_hypertable_chunks.py --table computed_features --start-date 2000-01-01

This will create chunks from 2000-01-01 to the earliest existing chunk date.
"""
import argparse
import os
import sys
from datetime import date, timedelta
from typing import Optional

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import psycopg
from psycopg import sql


def get_chunk_range(conn: psycopg.Connection, table_name: str) -> tuple[Optional[date], Optional[date]]:
    """Get the current chunk date range for a hypertable."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                MIN(range_start)::date AS min_date,
                MAX(range_end)::date AS max_date
            FROM timescaledb_information.chunks
            WHERE hypertable_name = %s;
            """,
            (table_name,)
        )
        row = cur.fetchone()
        if row and row[0] and row[1]:
            return (row[0], row[1])
        return (None, None)


def get_dummy_ids_for_table(conn: psycopg.Connection, table_name: str) -> tuple[Optional[int], Optional[int]]:
    """
    Get dummy IDs to use for creating chunks.

    Returns (feature_id, data_id) or (None, data_id) depending on table.
    """
    if table_name == "computed_features":
        # Need both feature_id and data_id
        with conn.cursor() as cur:
            # Get the first feature_id
            cur.execute("SELECT id FROM feature_definitions LIMIT 1;")
            row = cur.fetchone()
            feature_id = row[0] if row else None

            # Get the first stock id
            cur.execute("SELECT id FROM stocks LIMIT 1;")
            row = cur.fetchone()
            data_id = row[0] if row else None

            return (feature_id, data_id)
    elif table_name == "stock_ohlcv":
        # Only need data_id
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM stocks LIMIT 1;")
            row = cur.fetchone()
            data_id = row[0] if row else None
            return (None, data_id)
    else:
        return (None, None)


def create_chunk_for_date(
    conn: psycopg.Connection,
    table_name: str,
    target_date: date,
    feature_id: Optional[int],
    data_id: int
) -> bool:
    """
    Create a chunk for a specific date by inserting and deleting a dummy row.

    Returns True if successful, False otherwise.
    """
    try:
        with conn.cursor() as cur:
            if table_name == "computed_features":
                if feature_id is None:
                    print(f"  ⚠️  No feature_id available, skipping {target_date}")
                    return False

                # Insert dummy row
                cur.execute(
                    """
                    INSERT INTO computed_features (feature_id, data_id, date, value, source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING;
                    """,
                    (feature_id, data_id, target_date, 0.0, "dummy_for_chunk_creation")
                )

                # Delete dummy row
                cur.execute(
                    """
                    DELETE FROM computed_features
                    WHERE feature_id = %s AND data_id = %s AND date = %s AND source = %s;
                    """,
                    (feature_id, data_id, target_date, "dummy_for_chunk_creation")
                )

            elif table_name == "stock_ohlcv":
                # Insert dummy row
                cur.execute(
                    """
                    INSERT INTO stock_ohlcv (data_id, date, close, source)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING;
                    """,
                    (data_id, target_date, 0.0, "dummy_for_chunk_creation")
                )

                # Delete dummy row
                cur.execute(
                    """
                    DELETE FROM stock_ohlcv
                    WHERE data_id = %s AND date = %s AND source = %s;
                    """,
                    (data_id, target_date, "dummy_for_chunk_creation")
                )

            conn.commit()
            return True

    except Exception as e:
        print(f"  ❌ Failed to create chunk for {target_date}: {e}")
        conn.rollback()
        return False


def extend_chunks(
    conn: psycopg.Connection,
    table_name: str,
    start_date: date,
    end_date: Optional[date] = None,
    chunk_interval_days: int = 30,
    dry_run: bool = False
) -> int:
    """
    Extend chunk range by creating missing chunks.

    Args:
        conn: Database connection
        table_name: Name of hypertable
        start_date: Start date for new chunks
        end_date: End date for new chunks (defaults to current min chunk date)
        chunk_interval_days: Size of chunks in days
        dry_run: If True, only show what would be done

    Returns:
        Number of chunks created
    """
    # Get current chunk range
    min_chunk, max_chunk = get_chunk_range(conn, table_name)

    if min_chunk is None:
        print(f"❌ No chunks exist for {table_name}, cannot extend")
        return 0

    # Default end_date to current min chunk date
    if end_date is None:
        end_date = min_chunk

    # Validate date range
    if start_date >= end_date:
        print(f"❌ Invalid date range: start {start_date} >= end {end_date}")
        return 0

    print(f"\n📊 Current chunk range for {table_name}: {min_chunk} to {max_chunk}")
    print(f"🎯 Extending backwards from {end_date} to {start_date}")
    print(f"📦 Chunk interval: {chunk_interval_days} days\n")

    # Get dummy IDs for creating chunks
    feature_id, data_id = get_dummy_ids_for_table(conn, table_name)

    if data_id is None:
        print(f"❌ No data_id available (no stocks exist), cannot create chunks")
        return 0

    # Calculate chunks to create
    chunks_to_create = []
    current = start_date
    while current < end_date:
        chunk_end = min(current + timedelta(days=chunk_interval_days), end_date)
        chunks_to_create.append((current, chunk_end))
        current = chunk_end

    print(f"Will create {len(chunks_to_create)} chunks:")
    for chunk_start, chunk_end in chunks_to_create[:5]:  # Show first 5
        print(f"  • {chunk_start} to {chunk_end}")
    if len(chunks_to_create) > 5:
        print(f"  ... and {len(chunks_to_create) - 5} more")

    if dry_run:
        print("\n🔍 DRY RUN - No changes made")
        return 0

    print("\n⚙️  Creating chunks...")

    created = 0
    for i, (chunk_start, chunk_end) in enumerate(chunks_to_create):
        # Create chunk by inserting at chunk boundary
        if create_chunk_for_date(conn, table_name, chunk_start, feature_id, data_id):
            created += 1
            if (i + 1) % 10 == 0:
                print(f"  ✓ Created {i + 1}/{len(chunks_to_create)} chunks...")
        else:
            print(f"  ✗ Failed to create chunk starting at {chunk_start}")

    # Verify new chunk range
    new_min, new_max = get_chunk_range(conn, table_name)
    print(f"\n✅ New chunk range: {new_min} to {new_max}")
    print(f"📊 Created {created} new chunks\n")

    return created


def main():
    parser = argparse.ArgumentParser(
        description="Extend TimescaleDB hypertable chunk ranges to cover historical data"
    )
    parser.add_argument(
        "--table",
        required=True,
        choices=["computed_features", "stock_ohlcv"],
        help="Hypertable to extend"
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Start date for new chunks (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        help="End date for new chunks (defaults to current min chunk date)"
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=30,
        help="Chunk interval in days (default: 30)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--db-url",
        help="Database URL (defaults to G2_DB_URL env var)"
    )

    args = parser.parse_args()

    # Parse dates
    try:
        start_date = date.fromisoformat(args.start_date)
        end_date = date.fromisoformat(args.end_date) if args.end_date else None
    except ValueError as e:
        print(f"❌ Invalid date format: {e}")
        return 1

    # Get database URL
    db_url = args.db_url or os.environ.get("G2_DB_URL")
    if not db_url:
        # Try loading from settings
        try:
            from g2.config import load_settings
            settings = load_settings()
            db_url = settings.database_url
        except Exception:
            print("❌ No database URL provided (use --db-url or set G2_DB_URL)")
            return 1

    # Connect and extend chunks
    try:
        with psycopg.connect(db_url) as conn:
            conn.autocommit = True
            created = extend_chunks(
                conn,
                args.table,
                start_date,
                end_date,
                args.chunk_days,
                args.dry_run
            )

        if not args.dry_run:
            print(f"✅ Successfully created {created} chunks")

        return 0

    except Exception as e:
        print(f"❌ Failed to extend chunks: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
