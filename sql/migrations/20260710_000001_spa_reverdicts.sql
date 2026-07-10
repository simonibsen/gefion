-- Migration: SPA re-verdict results (spec 010)
-- Approved: specs/010-spa-reverdict/contracts/sql.md, 2026-07-09.
--
-- One row per execution of `regime discover spa <run>` — append-only history
-- ("latest" by created_at; re-runs add rows, nothing is updated or deleted).
-- Cascades with its run: this is derived analysis OF the run, re-runnable at
-- will from the ledger — unlike the candidate ledger, which is the audit
-- trail and must survive artifacts. A recorded row always implies the
-- reconstruction verification passed (drift refuses before any insert).
CREATE TABLE IF NOT EXISTS spa_reverdicts (
    id            SERIAL PRIMARY KEY,
    run_id        INTEGER NOT NULL
                  REFERENCES regime_discovery_runs(id) ON DELETE CASCADE,
    p_consistent  DOUBLE PRECISION NOT NULL,
    p_lower       DOUBLE PRECISION NOT NULL,
    p_upper       DOUBLE PRECISION NOT NULL,
    level         DOUBLE PRECISION NOT NULL,
    passed        BOOLEAN NOT NULL,
    iterations    INTEGER NOT NULL,
    seed          BIGINT NOT NULL,
    block_length  DOUBLE PRECISION NOT NULL,
    family_size   INTEGER NOT NULL,
    verification  JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS spa_reverdicts_run_created_idx
    ON spa_reverdicts (run_id, created_at DESC);
