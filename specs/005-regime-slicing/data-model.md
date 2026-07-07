# Phase 1 Data Model: Regime Slicing (005)

Derived from spec Key Entities + research. **All DDL below is PROPOSED for owner approval
(Schema Governance); it must not be written to `schema.sql` or run until approved.**

## Entities

### RegimeDefinition  → table `regime_definitions` (relational)

| Field | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `name` | text UNIQUE | canonical slug, e.g. `vol-regime-terciles` |
| `scope` | text | enum: `market` \| `sector` \| `industry` \| `asset` |
| `expression` | JSONB | the `RegimeExpression` AST (see below); `Json()`-wrapped |
| `bucketing` | JSONB | bucket labels + boundary method (causal quantile / threshold) |
| `persistence` | JSONB | `{min_dwell: int\|null, mode: "min_dwell"\|"schmitt"\|null}` — off by default |
| `origin` | text | `human` (006 adds `machine`) |
| `dataset_provenance` | JSONB | instruments/exchanges/date-range/snapshot (FR-023) |
| `descriptive_metadata` | JSONB | 3-layer descriptors (FR-024): dataset, regime, result |
| `status` | text | `active` \| `archived` |
| `created_at` | timestamptz | |

Validation: `expression` MUST validate against the AST schema (R3); every feature ref MUST resolve
to an existing causal feature; `scope` MUST be one of the enum; export to `regime-definitions/<name>.json`.

### RegimeExpression (embedded JSONB AST, not a table)

```jsonc
// node
{ "op": "AND" | "OR" | "NOT", "children": [ <node|leaf>, ... ] }
// leaves
{ "leaf": "comparison", "feature": "<causal_feature_ref>", "cmp": ">" , "value": 0, "scope": "market" }
{ "leaf": "reference", "regime": "<named_atomic_regime>" }
{ "leaf": "detector_function", "function_id": <int>, "scope": "market" }   // gated (006 fresh-holdout tier)
```

Rules: leaves carry their own `scope`; composite output scope = **finest** scope among leaves
(FR-020); a `detector_function` leaf breaks countability (flagged; admissible in 006 only under the
fresh-holdout tier, FR-019a).

### RegimeLabel  → table `regime_labels` (**TimescaleDB hypertable**)

| Field | Type | Notes |
|---|---|---|
| `regime_id` | int FK → regime_definitions | |
| `date` | date | hypertable time dimension |
| `entity_id` | int NOT NULL DEFAULT 0 | `0` = market-wide; stock id for sector/industry/asset scope |
| `label` | text | bucket label or `undefined` |
| `dataset_version` | text | provenance key (FR-023) |

PK: (`regime_id`, `entity_id`, `date`) — includes partition col `date` (TimescaleDB requirement);
`entity_id` uses sentinel `0` (not NULL) since PK columns cannot be NULL. One row per (regime, date, entity).
Causality: every label at `date` computed from data ≤ `date` (FR-004). BRIN index on `date`.

### RegimeSlicedResult (computed, not persisted by default)

Per-regime breakdown of a backtest/experiment evaluation: `{regime_id, bucket, metrics{return,
sharpe, drawdown, win_rate, profit_factor, trade_count}, raw_n, effective_n, mean_dwell,
low_power_flag, flicker_flag, undefined_excluded}` — plus, for conditional evaluation, `holdout_pvalue`
and `in_fdr_family`; or, for continuous conditioning, `{interaction_coef, interaction_pvalue}`.
MUST reconcile to the aggregate (FR-009).

## Relationships

```
regime_definitions 1───∞ regime_labels        (a definition has many dated labels)
regime_definitions ────  RegimeExpression      (embedded AST; leaves may reference other definitions or a sandboxed detector function)
regime_labels     ────>  backtest equity/trades (join by date[, entity] at slice time — post-run, read-only)
regime_labels     ────>  experiment holdout     (join to slice per-regime p-values → apply_fdr)
```

## State / lifecycle

- RegimeDefinition: `active` → `archived` (no destructive delete of labels referenced by results).
- RegimeLabel: recomputed on definition change or dataset-version change; sample-dependent
  diagnostics are re-evaluated, not inherited, across dataset versions (FR-025 lineage in 006).

## Proposed DDL (for approval — not executed)

- `CREATE TABLE regime_definitions (...)` per fields above.
- `CREATE TABLE regime_labels (...)` + `SELECT create_hypertable('regime_labels','date')` +
  BRIN index on `date`.
- Migration `sql/migrations/NNNNNN_regimes.sql` mirroring `schema.sql` (two-file rule).
