# SQL Contract — Agentic Regime Discovery (006)

**Status: APPROVED by owner 2026-07-07** (scope: exactly the four tables below, with the five
confirmed decisions — relational not hypertables, CASCADE from runs, JSONB shapes, the
grades uniqueness with descriptive separation, CHECK-constraint enums). Approval covers only
this change; future schema changes require separate approval.

At implementation: two-file rule (`schema.sql` + migration), gated behind a schema test as
the first foundational task, with docs/DATA_DICTIONARY.md regenerated in the same change
(the pre-push drift check enforces this).

```sql
CREATE TABLE IF NOT EXISTS regime_discovery_runs (
    id                SERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    seed              BIGINT NOT NULL,
    search_space      JSONB NOT NULL,     -- pre-registration: atoms, K, budgets, seams, tiers
    segregation       JSONB NOT NULL,     -- outer holdout, inner window, reserve block
    reserve_consumed  BOOLEAN NOT NULL DEFAULT FALSE,
    family_size       INTEGER,            -- realized FDR denominator
    status            TEXT NOT NULL DEFAULT 'pre_registered'
                      CHECK (status IN ('pre_registered','enumerated','evaluated','complete','invalid')),
    dataset_version   TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ
);

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

CREATE TABLE IF NOT EXISTS discovery_diagnostics (
    id                SERIAL PRIMARY KEY,
    run_id            INTEGER NOT NULL REFERENCES regime_discovery_runs(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL,
    detail            JSONB NOT NULL,
    sample_dependent  BOOLEAN NOT NULL,
    dataset_version   TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS regime_trust_grades (
    id                SERIAL PRIMARY KEY,
    candidate_id      INTEGER NOT NULL REFERENCES regime_candidates(id) ON DELETE CASCADE,
    fold              INTEGER NOT NULL,
    confirmed         BOOLEAN NOT NULL,
    descriptive       BOOLEAN NOT NULL DEFAULT FALSE,  -- backward slices: display-only
    detail            JSONB,
    graded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (candidate_id, fold, descriptive)
);
```

Notes: relational tables (not hypertables — low cardinality, no time partitioning need);
parameterized SQL + `Json()` in application code; admitted candidates additionally upsert
into `regime_definitions` (`origin='machine'`) — no schema change needed there.
