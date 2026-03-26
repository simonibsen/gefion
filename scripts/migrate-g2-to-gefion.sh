#!/bin/bash
# migrate-g2-to-gefion.sh
#
# Migration script for existing g2 deployments to transition to gefion.
# Run this AFTER updating to the new code.
#
# What it does:
#   1. Migrates ~/.g2/ config directory to ~/.gefion/
#   2. Renames the PostgreSQL database (g2 → gefion, g2_test → gefion_test)
#   3. Updates Docker containers
#   4. Verifies the migration
#
# Usage:
#   ./scripts/migrate-g2-to-gefion.sh [--dry-run]

set -euo pipefail

# Load .env if it exists
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

# Set PGPASSWORD so psql doesn't prompt
export PGPASSWORD="${POSTGRES_PASSWORD:-g2pass}"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "=== DRY RUN MODE — no changes will be made ==="
    echo ""
fi

OLD_DB="g2"
NEW_DB="gefion"
OLD_DB_USER="g2"
NEW_DB_USER="gefion"
OLD_DB_PASS="g2pass"
NEW_DB_PASS="gefionpass"
OLD_CONFIG_DIR="$HOME/.g2"
NEW_CONFIG_DIR="$HOME/.gefion"
DB_PORT="${DB_PORT:-6432}"

echo "=== g2 → Gefion Migration ==="
echo ""

# --- Step 1: Config directory ---
echo "Step 1: Migrate config directory"
if [[ -d "$OLD_CONFIG_DIR" ]]; then
    echo "  Found $OLD_CONFIG_DIR"
    if [[ -d "$NEW_CONFIG_DIR" ]]; then
        echo "  WARNING: $NEW_CONFIG_DIR already exists — merging files"
    fi
    if [[ "$DRY_RUN" == "false" ]]; then
        mkdir -p "$NEW_CONFIG_DIR"
        # Copy files, don't overwrite existing
        for f in "$OLD_CONFIG_DIR"/*; do
            base=$(basename "$f")
            if [[ ! -e "$NEW_CONFIG_DIR/$base" ]]; then
                cp -r "$f" "$NEW_CONFIG_DIR/$base"
                echo "  Copied: $base"
            else
                echo "  Skipped (exists): $base"
            fi
        done
        echo "  Old directory preserved at $OLD_CONFIG_DIR (remove manually when ready)"
    else
        echo "  Would copy files from $OLD_CONFIG_DIR to $NEW_CONFIG_DIR"
    fi
else
    echo "  No $OLD_CONFIG_DIR found — skipping"
fi
echo ""

# --- Step 2: Database rename ---
echo "Step 2: Rename database"

# Check if postgres is reachable
if pg_isready -h localhost -p "$DB_PORT" -q 2>/dev/null; then
    echo "  PostgreSQL is running on port $DB_PORT"

    # Check if old database exists
    OLD_EXISTS=$(psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$OLD_DB'" 2>/dev/null || echo "0")
    NEW_EXISTS=$(psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$NEW_DB'" 2>/dev/null || echo "0")

    if [[ "$OLD_EXISTS" == "1" && "$NEW_EXISTS" != "1" ]]; then
        echo "  Database '$OLD_DB' exists, '$NEW_DB' does not — renaming"
        if [[ "$DRY_RUN" == "false" ]]; then
            # Terminate connections to old DB
            psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -c \
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$OLD_DB' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
            # Rename database
            psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -c \
                "ALTER DATABASE $OLD_DB RENAME TO $NEW_DB;" 2>&1
            echo "  Renamed: $OLD_DB → $NEW_DB"
        else
            echo "  Would rename: $OLD_DB → $NEW_DB"
        fi
    elif [[ "$NEW_EXISTS" == "1" ]]; then
        echo "  Database '$NEW_DB' already exists — skipping rename"
    elif [[ "$OLD_EXISTS" != "1" ]]; then
        echo "  Database '$OLD_DB' not found — will be created by 'gefion init'"
    fi

    # Handle test database
    OLD_TEST_EXISTS=$(psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${OLD_DB}_test'" 2>/dev/null || echo "0")
    NEW_TEST_EXISTS=$(psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${NEW_DB}_test'" 2>/dev/null || echo "0")

    if [[ "$OLD_TEST_EXISTS" == "1" && "$NEW_TEST_EXISTS" != "1" ]]; then
        echo "  Test database '${OLD_DB}_test' exists — renaming"
        if [[ "$DRY_RUN" == "false" ]]; then
            psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -c \
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${OLD_DB}_test' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
            psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -c \
                "ALTER DATABASE ${OLD_DB}_test RENAME TO ${NEW_DB}_test;" 2>&1
            echo "  Renamed: ${OLD_DB}_test → ${NEW_DB}_test"
        else
            echo "  Would rename: ${OLD_DB}_test → ${NEW_DB}_test"
        fi
    fi

    # Rename user if needed
    USER_EXISTS=$(psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$NEW_DB_USER'" 2>/dev/null || echo "0")
    if [[ "$USER_EXISTS" != "1" ]]; then
        echo "  Renaming DB user: $OLD_DB_USER → $NEW_DB_USER"
        if [[ "$DRY_RUN" == "false" ]]; then
            psql -h localhost -p "$DB_PORT" -U "$OLD_DB_USER" -d postgres -c \
                "ALTER ROLE $OLD_DB_USER RENAME TO $NEW_DB_USER;" 2>&1
            psql -h localhost -p "$DB_PORT" -U "$NEW_DB_USER" -d postgres -c \
                "ALTER ROLE $NEW_DB_USER WITH PASSWORD '$NEW_DB_PASS';" 2>&1
            echo "  User renamed and password updated"
        else
            echo "  Would rename user and update password"
        fi
    else
        echo "  User '$NEW_DB_USER' already exists — skipping"
    fi
else
    echo "  PostgreSQL not running — skipping database migration"
    echo "  Run 'gefion init' after starting services to create the database"
fi
echo ""

# --- Step 3: Docker containers ---
echo "Step 3: Docker containers"
echo "  Stop old containers and start new ones:"
echo "    docker compose down"
echo "    docker compose up -d"
echo "  Container names will change from g2-* to gefion-*"
if [[ "$DRY_RUN" == "false" ]]; then
    echo "  (Run these commands manually — not done automatically for safety)"
fi
echo ""

# --- Step 4: Verify ---
echo "Step 4: Verification"
if [[ "$DRY_RUN" == "false" ]]; then
    echo "  Run: gefion init"
    echo "  Run: gefion health"
    echo "  Run: gefion ui  (verify UI loads)"
else
    echo "  Would verify: gefion init, gefion health, gefion ui"
fi
echo ""

echo "=== Migration complete ==="
echo ""
echo "Post-migration cleanup (when ready):"
echo "  rm -rf $OLD_CONFIG_DIR    # Remove old config directory"
echo "  # Old 'g2' CLI command still works as an alias"
