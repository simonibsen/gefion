# CLI Contract — Agentic Regime Discovery (006)

New `gefion regime discover` sub-group. All read commands support `--json` and `--db-url`.

## `gefion regime discover start`
```
gefion regime discover start --name <slug>
    --atoms <atoms.json>            # primitive library (pre-registered)
    [--depth 2] [--budget 100]      # K cap + per-cycle candidate budget
    [--tier interaction|grammar|expressive]...   # tiers enabled this run
    [--signal-source features]      # pluggable; v1 default features
    [--grading-scheme walk_forward] # pluggable
    [--universe-filter test_tickers,asset_type:common|passthrough]  # declared chain
    [--fresh-holdout <start>:<end>] # required if tier=expressive
    [--seed N] [--dataset <version>] [--json]
```
Pre-registers the search space (family denominator computed at enumeration), records
segregation boundaries, runs enumerate → evaluate → FDR → ledger. Long runs print run-id
immediately; progress is DB-visible (ledger rows accrue).

## Read commands
```
gefion regime discover list [--status ...] [--json]
gefion regime discover show <run> [--json]          # pre-registration, segregation, family size
gefion regime discover ledger <run> [--verdict admitted|rejected|refused...] [--json]
gefion regime discover diagnostics <run> [--sample-dependent|--structural] [--json]
gefion regime discover verdicts <run> [--json]
gefion regime discover grades [<candidate>] [--json] # forward folds; descriptive rows flagged
```

## Errors (honest, non-silent)
- expressive tier without a declared fresh-holdout block → refuse at start
- consumed reserve block re-declared without justification → refuse
- unfiltered universe without explicit `passthrough` → refuse
- run whose segregation cannot be proven → status `invalid`, no verdicts
