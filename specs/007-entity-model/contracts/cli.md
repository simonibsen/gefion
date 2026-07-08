# CLI Contract — First-Class Entities (007)

All read commands support `--json` and `--db-url`.

## `gefion macro ingest`
```
gefion macro ingest --name vix
    [--provider alphavantage:INDEX_DATA]   # default; 'fred:VIXCLS' fallback
    [--kind index] [--cadence daily]
    [--full]                                # decades backfill vs incremental
    [--db-url …] [--json]
```
Creates/updates the catalog row (rows are configuration — SC-207), fetches, upserts
into `macro_series_values`, and materializes the `macro_<name>` feature definition
(`entity_table='macro_series'`, `source_table='macro_series_values'`,
`source_column='value'`) into `computed_features`. Honest refusals: unknown
provider; provider endpoint unavailable (names the fallback); catalog/name
mismatch.

## `gefion macro list`
```
gefion macro list [--json]
```
Catalog + per-series value coverage (first/last date, row count) + whether the
feature is materialized.

## `gefion data entity-delete`
```
gefion data entity-delete <entity_table> <key> [--confirm] [--db-url …] [--json]
```
`<key>` is the natural key where one exists (`stocks` → symbol, `macro_series` →
name), else the integer id. **Dry-run by default**: reports feature-value counts per
feature (registry edges) and hard-FK dependents from `pg_constraint` — the full
blast radius. `--confirm`: deletes feature values (per registry) first, then the
entity row; hard-FK dependents cascade where declared, or the command refuses with
the blocker list. Never touches audit ledgers (discovery candidates / diagnostics /
trust grades) — deleting an artifact never deletes accounting.

## `gefion db-health` (extended)
New `entity_integrity` section: per declared entity table, orphaned feature-value
counts, with actionable warnings — same style as `dimension_coverage`.

## Errors (honest, non-silent)
- feature registration with an undeclared/nonexistent `entity_table` → refused with
  the validation reason
- `entity-delete` on an unknown table/key → refused; on RESTRICT blockers → refused
  with the list
- `macro ingest` when the premium endpoint is unavailable → refused naming the
  fallback provider and the config change needed
