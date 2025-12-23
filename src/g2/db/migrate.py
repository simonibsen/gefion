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


# =============================================================================
# Migration Runner System
# =============================================================================


def ensure_migrations_table(conn: Connection) -> None:
    """
    Ensure schema_migrations tracking table exists.

    This table tracks which migrations have been applied to the database.
    Safe to call multiple times (idempotent).
    """
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                version TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT NOW(),
                checksum TEXT
            );
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_schema_migrations_version
                ON schema_migrations(version);
        """)

    conn.commit()


def get_applied_migrations(conn: Connection) -> set[str]:
    """
    Get set of migration versions that have been applied.

    Returns:
        Set of version strings (e.g., {"001", "002", "003"})
    """
    ensure_migrations_table(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version;")
        return {row[0] for row in cur.fetchall()}


def record_migration(
    conn: Connection,
    version: str,
    name: str,
    checksum: str | None = None
) -> None:
    """
    Record a migration as applied.

    Args:
        conn: Database connection
        version: Migration version (e.g., "001")
        name: Migration name (e.g., "create_users_table")
        checksum: Optional checksum of migration file
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO schema_migrations (version, name, checksum)
            VALUES (%s, %s, %s)
            ON CONFLICT (version) DO NOTHING;
        """, (version, name, checksum))

    conn.commit()


def scan_migration_files(migrations_dir) -> list[dict]:
    """
    Scan migration directory for SQL files.

    Args:
        migrations_dir: Path to migrations directory

    Returns:
        List of migration dicts sorted by version:
        [
            {"version": "001", "name": "create_users", "path": Path(...)}
        ]
    """
    from pathlib import Path
    import re

    migrations_path = Path(migrations_dir)
    if not migrations_path.exists():
        return []

    migrations = []
    pattern = re.compile(r'^(\d+)_(.+)\.sql$')

    for file_path in migrations_path.glob("*.sql"):
        match = pattern.match(file_path.name)
        if match:
            version, name = match.groups()
            migrations.append({
                "version": version,
                "name": name,
                "path": file_path
            })

    # Sort by version number
    migrations.sort(key=lambda m: m["version"])

    return migrations


def get_pending_migrations(
    conn: Connection,
    all_migrations: list[dict]
) -> list[dict]:
    """
    Filter migrations to only those not yet applied.

    Args:
        conn: Database connection
        all_migrations: List of all available migrations

    Returns:
        List of migrations that haven't been applied yet
    """
    applied = get_applied_migrations(conn)
    return [m for m in all_migrations if m["version"] not in applied]


def compute_checksum(file_path) -> str:
    """Compute SHA256 checksum of migration file."""
    import hashlib
    from pathlib import Path

    content = Path(file_path).read_bytes()
    return hashlib.sha256(content).hexdigest()


def apply_migration(conn: Connection, migration: dict) -> None:
    """
    Apply a single migration file.

    Args:
        migration: Migration dict with version, name, and path

    Raises:
        Exception if migration fails (will not be recorded)
    """
    from pathlib import Path
    import re

    migration_path = Path(migration["path"])
    sql_content = migration_path.read_text()
    checksum = compute_checksum(migration_path)

    # Filter out psql meta-commands (lines starting with \)
    # These are psql-specific and not valid SQL
    lines = sql_content.split('\n')
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip psql meta-commands
        if stripped.startswith('\\'):
            continue
        filtered_lines.append(line)

    filtered_sql = '\n'.join(filtered_lines)

    # Execute migration SQL
    with conn.cursor() as cur:
        cur.execute(filtered_sql)

    conn.commit()

    # Record successful migration
    record_migration(
        conn,
        migration["version"],
        migration["name"],
        checksum
    )


def run_migrations(
    conn: Connection,
    migrations_dir,
    dry_run: bool = False
) -> dict:
    """
    Run all pending migrations.

    Args:
        conn: Database connection
        migrations_dir: Path to migrations directory
        dry_run: If True, only show what would be done

    Returns:
        Dict with results:
        {
            "applied": 3,
            "skipped": 2,
            "migrations": [
                {"version": "001", "name": "...", "status": "applied"},
                ...
            ]
        }
    """
    ensure_migrations_table(conn)

    all_migrations = scan_migration_files(migrations_dir)
    pending = get_pending_migrations(conn, all_migrations)
    applied_count = len(all_migrations) - len(pending)

    results = {
        "applied": 0,
        "skipped": applied_count,
        "migrations": []
    }

    for migration in all_migrations:
        if migration["version"] in get_applied_migrations(conn):
            results["migrations"].append({
                "version": migration["version"],
                "name": migration["name"],
                "status": "skipped"
            })
            continue

        if dry_run:
            results["migrations"].append({
                "version": migration["version"],
                "name": migration["name"],
                "status": "pending"
            })
            continue

        # Apply migration
        apply_migration(conn, migration)
        results["applied"] += 1
        results["migrations"].append({
            "version": migration["version"],
            "name": migration["name"],
            "status": "applied"
        })

    return results
