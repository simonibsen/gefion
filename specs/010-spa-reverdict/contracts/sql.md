# SQL Contract — SPA Re-Verdict (010)

**Status: APPROVED by owner 2026-07-09** (scope: exactly this one table and
its index; future schema changes require separate approval). Applied at
implementation via the two-file rule, gated behind schema tests written first,
with the data dictionary regenerated in the same commit.

One new table. No changes to any existing table, column, or constraint.

```sql
-- SPA re-verdict results (spec 010): one row per execution of
-- `regime discover spa <run>` — append-only history; "latest" by created_at.
-- Cascades with its run: this is derived analysis OF the run (re-runnable at
-- will from the ledger), unlike the candidate ledger itself, which is the
-- audit trail and must survive artifacts. Deliberate, declared deletion story
-- per the add-a-table checklist.
CREATE TABLE IF NOT EXISTS spa_reverdicts (
    id            SERIAL PRIMARY KEY,
    run_id        INTEGER NOT NULL
                  REFERENCES regime_discovery_runs(id) ON DELETE CASCADE,
    p_consistent  DOUBLE PRECISION NOT NULL,   -- the verdict (Hansen consistent null)
    p_lower       DOUBLE PRECISION NOT NULL,   -- RC-like bracket (diagnostic)
    p_upper       DOUBLE PRECISION NOT NULL,   -- fully-centered bracket (diagnostic)
    level         DOUBLE PRECISION NOT NULL,   -- pass/fail level (default: run's FDR rate)
    passed        BOOLEAN NOT NULL,            -- p_consistent > level
    iterations    INTEGER NOT NULL,            -- bootstrap B
    seed          BIGINT NOT NULL,
    block_length  DOUBLE PRECISION NOT NULL,   -- expected block length (auto or override)
    family_size   INTEGER NOT NULL,            -- must equal the run's BH family_size
    verification  JSONB NOT NULL,              -- {units, max_abs_divergence, all_match}
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS spa_reverdicts_run_created_idx
    ON spa_reverdicts (run_id, created_at DESC);
```

Notes:
- **Append-only**: no UNIQUE on run_id — re-runs (new seed/iterations) add
  rows; nothing is ever updated or deleted by the command.
- **A row implies verification passed** — reconstruction mismatch refuses
  before any insert, so drifted-world verdicts cannot exist in this table.
- **Two-file rule at implementation**: `sql/schema.sql` + migration
  `2026MMDD_NNNNNN_spa_reverdicts.sql`, gated behind schema tests written
  first, data dictionary regenerated in the same commit (TABLE_PURPOSE entry;
  layer: audit/ops beside the discovery ledgers, outside the feeds graph).
- No hypertable (rows ≈ a handful per run), no trigger, no view.
