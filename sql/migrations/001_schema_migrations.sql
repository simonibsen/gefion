-- Migration 001: Schema Migrations Tracking Table
--
-- This is a special migration that creates the table used to track
-- which migrations have been applied. This should always be the first
-- migration in the sequence.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id SERIAL PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT NOW(),
    checksum TEXT
);

-- Index for quick lookups of applied migrations
CREATE INDEX IF NOT EXISTS idx_schema_migrations_version
    ON schema_migrations(version);

-- Insert record for this migration itself
INSERT INTO schema_migrations (version, name, checksum)
VALUES ('001', 'schema_migrations', 'baseline')
ON CONFLICT (version) DO NOTHING;
