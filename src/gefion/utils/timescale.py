"""
TimescaleDB chunk management utilities.

Provides functions for:
- Querying chunk date ranges
- Creating missing chunks
- Filtering data to match available chunk ranges
- Preventing "chunk not found" errors
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Tuple, Iterable, Mapping, TypeVar
import warnings

import psycopg
from psycopg import sql
from gefion.observability import create_span, set_attributes


T = TypeVar('T', bound=Mapping)


def get_chunk_date_range(
    conn: psycopg.Connection,
    hypertable_name: str
) -> Tuple[Optional[date], Optional[date]]:
    """
    Query the min/max date range for available chunks in a hypertable.

    Args:
        conn: Database connection
        hypertable_name: Name of the hypertable (e.g., 'computed_features')

    Returns:
        Tuple of (min_date, max_date) or (None, None) if no chunks exist

    Example:
        >>> min_date, max_date = get_chunk_date_range(conn, 'computed_features')
        >>> # min_date might be date(2008, 1, 12), max_date might be date(2025, 12, 31)
    """
    with create_span("utils.timescale.get_chunk_date_range", hypertable=hypertable_name):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    MIN(range_start)::date AS min_date,
                    MAX(range_end)::date AS max_date
                FROM timescaledb_information.chunks
                WHERE hypertable_name = %s;
                """,
                (hypertable_name,)
            )
            row = cur.fetchone()
            if row and row[0] and row[1]:
                return (row[0], row[1])
            return (None, None)


def filter_rows_by_chunk_range(
    rows: Iterable[T],
    date_column: str,
    min_date: Optional[date],
    max_date: Optional[date],
    warn_on_skip: bool = True
) -> Tuple[list[T], int]:
    """
    Filter data rows to only include dates within the chunk range.

    Args:
        rows: Iterable of data rows (dicts or dict-like objects)
        date_column: Name of the date column to check
        min_date: Minimum date in chunk range (inclusive)
        max_date: Maximum date in chunk range (exclusive)
        warn_on_skip: Whether to emit warning when rows are skipped

    Returns:
        Tuple of (filtered_rows, skipped_count)

    Example:
        >>> rows = [
        ...     {"date": date(1999, 11, 1), "value": 100},
        ...     {"date": date(2020, 1, 1), "value": 200}
        ... ]
        >>> filtered, skipped = filter_rows_by_chunk_range(
        ...     rows, "date", date(2008, 1, 12), date(2025, 12, 31)
        ... )
        >>> # filtered contains only the 2020 row, skipped = 1
    """
    if min_date is None or max_date is None:
        # No chunk range constraints, return all rows
        return list(rows), 0

    filtered = []
    skipped_count = 0
    earliest_skipped = None
    latest_skipped = None

    for row in rows:
        row_date = row.get(date_column)

        # Handle different date formats
        if isinstance(row_date, date):
            parsed_date = row_date
        elif isinstance(row_date, str):
            try:
                from datetime import datetime
                parsed_date = datetime.fromisoformat(row_date).date()
            except Exception:
                # Can't parse date, include the row (let insert handle it)
                filtered.append(row)
                continue
        elif row_date is None:
            # No date, skip it
            continue
        else:
            # Unknown date format, include the row
            filtered.append(row)
            continue

        # Check if date is within chunk range
        if min_date <= parsed_date < max_date:
            filtered.append(row)
        else:
            skipped_count += 1
            if earliest_skipped is None or parsed_date < earliest_skipped:
                earliest_skipped = parsed_date
            if latest_skipped is None or parsed_date > latest_skipped:
                latest_skipped = parsed_date

    # Emit warning if rows were skipped
    if warn_on_skip and skipped_count > 0:
        if earliest_skipped == latest_skipped:
            date_range = f"date {earliest_skipped}"
        else:
            date_range = f"dates {earliest_skipped} to {latest_skipped}"
        warnings.warn(
            f"Skipped {skipped_count} rows with {date_range} "
            f"(outside chunk range {min_date} to {max_date}). "
            f"Run 'g2 db-tune --extend-chunks' to create missing chunks."
        )

    return filtered, skipped_count


def create_chunks_for_date_range(
    conn: psycopg.Connection,
    hypertable_name: str,
    start_date: date,
    end_date: date,
    chunk_interval_days: int = 30,
    dry_run: bool = False
) -> Tuple[int, list[str]]:
    """
    Create chunks for a date range in a hypertable.

    This is useful when you need to insert data that falls outside the
    current chunk range. It creates chunks by inserting and deleting a
    dummy row for each chunk boundary.

    Args:
        conn: Database connection
        hypertable_name: Name of the hypertable
        start_date: Start of date range to create chunks for
        end_date: End of date range to create chunks for
        chunk_interval_days: Size of chunks in days (default: 30)
        dry_run: If True, only report what would be done without creating chunks

    Returns:
        Tuple of (chunks_created, chunk_ranges) where chunk_ranges is a list
        of date range strings describing the chunks that were/would be created

    Warning:
        This function modifies the database by creating chunks. It requires
        a temporary dummy row to be inserted. For computed_features table,
        this requires a valid feature_id, data_id, and date.

    Example:
        >>> count, ranges = create_chunks_for_date_range(
        ...     conn, 'computed_features',
        ...     date(2000, 1, 1), date(2008, 1, 1),
        ...     chunk_interval_days=30
        ... )
        >>> # Creates chunks for the 2000-2008 range
    """
    with create_span("utils.timescale.create_chunks_for_date_range", hypertable=hypertable_name, dry_run=dry_run):
        current_min, current_max = get_chunk_date_range(conn, hypertable_name)

        chunks_to_create = []

        # Calculate chunk boundaries
        current = start_date
        while current < end_date:
            chunk_end = current + timedelta(days=chunk_interval_days)
            # Check if this chunk already exists
            if current_min is None or current < current_min:
                chunks_to_create.append((current, min(chunk_end, end_date)))
            current = chunk_end

        if dry_run:
            return len(chunks_to_create), [
                f"{start.isoformat()} to {end.isoformat()}"
                for start, end in chunks_to_create
            ]

        # Get IDs needed for chunk creation
        feature_id = None
        data_id = None

        with conn.cursor() as cur:
            if hypertable_name == "computed_features":
                # Need both feature_id and data_id
                cur.execute("SELECT id FROM feature_definitions LIMIT 1;")
                row = cur.fetchone()
                feature_id = row[0] if row else None

                cur.execute("SELECT id FROM stocks LIMIT 1;")
                row = cur.fetchone()
                data_id = row[0] if row else None

                if not feature_id or not data_id:
                    warnings.warn(
                        f"Cannot create chunks for {hypertable_name}: no feature_id or data_id available. "
                        f"Insert at least one feature definition and stock first."
                    )
                    return 0, []
            elif hypertable_name == "stock_ohlcv":
                # Only need data_id
                cur.execute("SELECT id FROM stocks LIMIT 1;")
                row = cur.fetchone()
                data_id = row[0] if row else None

                if not data_id:
                    warnings.warn(
                        f"Cannot create chunks for {hypertable_name}: no data_id available. "
                        f"Insert at least one stock first."
                    )
                    return 0, []

        # Create chunks by inserting and deleting dummy rows
        created = 0
        chunk_ranges = []

        for chunk_start, chunk_end in chunks_to_create:
            try:
                with conn.cursor() as cur:
                    if hypertable_name == "computed_features":
                        # Insert dummy row to create chunk
                        cur.execute(
                            """
                            INSERT INTO computed_features (feature_id, data_id, date, value, source)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING;
                            """,
                            (feature_id, data_id, chunk_start, 0.0, "__chunk_creation__")
                        )
                        # Delete dummy row
                        cur.execute(
                            """
                            DELETE FROM computed_features
                            WHERE feature_id = %s AND data_id = %s AND date = %s AND source = %s;
                            """,
                            (feature_id, data_id, chunk_start, "__chunk_creation__")
                        )
                    elif hypertable_name == "stock_ohlcv":
                        # Insert dummy row to create chunk
                        cur.execute(
                            """
                            INSERT INTO stock_ohlcv (data_id, date, close, source)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT DO NOTHING;
                            """,
                            (data_id, chunk_start, 0.0, "__chunk_creation__")
                        )
                        # Delete dummy row
                        cur.execute(
                            """
                            DELETE FROM stock_ohlcv
                            WHERE data_id = %s AND date = %s AND source = %s;
                            """,
                            (data_id, chunk_start, "__chunk_creation__")
                        )

                # Only commit if not in autocommit mode
                if not conn.autocommit:
                    conn.commit()
                created += 1
                chunk_ranges.append(f"{chunk_start.isoformat()} to {chunk_end.isoformat()}")

            except Exception as e:
                # If chunk creation fails, log but continue
                warnings.warn(f"Failed to create chunk for {chunk_start}: {e}")
                # Only rollback if not in autocommit mode
                if not conn.autocommit:
                    conn.rollback()

        return created, chunk_ranges


def ensure_chunks_for_date_range(
    conn: psycopg.Connection,
    hypertable_name: str,
    start_date: date,
    end_date: date,
    chunk_interval_days: int = 30
) -> bool:
    """
    Ensure chunks exist for a date range before inserting data.

    This is a convenience function that creates missing chunks automatically.
    Call this before bulk inserts to prevent "chunk not found" errors.

    Args:
        conn: Database connection
        hypertable_name: Name of hypertable
        start_date: Start of date range
        end_date: End of date range
        chunk_interval_days: Chunk size in days

    Returns:
        True if chunks exist or were created successfully, False otherwise

    Example:
        >>> # Before inserting data
        >>> ensure_chunks_for_date_range(conn, 'computed_features',
        ...     date(2000, 1, 1), date(2025, 12, 31))
        >>> # Now insert can proceed safely
    """
    with create_span("utils.timescale.ensure_chunks_for_date_range", hypertable=hypertable_name):
        try:
            created, ranges = create_chunks_for_date_range(
                conn, hypertable_name, start_date, end_date,
                chunk_interval_days, dry_run=False
            )
            if created > 0:
                warnings.warn(
                    f"Auto-created {created} chunks for {hypertable_name} "
                    f"to accommodate date range {start_date} to {end_date}"
                )
            return True
        except Exception as e:
            warnings.warn(f"Failed to ensure chunks for {hypertable_name}: {e}")
            return False


def validate_and_filter_insert_data(
    conn: psycopg.Connection,
    hypertable_name: str,
    rows: Iterable[T],
    date_column: str = "date",
    warn_on_skip: bool = True
) -> Tuple[list[T], dict]:
    """
    Validate data against chunk ranges and filter out invalid dates.

    This is a convenience function that combines chunk range detection
    and row filtering in a single call.

    Args:
        conn: Database connection
        hypertable_name: Name of the hypertable
        rows: Data rows to validate
        date_column: Name of the date column (default: "date")
        warn_on_skip: Whether to emit warnings for skipped rows

    Returns:
        Tuple of (filtered_rows, stats) where stats contains:
        - total_rows: Number of input rows
        - filtered_rows: Number of rows after filtering
        - skipped_rows: Number of rows skipped
        - chunk_min: Minimum date in chunk range
        - chunk_max: Maximum date in chunk range

    Example:
        >>> filtered, stats = validate_and_filter_insert_data(
        ...     conn, 'computed_features', rows
        ... )
        >>> print(f"Filtered {stats['filtered_rows']} / {stats['total_rows']} rows")
    """
    with create_span("utils.timescale.validate_and_filter_insert_data", hypertable=hypertable_name):
        # Get chunk range
        min_date, max_date = get_chunk_date_range(conn, hypertable_name)

        # Convert to list to count total rows
        rows_list = list(rows)
        total_rows = len(rows_list)

        # Filter rows
        filtered, skipped = filter_rows_by_chunk_range(
            rows_list, date_column, min_date, max_date, warn_on_skip
        )

        stats = {
            "total_rows": total_rows,
            "filtered_rows": len(filtered),
            "skipped_rows": skipped,
            "chunk_min": min_date,
            "chunk_max": max_date
        }

        return filtered, stats
