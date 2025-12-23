# Database Migration System

**Date:** 2025-12-22
**Version:** 1.0

## Overview

G2 uses an automated migration system to manage database schema changes across different environments. Migrations are tracked in a `schema_migrations` table, ensuring changes are applied consistently and idempotently.

## Quick Start

### Checking Migration Status

```bash
# Check database health including pending migrations
g2 db-health

# Output:
#   ⚠️  Pending migrations: 2
#       - 005_add_user_preferences
#       - 006_add_indexes
#   Run 'g2 db-migrate' to apply pending migrations
```

### Running Migrations

```bash
# Apply all pending migrations
g2 db-migrate

# Preview pending migrations without applying
g2 db-migrate --dry-run

# Apply migrations to a specific database
g2 db-migrate --db-url postgresql://user:pass@host:5432/db
```

### Automatic Warnings

The system automatically warns about pending migrations:

**Before data operations:**
```bash
g2 data-update
# Output:
# ⚠️  Warning: 2 pending migration(s) detected. Database schema may be out of sync.
#   - 005_add_user_preferences
#   - 006_add_indexes
#   Run 'g2 db-migrate' to apply migrations before proceeding.
```

**During health checks:**
```bash
g2 db-health
# Shows pending migrations in health report
```

### Creating a New Migration

1. Create a new SQL file in `sql/migrations/` with the naming pattern `NNN_description.sql`:
   ```
   005_add_user_preferences.sql
   ```

2. Write your SQL statements:
   ```sql
   -- Migration 005: Add User Preferences

   CREATE TABLE IF NOT EXISTS user_preferences (
       id SERIAL PRIMARY KEY,
       user_id INTEGER NOT NULL,
       preferences JSONB DEFAULT '{}',
       created_at TIMESTAMP DEFAULT NOW()
   );

   CREATE INDEX idx_user_preferences_user_id ON user_preferences(user_id);
   ```

3. Run the migration:
   ```bash
   g2 db-migrate
   ```

## Migration System Design

### Architecture

The migration system consists of three main components:

1. **Migration Tracking Table** (`schema_migrations`)
   - Stores version, name, checksum, and timestamp for each applied migration
   - Created automatically on first run

2. **Migration Files** (`sql/migrations/*.sql`)
   - Numbered SQL files (001, 002, 003, etc.)
   - Applied in sequential order
   - Standard SQL syntax (psql meta-commands like `\echo` are filtered out)

3. **Migration Runner** (`g2 db-migrate` command)
   - Scans migration directory
   - Compares with applied migrations
   - Applies pending migrations in order
   - Records successful migrations

### Key Features

✅ **Idempotent** - Safe to run multiple times
✅ **Ordered** - Migrations apply in sequence (001, 002, 003, ...)
✅ **Tracked** - All migrations recorded in database
✅ **Checksummed** - File integrity verification
✅ **Atomic** - Each migration in a transaction
✅ **Safe** - Failed migrations aren't recorded

## Migration File Format

### Naming Convention

```
NNN_description.sql
```

- `NNN`: Zero-padded number (001, 002, 003, ...)
- `description`: Snake_case description
- Extension: `.sql`

### Example Migration

```sql
-- Migration 005: Add Index for Performance
--
-- Description: Adds index on frequently queried columns
-- to improve query performance.

CREATE INDEX IF NOT EXISTS idx_stocks_exchange
    ON stocks(exchange)
    WHERE exchange IS NOT NULL;
```

### Best Practices

1. **Use IF NOT EXISTS** - Make migrations idempotent
   ```sql
   CREATE TABLE IF NOT EXISTS ...
   CREATE INDEX IF NOT EXISTS ...
   ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...
   ```

2. **Include Comments** - Explain why the migration exists
   ```sql
   -- Migration 006: Fix data integrity issue
   -- Bug: Users could create duplicate entries
   -- Fix: Add unique constraint
   ```

3. **Test Before Committing** - Run on local database first
   ```bash
   g2 db-migrate --dry-run  # Preview
   g2 db-migrate            # Apply
   ```

4. **One Change Per Migration** - Keep migrations focused
   - ✅ Good: `005_add_user_email_index.sql`
   - ❌ Bad: `005_add_indexes_and_fix_constraints_and_update_data.sql`

5. **Avoid psql Meta-Commands** - Use standard SQL only
   - ✅ Good: SQL statements only
   - ❌ Bad: `\echo`, `\set`, `\timing`

## Deployment Workflow

### For New Databases

```bash
# 1. Initialize schema
g2 db-init

# 2. Run migrations (if any post-baseline migrations exist)
g2 db-migrate

# 3. Enable compression (optional but recommended)
g2 db-tune --compress-after-days 30
```

### For Existing Databases

```bash
# 1. Preview pending migrations
g2 db-migrate --dry-run

# 2. Backup database (recommended)
pg_dump -h host -U user -d database > backup.sql

# 3. Apply migrations
g2 db-migrate

# 4. Verify application
# Test your application to ensure everything works
```

### CI/CD Integration

```yaml
# Example GitHub Actions workflow
deploy:
  steps:
    - name: Run database migrations
      run: |
        g2 db-migrate --db-url ${{ secrets.DATABASE_URL }}
```

## Troubleshooting

### Migration Failed

If a migration fails:

1. **Check the error message** - Indicates what went wrong
2. **Migration is NOT recorded** - Failed migrations won't be marked as applied
3. **Fix the migration file** - Correct the SQL syntax or logic
4. **Run again** - `g2 db-migrate` will retry the failed migration

### Migration Skipped Unexpectedly

If a migration was skipped but you expected it to run:

```bash
# Check what's recorded
psql -d g2 -c "SELECT * FROM schema_migrations ORDER BY version;"

# Check migration files
ls -la sql/migrations/
```

### Reset Migration State (Development Only)

⚠️ **WARNING: Only for development databases**

```sql
-- Drop migration tracking (will re-run all migrations)
DROP TABLE schema_migrations;

-- Then run migrations again
g2 db-migrate
```

## Testing

The migration system includes comprehensive tests:

```bash
# Run migration tests
ENABLE_DB_TESTS=1 python -m pytest tests/test_db_migrate.py -v
```

Test coverage includes:
- ✅ Migration table creation
- ✅ Migration scanning and ordering
- ✅ Idempotency (safe to re-run)
- ✅ Partial application (some already applied)
- ✅ Error handling (failed migrations)
- ✅ Checksum verification

## Migration History

Current migrations:

| Version | Name | Description |
|---------|------|-------------|
| 001 | schema_migrations | Migration tracking table |
| 002 | cross_sectional_features | Cross-sectional feature support |
| 003 | plural_source_columns | Multiple source column support |
| 004 | performance_optimizations | Index on feature_definitions |

## Advanced Usage

### Custom Migration Directory

```bash
g2 db-migrate --migrations-dir /path/to/custom/migrations
```

### JSON Output

```bash
g2 db-migrate --json
```

Output:
```json
{
  "applied": 2,
  "skipped": 1,
  "migrations": [
    {"version": "001", "name": "...", "status": "skipped"},
    {"version": "002", "name": "...", "status": "applied"}
  ]
}
```

## Schema: schema_migrations Table

```sql
CREATE TABLE schema_migrations (
    id SERIAL PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT NOW(),
    checksum TEXT
);
```

**Columns:**
- `id`: Auto-increment primary key
- `version`: Migration number (e.g., "001", "002")
- `name`: Migration name from filename
- `applied_at`: Timestamp when migration was applied
- `checksum`: SHA256 hash of migration file

## Files

```
sql/
├── schema.sql                    # Base schema (for new databases)
└── migrations/                   # Migration files
    ├── 001_schema_migrations.sql # Migration tracking
    ├── 002_*.sql                 # Feature migrations
    ├── 003_*.sql
    └── 004_*.sql

src/g2/
├── db/
│   └── migrate.py                # Migration logic
└── cli.py                        # db-migrate command

tests/
└── test_db_migrate.py            # Migration tests (TDD)
```

## FAQ

**Q: Can I modify an applied migration?**
A: No, once applied, migrations should be considered immutable. Create a new migration instead.

**Q: Can I skip a migration?**
A: Not recommended. Migrations must be applied in order. If you need to skip, you'd have to manually record it in `schema_migrations`.

**Q: What happens if two developers create migration 005?**
A: This is a merge conflict. One developer needs to renumber their migration to 006.

**Q: Should I commit migration files?**
A: Yes! Migration files are part of your codebase and should be version controlled.

**Q: Can I run migrations in parallel?**
A: No, migrations run sequentially to maintain order and dependencies.

## See Also

- [Performance Optimizations](PERFORMANCE_OPTIMIZATIONS.md)
- [Database Schema](../sql/schema.sql)
- [Migration Source Code](../src/g2/db/migrate.py)
