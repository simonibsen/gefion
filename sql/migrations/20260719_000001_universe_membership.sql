-- Modeling universe membership (spec 015, owner-approved 2026-07-19).
-- Named, rule-defined subsets of the stock population; membership stored in
-- complement form (exclusion intervals). Mirrors sql/schema.sql exactly.

CREATE TABLE IF NOT EXISTS universe_definitions (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    rules       JSONB NOT NULL,                     -- [{name, attribute, op, value, reason}]
    pins        JSONB NOT NULL DEFAULT '[]'::jsonb, -- [{symbol, action, reason}]
    fingerprint TEXT NOT NULL,                      -- sha256 of canonical rules+pins
    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS universe_definitions_default_idx
    ON universe_definitions(is_default) WHERE is_default;

CREATE TABLE IF NOT EXISTS universe_exclusions (
    id            SERIAL PRIMARY KEY,
    universe_id   INTEGER NOT NULL REFERENCES universe_definitions(id) ON DELETE CASCADE,
    data_id       INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    rule_name     TEXT NOT NULL,
    excluded_from DATE NOT NULL,
    excluded_to   DATE,                             -- NULL = open-ended
    refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (universe_id, data_id, rule_name, excluded_from)
);

CREATE INDEX IF NOT EXISTS universe_exclusions_lookup_idx
    ON universe_exclusions(universe_id, data_id);
