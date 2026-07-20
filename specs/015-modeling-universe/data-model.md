# Data Model: Modeling Universe Membership (015)

## New tables (owner approval required before touching sql/schema.sql)

### universe_definitions

Mirrors the `regime_definitions` idiom: named, versioned-by-fingerprint,
enabled/disabled definition objects.

```sql
CREATE TABLE IF NOT EXISTS universe_definitions (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    rules       JSONB NOT NULL,                    -- [{name, attribute, op, value, reason}]
    pins        JSONB NOT NULL DEFAULT '[]'::jsonb, -- [{symbol, action: include|exclude, reason}]
    fingerprint TEXT NOT NULL,                     -- sha256 of canonical rules+pins JSON
    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- at most one default universe
CREATE UNIQUE INDEX IF NOT EXISTS universe_definitions_default_idx
    ON universe_definitions(is_default) WHERE is_default;
```

Validation (application layer, at define/update time):
- `name`: kebab/snake identifier; reserved name `all` refused.
- `rules[*].attribute` ∈ attribute registry; `rules[*].op` ∈
  {eq, ne, in, gte, lte, between, is_missing}; value type checked against op.
- `rules[*].name` unique within the definition; `reason` mandatory.
- `pins[*].action` ∈ {include, exclude}; `reason` mandatory.
- fingerprint recomputed on every write; changes iff rules/pins change.

### universe_exclusions (membership in complement form)

A symbol is a member of universe U as of date D iff **no row** here covers
(U, symbol, D). Static rules ⇒ one open-ended row per excluded symbol.

```sql
CREATE TABLE IF NOT EXISTS universe_exclusions (
    id            SERIAL PRIMARY KEY,
    universe_id   INTEGER NOT NULL REFERENCES universe_definitions(id) ON DELETE CASCADE,
    data_id       INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    rule_name     TEXT NOT NULL,                   -- rule or pin that caused this interval
    excluded_from DATE NOT NULL,
    excluded_to   DATE,                            -- NULL = open-ended
    refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (universe_id, data_id, rule_name, excluded_from)
);

CREATE INDEX IF NOT EXISTS universe_exclusions_lookup_idx
    ON universe_exclusions(universe_id, data_id);
```

State transitions (refresh only — no manual DML):
- Refresh recomputes the interval set per rule and reconciles: unchanged
  intervals untouched (determinism, SC-004), disappeared intervals deleted,
  new intervals inserted. Guard (FR-010) evaluated before applying.

## Existing structures gaining data (no DDL)

| Where | Addition |
|---|---|
| `ml_datasets.universe` JSONB | `universe_name`, `universe_fingerprint`, `resolved_count` keys |
| model artifact `metadata.json` + train result dict | `universe` stamp (name+fingerprint), same mechanism as `device` (#146) |
| `experiments.config` JSONB | `universe` stamp inherited from the dataset |
| discovery `search_space` JSONB | existing `universe_filter` extended with name+fingerprint |

## Attribute registry (code-level, not a table)

| Attribute | Source | Kind |
|---|---|---|
| `asset_type` | stocks | static |
| `industry` | stocks | static |
| `sector` | stocks | static |
| `exchange` | stocks | static |
| `status` | stocks | static |
| `close` | stock_ohlcv | time-varying (daily) |

`market_cap` (and other fundamentals) enter the registry only when
fundamentals vintages exist — deferred (research R3).

## Entity relationships

```
universe_definitions 1 ──< universe_exclusions >── 1 stocks
        │ (name+fingerprint stamped into)
        └──> ml_datasets.universe / experiments.config / model artifact metadata
```

## Seed data (db-init, idempotent)

`modeling_default` (is_default=true) with rules:
1. `no-shell-companies`: industry eq "SHELL COMPANIES" — "Blank-check entities; cash boxes, not operating businesses"
2. `no-etfs`: asset_type eq "ETF" — "Funds, not companies; double-counts constituents in cross-sections"
3. `no-penny-stocks`: close lt 1.00 — "Sub-dollar prices distort return statistics" (time-varying; owner-approved 2026-07-19)
