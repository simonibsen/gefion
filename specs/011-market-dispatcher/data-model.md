# Data Model — Market-Level Dispatcher Mode (011)

## DDL PROPOSAL (owner approval required — not applied)

One column on the existing registry, two-file rule + dictionary regen on approval:

```sql
-- sql/migrations/2026MMDD_000001_feature_function_scope.sql (and schema.sql)
ALTER TABLE feature_functions
    ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'stock'
    CHECK (scope IN ('stock', 'market'));
```

- Every existing row defaults to `'stock'` — zero behavior change for the
  per-stock world.
- `'market'` rows are executed by the market mode only; per-stock compute
  paths ignore them (and vice versa) — the CHECK makes a third state
  impossible.
- No index (tiny table, filtered scans trivial).

## Registry row (market function) — existing columns, new usage

| Column | Market-mode usage |
|---|---|
| `name` | matches macro series + `macro_<name>` feature (R7 convention) |
| `scope` | `'market'` (NEW column) |
| `language` | `'python'` |
| `function_body` | defines `compute(rows) -> float | None` (R2 contract) |
| `inputs` | `{"features": ["indicator_sma_200", ...]}` — per-stock columns joined into the cross-section |
| `enabled` | honored by derive (skip-and-report when false) |
| `checksum` | audit of body text (existing mechanism) |

## Value storage — UNCHANGED

`computed_features(data_id = macro_series.id, feature_id = macro_<name>,
date, value)`; `entity_table='macro_series'` on the definition. No new value
surfaces (FR-1109).
