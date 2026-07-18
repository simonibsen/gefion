-- Migration: market-function candidate ledger (spec 014, epic #114)
-- Approved: specs/014-generated-market-features/data-model.md, 2026-07-18.
--
-- The waiting room for machine-generated market-scope function bodies.
-- Candidates live HERE, never in feature_functions, so pending/rejected
-- generated code has no execution path by construction — every executor
-- enumerates feature_functions only. Approval promotes into
-- feature_functions atomically; rejection retains the row (audit ledger:
-- supersede/hide, never erase). No FK on promoted_function_id — the ledger
-- survives deletion of the function it promoted (008 findings precedent).
-- UNIQUE (name, version): regeneration creates a new version, never a
-- silent overwrite. Plain relational — tens of rows, no time-series access.
CREATE TABLE IF NOT EXISTS market_function_candidates (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL CHECK (kind IN ('cross_section', 'composite')),
    function_body TEXT NOT NULL,
    inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
    description TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('claude', 'template', 'manual')),
    principle_id TEXT,
    generator TEXT,
    dry_run JSONB,
    review_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_state IN ('pending', 'approved', 'rejected')),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    review_reason TEXT,
    promoted_function_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name, version)
);
