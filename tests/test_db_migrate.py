"""
Test database migration runner (TDD approach).

These tests define the expected behavior of the g2 db-migrate command.
"""
import os
import tempfile
from pathlib import Path

import psycopg
import pytest

from g2.db import schema


def create_connection():
    if os.getenv("ENABLE_DB_TESTS", "0") != "1":
        pytest.skip("DB tests disabled (set ENABLE_DB_TESTS=1 to enable)")
    try:
        return psycopg.connect(schema.test_db_url())
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB not available: {exc}")


@pytest.fixture(scope="module")
def conn():
    connection = create_connection()
    connection.autocommit = True
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def clean_migrations_table(conn):
    """Clean up schema_migrations table before each test."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS schema_migrations CASCADE;")
    yield


def test_schema_migrations_table_creation(conn):
    """Test that schema_migrations table can be created."""
    from g2.db.migrate import ensure_migrations_table

    ensure_migrations_table(conn)

    # Verify table exists
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'schema_migrations'
            );
        """)
        exists = cur.fetchone()[0]

    assert exists, "schema_migrations table should exist"


def test_schema_migrations_table_structure(conn):
    """Test that schema_migrations table has correct columns."""
    from g2.db.migrate import ensure_migrations_table

    ensure_migrations_table(conn)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'schema_migrations'
            ORDER BY ordinal_position;
        """)
        columns = {row[0]: row[1] for row in cur.fetchall()}

    assert 'id' in columns
    assert 'version' in columns
    assert 'name' in columns
    assert 'applied_at' in columns
    assert 'checksum' in columns


def test_get_applied_migrations_empty(conn):
    """Test getting applied migrations when none exist."""
    from g2.db.migrate import ensure_migrations_table, get_applied_migrations

    ensure_migrations_table(conn)
    applied = get_applied_migrations(conn)

    assert isinstance(applied, set)
    assert len(applied) == 0


def test_record_migration(conn):
    """Test recording a migration as applied."""
    from g2.db.migrate import ensure_migrations_table, record_migration, get_applied_migrations

    ensure_migrations_table(conn)
    record_migration(conn, "002", "test_migration", "abc123")

    applied = get_applied_migrations(conn)
    assert "002" in applied


def test_record_migration_idempotent(conn):
    """Test that recording the same migration twice doesn't error."""
    from g2.db.migrate import ensure_migrations_table, record_migration

    ensure_migrations_table(conn)
    record_migration(conn, "002", "test_migration", "abc123")
    record_migration(conn, "002", "test_migration", "abc123")  # Should not error

    # Verify only one record
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = '002';")
        count = cur.fetchone()[0]

    assert count == 1


def test_scan_migration_files(tmp_path):
    """Test scanning migration directory for SQL files."""
    from g2.db.migrate import scan_migration_files

    # Create test migration files
    (tmp_path / "001_first.sql").write_text("-- First migration")
    (tmp_path / "002_second.sql").write_text("-- Second migration")
    (tmp_path / "003_third.sql").write_text("-- Third migration")
    (tmp_path / "readme.txt").write_text("Not a migration")

    migrations = scan_migration_files(tmp_path)

    assert len(migrations) == 3
    assert migrations[0]["version"] == "001"
    assert migrations[0]["name"] == "first"
    assert migrations[1]["version"] == "002"
    assert migrations[2]["version"] == "003"


def test_scan_migration_files_sorted(tmp_path):
    """Test that migrations are returned in sorted order."""
    from g2.db.migrate import scan_migration_files

    # Create files in non-sorted order
    (tmp_path / "003_third.sql").write_text("-- Third")
    (tmp_path / "001_first.sql").write_text("-- First")
    (tmp_path / "002_second.sql").write_text("-- Second")

    migrations = scan_migration_files(tmp_path)

    versions = [m["version"] for m in migrations]
    assert versions == ["001", "002", "003"]


def test_get_pending_migrations(conn, tmp_path):
    """Test identifying which migrations haven't been applied yet."""
    from g2.db.migrate import (
        ensure_migrations_table,
        record_migration,
        scan_migration_files,
        get_pending_migrations
    )

    # Create test migrations
    (tmp_path / "001_first.sql").write_text("-- First")
    (tmp_path / "002_second.sql").write_text("-- Second")
    (tmp_path / "003_third.sql").write_text("-- Third")

    ensure_migrations_table(conn)

    # Record that 001 and 002 have been applied
    record_migration(conn, "001", "first", "hash1")
    record_migration(conn, "002", "second", "hash2")

    all_migrations = scan_migration_files(tmp_path)
    pending = get_pending_migrations(conn, all_migrations)

    assert len(pending) == 1
    assert pending[0]["version"] == "003"
    assert pending[0]["name"] == "third"


def test_apply_migration(conn, tmp_path):
    """Test applying a single migration."""
    from g2.db.migrate import ensure_migrations_table, apply_migration, get_applied_migrations

    # Create a test migration that creates a table
    migration_sql = """
    CREATE TABLE IF NOT EXISTS test_table (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL
    );
    """
    migration_file = tmp_path / "002_create_test_table.sql"
    migration_file.write_text(migration_sql)

    ensure_migrations_table(conn)

    migration = {
        "version": "002",
        "name": "create_test_table",
        "path": migration_file
    }

    apply_migration(conn, migration)

    # Verify table was created
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'test_table'
            );
        """)
        exists = cur.fetchone()[0]

    assert exists, "Migration should have created test_table"

    # Verify migration was recorded
    applied = get_applied_migrations(conn)
    assert "002" in applied


def test_apply_migration_with_error(conn, tmp_path):
    """Test that failed migrations don't get recorded."""
    from g2.db.migrate import ensure_migrations_table, apply_migration, get_applied_migrations

    # Create a migration with invalid SQL
    migration_sql = "INVALID SQL THAT WILL FAIL;"
    migration_file = tmp_path / "999_bad_migration.sql"
    migration_file.write_text(migration_sql)

    ensure_migrations_table(conn)

    migration = {
        "version": "999",
        "name": "bad_migration",
        "path": migration_file
    }

    # Migration should raise an exception
    with pytest.raises(Exception):
        apply_migration(conn, migration)

    # Verify migration was NOT recorded
    applied = get_applied_migrations(conn)
    assert "999" not in applied


def test_run_migrations_all_pending(conn, tmp_path):
    """Test running all pending migrations."""
    from g2.db.migrate import ensure_migrations_table, run_migrations

    # Create multiple test migrations
    (tmp_path / "001_first.sql").write_text("CREATE TABLE IF NOT EXISTS table1 (id SERIAL);")
    (tmp_path / "002_second.sql").write_text("CREATE TABLE IF NOT EXISTS table2 (id SERIAL);")
    (tmp_path / "003_third.sql").write_text("CREATE TABLE IF NOT EXISTS table3 (id SERIAL);")

    ensure_migrations_table(conn)

    result = run_migrations(conn, tmp_path)

    assert result["applied"] == 3
    assert result["skipped"] == 0
    assert len(result["migrations"]) == 3

    # Verify all tables were created
    with conn.cursor() as cur:
        for table in ["table1", "table2", "table3"]:
            cur.execute(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = '{table}'
                );
            """)
            exists = cur.fetchone()[0]
            assert exists, f"{table} should exist"


def test_run_migrations_some_already_applied(conn, tmp_path):
    """Test running migrations when some are already applied."""
    from g2.db.migrate import ensure_migrations_table, record_migration, run_migrations

    (tmp_path / "001_first.sql").write_text("CREATE TABLE IF NOT EXISTS table1 (id SERIAL);")
    (tmp_path / "002_second.sql").write_text("CREATE TABLE IF NOT EXISTS table2 (id SERIAL);")
    (tmp_path / "003_third.sql").write_text("CREATE TABLE IF NOT EXISTS table3 (id SERIAL);")

    ensure_migrations_table(conn)

    # Manually record that 001 was already applied
    record_migration(conn, "001", "first", "hash1")

    result = run_migrations(conn, tmp_path)

    assert result["applied"] == 2  # Only 002 and 003
    assert result["skipped"] == 1  # 001 was skipped

    # Verify table2 and table3 were created
    with conn.cursor() as cur:
        for table in ["table2", "table3"]:
            cur.execute(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = '{table}'
                );
            """)
            exists = cur.fetchone()[0]
            assert exists, f"{table} should exist"


def test_run_migrations_idempotent(conn, tmp_path):
    """Test that running migrations twice doesn't cause errors."""
    from g2.db.migrate import ensure_migrations_table, run_migrations

    (tmp_path / "001_first.sql").write_text("CREATE TABLE IF NOT EXISTS table1 (id SERIAL);")
    (tmp_path / "002_second.sql").write_text("CREATE TABLE IF NOT EXISTS table2 (id SERIAL);")

    ensure_migrations_table(conn)

    # Run migrations first time
    result1 = run_migrations(conn, tmp_path)
    assert result1["applied"] == 2

    # Run migrations second time - should skip all
    result2 = run_migrations(conn, tmp_path)
    assert result2["applied"] == 0
    assert result2["skipped"] == 2


def test_run_migrations_stops_on_error(conn, tmp_path):
    """Test that migration runner stops on first error."""
    from g2.db.migrate import ensure_migrations_table, run_migrations, get_applied_migrations

    (tmp_path / "001_first.sql").write_text("CREATE TABLE IF NOT EXISTS table1 (id SERIAL);")
    (tmp_path / "002_bad.sql").write_text("INVALID SQL;")
    (tmp_path / "003_third.sql").write_text("CREATE TABLE IF NOT EXISTS table3 (id SERIAL);")

    ensure_migrations_table(conn)

    # Should raise exception on bad migration
    with pytest.raises(Exception):
        run_migrations(conn, tmp_path)

    # Verify only 001 was applied, 003 was not
    applied = get_applied_migrations(conn)
    assert "001" in applied
    assert "002" not in applied
    assert "003" not in applied
