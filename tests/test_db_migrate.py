"""
Test database migration runner (TDD approach).

These tests define the expected behavior of the gefion db-migrate command.
"""
import os
import tempfile
from pathlib import Path

import psycopg
import pytest

from gefion.db import schema


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


@pytest.fixture(scope="module", autouse=True)
def restore_migration_baseline(conn):
    """Restore real migration records after this module's tests.

    Tests here drop schema_migrations and insert fake records. Without
    restoring the baseline afterwards, any later db-init against the test
    database replays historical migrations on an already-current schema and
    fails on since-dropped tables like quantile_predictions (issue #29).
    """
    yield

    import gefion
    from gefion.db.migrate import baseline_migrations, ensure_migrations_table

    migrations_dir = Path(gefion.__file__).parent.parent.parent / "sql" / "migrations"
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS schema_migrations CASCADE;")
    ensure_migrations_table(conn)
    baseline_migrations(conn, migrations_dir)


@pytest.fixture
def clean_migrations_table(conn):
    """Clean up schema_migrations table before each test."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS schema_migrations CASCADE;")
    yield


def test_schema_migrations_table_creation(conn):
    """Test that schema_migrations table can be created."""
    from gefion.db.migrate import ensure_migrations_table

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
    from gefion.db.migrate import ensure_migrations_table

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


def test_get_applied_migrations_empty(conn, clean_migrations_table):
    """Test getting applied migrations when none exist."""
    from gefion.db.migrate import ensure_migrations_table, get_applied_migrations

    ensure_migrations_table(conn)
    applied = get_applied_migrations(conn)

    assert isinstance(applied, set)
    assert len(applied) == 0


def test_record_migration(conn):
    """Test recording a migration as applied."""
    from gefion.db.migrate import ensure_migrations_table, record_migration, get_applied_migrations

    ensure_migrations_table(conn)
    record_migration(conn, "002", "test_migration", "abc123")

    applied = get_applied_migrations(conn)
    assert "002" in applied


def test_record_migration_idempotent(conn):
    """Test that recording the same migration twice doesn't error."""
    from gefion.db.migrate import ensure_migrations_table, record_migration

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
    from gefion.db.migrate import scan_migration_files

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
    from gefion.db.migrate import scan_migration_files

    # Create files in non-sorted order
    (tmp_path / "003_third.sql").write_text("-- Third")
    (tmp_path / "001_first.sql").write_text("-- First")
    (tmp_path / "002_second.sql").write_text("-- Second")

    migrations = scan_migration_files(tmp_path)

    versions = [m["version"] for m in migrations]
    assert versions == ["001", "002", "003"]


def test_get_pending_migrations(conn, tmp_path, clean_migrations_table):
    """Test identifying which migrations haven't been applied yet."""
    from gefion.db.migrate import (
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
    from gefion.db.migrate import ensure_migrations_table, apply_migration, get_applied_migrations

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
    from gefion.db.migrate import ensure_migrations_table, apply_migration, get_applied_migrations

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


def test_run_migrations_all_pending(conn, tmp_path, clean_migrations_table):
    """Test running all pending migrations."""
    from gefion.db.migrate import ensure_migrations_table, run_migrations

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


def test_run_migrations_some_already_applied(conn, tmp_path, clean_migrations_table):
    """Test running migrations when some are already applied."""
    from gefion.db.migrate import ensure_migrations_table, record_migration, run_migrations

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


def test_run_migrations_idempotent(conn, tmp_path, clean_migrations_table):
    """Test that running migrations twice doesn't cause errors."""
    from gefion.db.migrate import ensure_migrations_table, run_migrations

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


def test_run_migrations_stops_on_error(conn, tmp_path, clean_migrations_table):
    """Test that migration runner stops on first error."""
    from gefion.db.migrate import ensure_migrations_table, run_migrations, get_applied_migrations

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


def test_baseline_migrations_records_without_executing(conn, tmp_path, clean_migrations_table):
    """baseline_migrations records all migrations as applied WITHOUT running them.

    Used by db-init on a fresh database: schema.sql already embodies the final
    state, so historical migrations must be recorded, not replayed (some
    reference tables that no longer exist, e.g. quantile_predictions).
    """
    from gefion.db.migrate import baseline_migrations, ensure_migrations_table, get_applied_migrations

    # This migration would fail if actually executed
    (tmp_path / "001_first.sql").write_text("ALTER TABLE no_such_table ADD COLUMN x INTEGER;")
    (tmp_path / "002_second.sql").write_text("CREATE TABLE IF NOT EXISTS baseline_table2 (id SERIAL);")

    ensure_migrations_table(conn)

    result = baseline_migrations(conn, tmp_path)

    assert result["baselined"] == 2
    applied = get_applied_migrations(conn)
    assert "001" in applied
    assert "002" in applied

    # Verify 002 was NOT executed (recorded only)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'baseline_table2'
            );
        """)
        assert not cur.fetchone()[0], "baseline must record migrations, not execute them"


def test_baseline_migrations_records_checksums(conn, tmp_path, clean_migrations_table):
    """baseline_migrations stores each file's checksum so drift is detectable."""
    from gefion.db.migrate import baseline_migrations, compute_checksum, ensure_migrations_table

    path = tmp_path / "001_first.sql"
    path.write_text("CREATE TABLE IF NOT EXISTS baseline_table1 (id SERIAL);")

    ensure_migrations_table(conn)
    baseline_migrations(conn, tmp_path)

    with conn.cursor() as cur:
        cur.execute("SELECT checksum FROM schema_migrations WHERE version = '001';")
        row = cur.fetchone()

    assert row is not None
    assert row[0] == compute_checksum(path)


def test_baseline_migrations_skips_already_recorded(conn, tmp_path, clean_migrations_table):
    """baseline_migrations is idempotent and skips versions already recorded."""
    from gefion.db.migrate import baseline_migrations, ensure_migrations_table, record_migration

    (tmp_path / "001_first.sql").write_text("SELECT 1;")
    (tmp_path / "002_second.sql").write_text("SELECT 2;")

    ensure_migrations_table(conn)
    record_migration(conn, "001", "first", "hash1")

    result = baseline_migrations(conn, tmp_path)

    assert result["baselined"] == 1
    assert result["skipped"] == 1


# ============================================================================
# TDD Tests for Migration Improvements (#28)
# ============================================================================


class TestParseSchemaChanges:
    """Test SQL parsing for schema changes."""

    def test_parse_create_table(self):
        """Parse CREATE TABLE statements."""
        from gefion.db.migrate import parse_migration_schema_changes

        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        changes = parse_migration_schema_changes(sql)

        assert {"type": "table", "name": "users"} in changes

    def test_parse_create_table_if_not_exists(self):
        """Parse CREATE TABLE IF NOT EXISTS."""
        from gefion.db.migrate import parse_migration_schema_changes

        sql = "CREATE TABLE IF NOT EXISTS stocks (id SERIAL);"
        changes = parse_migration_schema_changes(sql)

        assert {"type": "table", "name": "stocks"} in changes

    def test_parse_alter_table_add_column(self):
        """Parse ALTER TABLE ADD COLUMN."""
        from gefion.db.migrate import parse_migration_schema_changes

        sql = "ALTER TABLE stocks ADD COLUMN sector TEXT;"
        changes = parse_migration_schema_changes(sql)

        assert {"type": "column", "table": "stocks", "name": "sector"} in changes

    def test_parse_alter_table_add_column_if_not_exists(self):
        """Parse ALTER TABLE ADD COLUMN IF NOT EXISTS."""
        from gefion.db.migrate import parse_migration_schema_changes

        sql = "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS industry TEXT;"
        changes = parse_migration_schema_changes(sql)

        assert {"type": "column", "table": "stocks", "name": "industry"} in changes

    def test_parse_create_index(self):
        """Parse CREATE INDEX."""
        from gefion.db.migrate import parse_migration_schema_changes

        sql = "CREATE INDEX idx_stocks_symbol ON stocks(symbol);"
        changes = parse_migration_schema_changes(sql)

        assert {"type": "index", "name": "idx_stocks_symbol"} in changes

    def test_parse_create_unique_index(self):
        """Parse CREATE UNIQUE INDEX."""
        from gefion.db.migrate import parse_migration_schema_changes

        sql = "CREATE UNIQUE INDEX IF NOT EXISTS idx_unique ON users(email);"
        changes = parse_migration_schema_changes(sql)

        assert {"type": "index", "name": "idx_unique"} in changes

    def test_parse_multiple_statements(self):
        """Parse SQL with multiple statements."""
        from gefion.db.migrate import parse_migration_schema_changes

        sql = """
        CREATE TABLE orders (id SERIAL);
        ALTER TABLE orders ADD COLUMN user_id INTEGER;
        CREATE INDEX idx_orders_user ON orders(user_id);
        """
        changes = parse_migration_schema_changes(sql)

        assert len(changes) == 3
        assert {"type": "table", "name": "orders"} in changes
        assert {"type": "column", "table": "orders", "name": "user_id"} in changes
        assert {"type": "index", "name": "idx_orders_user"} in changes


class TestVerifySchemaObjects:
    """Test schema verification against database."""

    def test_verify_existing_table(self, conn):
        """Verify a table that exists."""
        from gefion.db.migrate import verify_schema_objects

        # Create a test table
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS test_verify_table (id SERIAL);")

        changes = [{"type": "table", "name": "test_verify_table"}]
        missing = verify_schema_objects(conn, changes)

        assert len(missing) == 0

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS test_verify_table;")

    def test_verify_missing_table(self, conn):
        """Verify a table that doesn't exist."""
        from gefion.db.migrate import verify_schema_objects

        changes = [{"type": "table", "name": "nonexistent_table_xyz"}]
        missing = verify_schema_objects(conn, changes)

        assert len(missing) == 1
        assert missing[0]["type"] == "table"
        assert missing[0]["name"] == "nonexistent_table_xyz"

    def test_verify_existing_column(self, conn):
        """Verify a column that exists."""
        from gefion.db.migrate import verify_schema_objects

        # Create table with column
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS test_col_table (id SERIAL, name TEXT);")

        changes = [{"type": "column", "table": "test_col_table", "name": "name"}]
        missing = verify_schema_objects(conn, changes)

        assert len(missing) == 0

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS test_col_table;")

    def test_verify_missing_column(self, conn):
        """Verify a column that doesn't exist."""
        from gefion.db.migrate import verify_schema_objects

        # Create table without the column
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS test_col_missing (id SERIAL);")

        changes = [{"type": "column", "table": "test_col_missing", "name": "missing_col"}]
        missing = verify_schema_objects(conn, changes)

        assert len(missing) == 1
        assert missing[0]["type"] == "column"
        assert missing[0]["name"] == "missing_col"

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS test_col_missing;")

    def test_verify_existing_index(self, conn):
        """Verify an index that exists."""
        from gefion.db.migrate import verify_schema_objects

        # Create table with index
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS test_idx_table (id SERIAL);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_test_verify ON test_idx_table(id);")

        changes = [{"type": "index", "name": "idx_test_verify"}]
        missing = verify_schema_objects(conn, changes)

        assert len(missing) == 0

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS test_idx_table;")

    def test_verify_missing_index(self, conn):
        """Verify an index that doesn't exist."""
        from gefion.db.migrate import verify_schema_objects

        changes = [{"type": "index", "name": "idx_nonexistent_xyz"}]
        missing = verify_schema_objects(conn, changes)

        assert len(missing) == 1
        assert missing[0]["type"] == "index"


class TestGetMigrationStatus:
    """Test getting full migration status."""

    def test_get_status_with_applied_and_pending(self, conn, tmp_path, clean_migrations_table):
        """Get status with some applied and some pending."""
        from gefion.db.migrate import (
            ensure_migrations_table,
            record_migration,
            get_migration_status,
        )

        ensure_migrations_table(conn)

        # Create migration files
        (tmp_path / "001_first.sql").write_text("CREATE TABLE first (id SERIAL);")
        (tmp_path / "002_second.sql").write_text("CREATE TABLE second (id SERIAL);")

        # Record first as applied
        record_migration(conn, "001", "first", "abc123")

        status = get_migration_status(conn, tmp_path)

        assert status["total"] == 2
        assert status["applied_count"] == 1
        assert status["pending_count"] == 1
        assert len(status["applied"]) == 1
        assert len(status["pending"]) == 1
        assert status["applied"][0]["version"] == "001"
        assert status["pending"][0]["version"] == "002"


class TestRepairMigration:
    """Test repairing a failed migration."""

    def test_repair_removes_and_reapplies(self, conn, tmp_path, clean_migrations_table):
        """Repair should remove record and re-apply migration."""
        from gefion.db.migrate import (
            ensure_migrations_table,
            record_migration,
            get_applied_migrations,
            repair_migration,
        )

        ensure_migrations_table(conn)

        # Create migration file that creates a table
        (tmp_path / "001_test.sql").write_text(
            "CREATE TABLE IF NOT EXISTS repair_test (id SERIAL);"
        )

        # Record as applied (simulating a failed migration that was recorded)
        record_migration(conn, "001", "test", "old_checksum")

        # Verify it's recorded
        applied = get_applied_migrations(conn)
        assert "001" in applied

        # Repair it
        result = repair_migration(conn, "001", tmp_path)

        assert result["success"] is True
        assert result["version"] == "001"

        # Verify it's still in applied (re-applied)
        applied = get_applied_migrations(conn)
        assert "001" in applied

        # Verify the table was actually created
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'repair_test'
                );
            """)
            exists = cur.fetchone()[0]
        assert exists, "repair_test table should exist after repair"

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS repair_test;")

    def test_repair_nonexistent_version(self, conn, tmp_path, clean_migrations_table):
        """Repair should fail for nonexistent version."""
        from gefion.db.migrate import ensure_migrations_table, repair_migration

        ensure_migrations_table(conn)

        result = repair_migration(conn, "999", tmp_path)

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_repair_not_in_tracking_table(self, conn, tmp_path, clean_migrations_table):
        """Repair a migration that exists as file but not in tracking table."""
        from gefion.db.migrate import (
            ensure_migrations_table,
            get_applied_migrations,
            repair_migration,
        )

        ensure_migrations_table(conn)

        # Create migration file
        (tmp_path / "001_new.sql").write_text(
            "CREATE TABLE IF NOT EXISTS repair_new (id SERIAL);"
        )

        # Don't record it - repair should still work (just applies it)
        result = repair_migration(conn, "001", tmp_path)

        assert result["success"] is True

        # Verify it's now applied
        applied = get_applied_migrations(conn)
        assert "001" in applied

        # Cleanup
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS repair_new;")
