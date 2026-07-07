-- Migration: Agentic Regime Discovery (spec 006)
--
-- Adds the discovery bookkeeping tables that make autonomous regime search
-- honest. Owner-approved 2026-07-07 (contracts/sql.md).
--
-- Changes:
--   1. New table: regime_discovery_runs (pre-registration: seed, search space,
--      segregation boundaries, reserve consumption, realized FDR family size,
--      status lifecycle pre_registered -> enumerated -> evaluated -> complete/invalid)
--   2. New table: regime_candidates (the candidate ledger — every candidate
--      evaluated, including losers, with tier, provenance, per-test results,
--      counted-in-family flag, and verdict)
--   3. New table: discovery_diagnostics (limits hit: budget/depth exhaustion,
--      min-sample refusals, uncomputable proposals — tagged sample-dependent
--      vs structural with quantitative reasons)
--   4. New table: regime_trust_grades (walk-forward trust accrual per admitted
--      candidate; descriptive backward slices display-only, never graded)
--
-- Relational tables, not hypertables: low cardinality, no time partitioning.
-- Mirrors the canonical DDL in sql/schema.sql (two-file rule).

-- =============================================================================
-- 1. DISCOVERY RUNS (pre-registration)
-- =============================================================================

CREATE TABLE IF NOT EXISTS regime_discovery_runs (
    id                SERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    seed              BIGINT NOT NULL,
    search_space      JSONB NOT NULL,
    segregation       JSONB NOT NULL,
    reserve_consumed  BOOLEAN NOT NULL DEFAULT FALSE,
    family_size       INTEGER,
    status            TEXT NOT NULL DEFAULT 'pre_registered'
                      CHECK (status IN ('pre_registered','enumerated','evaluated','complete','invalid')),
    dataset_version   TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ
);

-- =============================================================================
-- 2. CANDIDATE LEDGER
-- =============================================================================

CREATE TABLE IF NOT EXISTS regime_candidates (
    id                SERIAL PRIMARY KEY,
    run_id            INTEGER NOT NULL REFERENCES regime_discovery_runs(id) ON DELETE CASCADE,
    candidate_hash    TEXT NOT NULL,
    expression        JSONB NOT NULL,
    tier              TEXT NOT NULL CHECK (tier IN ('interaction','grammar','expressive')),
    provenance        JSONB,
    results           JSONB,
    counted_in_family BOOLEAN NOT NULL DEFAULT TRUE,
    verdict           TEXT CHECK (verdict IN
        ('admitted','rejected','refused_low_power','refused_degenerate','refused_unstable')),
    UNIQUE (run_id, candidate_hash)
);
CREATE INDEX IF NOT EXISTS idx_regime_candidates_run ON regime_candidates(run_id);

-- =============================================================================
-- 3. DIAGNOSTICS LEDGER
-- =============================================================================

CREATE TABLE IF NOT EXISTS discovery_diagnostics (
    id                SERIAL PRIMARY KEY,
    run_id            INTEGER NOT NULL REFERENCES regime_discovery_runs(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL,
    detail            JSONB NOT NULL,
    sample_dependent  BOOLEAN NOT NULL,
    dataset_version   TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 4. TRUST GRADES (forward-accruing)
-- =============================================================================

CREATE TABLE IF NOT EXISTS regime_trust_grades (
    id                SERIAL PRIMARY KEY,
    candidate_id      INTEGER NOT NULL REFERENCES regime_candidates(id) ON DELETE CASCADE,
    fold              INTEGER NOT NULL,
    confirmed         BOOLEAN NOT NULL,
    descriptive       BOOLEAN NOT NULL DEFAULT FALSE,
    detail            JSONB,
    graded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (candidate_id, fold, descriptive)
);
