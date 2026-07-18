# Data Model: Generated Market-Level Features with an Owner Gate

## Proposed DDL — REQUIRES OWNER APPROVAL (Schema Governance)

One new table. No changes to existing tables. Two-file rule applies:
`sql/schema.sql` gains the CREATE, and
`sql/migrations/20260718_000001_market_function_candidates.sql` carries the
incremental change. **Not executed until the owner approves this exact DDL.**

```sql
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
```

Notes:
- No FK on `promoted_function_id` — candidates are an audit ledger and must
  survive function deletion (house pattern: `data_quality_findings`).
- `UNIQUE (name, version)`: regenerating from the same principle creates a
  new version, never a silent overwrite (spec edge case).
- `dry_run` JSONB: `{"ok": bool, "sample": [{"date": ..., "value": ...}],
  "error": str | null, "seed": int, "ran_at": iso}`.
- Not a hypertable (tens of rows, no time-series access pattern).

## Entities

### MarketFunctionCandidate (`market_function_candidates`)

| Field | Meaning | Rules |
|---|---|---|
| name, version | candidate identity | unique pair; version bumps on regeneration |
| kind | `cross_section` \| `composite` | fixes the contract the body must satisfy and the dry-run input shape |
| function_body | the generated Python body | must define `compute`; only ever executed in the sandbox |
| inputs | declared inputs | cross_section: `{"features": [...]}` (may be empty); composite: `{"series": [...]}` (non-empty, all series must exist + be enabled at promotion) |
| origin / principle_id / generator | provenance | origin `claude`/`template`/`manual`; recorded at creation, immutable |
| dry_run | stored dry-run record | refreshed on re-run at review; `ok=false` blocks approval |
| review_state | `pending` → `approved` \| `rejected` | terminal states; no transition out of rejected (a fix is a NEW version) |
| reviewed_by / reviewed_at / review_reason | the decision | reason REQUIRED on rejection; all immutable once set |
| promoted_function_id | audit link to `feature_functions` | set only on approval |

**State transitions**:

```
pending ──approve (human, dry_run.ok)──▶ approved ──▶ [promotion: feature_functions row + feature_definitions pairing]
pending ──reject (human, reason)──────▶ rejected  (terminal; retained)
```

Approval and promotion are one atomic act — an `approved` candidate row
without its promoted function is a bug, not a state.

### Composite market function (existing `feature_functions`, no DDL)

- `scope='market'`, `inputs={"series": [...]}` — the input shape IS the
  discriminator (research R2).
- Output series: one `feature_definitions` row under the macro home
  (entity_table `macro_series`), same as every derived series (007/011).
- Cycle rule: the dependency graph (function output name → declared input
  series names, recursing through composite-produced series) must be acyclic
  — enforced at registration and at promotion.

### Derive ordering (behavioral, no schema)

`macro derive --series all`: non-composite market functions first, then
composites in topological order of their dependency graph, so same-night
inputs are fresh before composites read them.
