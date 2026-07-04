# CLI Contract — Regime Slicing (005)

All commands live under the `gefion regime` group (new) plus additions to existing `backtest`/
`experiment` commands. Output via `output.py`/`cli_helpers`; every read/compute command supports
`--json` (bypasses formatting, Constitution V). Exit non-zero on error with a clear message.

## `gefion regime define`
```
gefion regime define --name <slug> --scope market|sector|industry|asset
                     --expression <file.json | '-'>   # RegimeExpression AST
                     [--bucketing <file.json>] [--min-dwell <int>] [--dwell-mode min_dwell|schmitt]
                     [--json]
```
Validates the AST (all feature refs resolve; scope valid), stores the definition, exports to
`regime-definitions/<slug>.json`. Errors: `unknown feature ref`, `invalid scope`, `duplicate name`.

## `gefion regime list` / `show`
```
gefion regime list [--scope …] [--status active|archived] [--json]
gefion regime show <name> [--json]     # AST, bucketing, persistence, provenance, metadata
```

## `gefion regime compute`
```
gefion regime compute <name> [--dataset <name_version>] [--start <date>] [--end <date>] [--json]
```
Computes causal labels (no lookahead), applies persistence, records realized dwell-time, stamps
dataset provenance. Reports per-bucket coverage + flicker flag. Idempotent per (regime, dataset).

## `gefion regime labels`
```
gefion regime labels <name> [--entity <symbol>] [--json]   # episodes, bucket frequencies, dwell-time
```

## `gefion regime import` / `export` / `archive`
```
gefion regime import <dir>   |   gefion regime export <dir>   |   gefion regime archive <name>
```

## `gefion regime interaction`
```
gefion regime interaction --signal <name> --by <conditioning_var>
                          [--horizon-days N] [--start …] [--end …] [--json]
```
Runs OLS `return ~ signal + var + signal×var` with HAC errors; returns `{interaction_coef,
interaction_pvalue, n, effective_n}`.

## `backtest run` — new option (additive, opt-in)
```
gefion backtest run … --by-regime <name> [--json]
```
Without `--by-regime`: byte-for-byte unchanged (FR-007). With it: adds `by_regime` block to output —
per-bucket `{metrics, raw_n, effective_n, mean_dwell, low_power, flicker}` + reconciliation check.

## `experiment run` — new option
```
gefion experiment run … --by-regime <name>
```
Adds per-regime holdout p-values to results; the (experiment × regime × bucket) tests enter the
cycle's flat BH family (`apply_fdr`); low-power/undefined buckets fail closed.
