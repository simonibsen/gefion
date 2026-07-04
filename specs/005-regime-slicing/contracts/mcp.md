# MCP Contract — Regime Slicing (005)

Each MCP tool mirrors a CLI command (Constitution III: MCP wraps CLI, does not bypass). Tools
return the same structured payload the CLI emits with `--json`. Adding these tools requires a
review/update of the `/gefion` operator skill's tool routing. All new tools must be documented in
`docs/MCP_WORKFLOWS.md` for docs-drift.

| MCP tool | Args | Returns | Wraps |
|---|---|---|---|
| `regime_define` | `name, scope, expression(AST), bucketing?, min_dwell?, dwell_mode?` | `{id, name, exported_path}` | `regime define` |
| `regime_list` | `scope?, status?` | `[{name, scope, status, coverage?}]` | `regime list --json` |
| `regime_show` | `name` | `{name, scope, expression, bucketing, persistence, provenance, metadata}` | `regime show --json` |
| `regime_compute` | `name, dataset?, start?, end?` | `{buckets:[{label, coverage, effective_n}], mean_dwell, flicker}` | `regime compute --json` |
| `regime_labels` | `name, entity?` | `{episodes, bucket_frequencies, dwell_time}` | `regime labels --json` |
| `regime_definitions_import` / `_export` | `dir` | `{count}` | `regime import/export` |
| `regime_archive` | `name` | `{name, status}` | `regime archive` |
| `regime_interaction` | `signal, by, horizon_days?, start?, end?` | `{interaction_coef, interaction_pvalue, n, effective_n}` | `regime interaction --json` |

## Extended existing tools

- `backtest_run` — **new optional arg** `by_regime: str`. When set, the returned object gains a
  `by_regime` block: per-bucket metrics + `raw_n`/`effective_n`/`mean_dwell`/`low_power`/`flicker` +
  `reconciliation_ok`. When unset, output is unchanged.
- `experiment_run` — **new optional arg** `by_regime: str`. Adds per-regime holdout p-values and
  marks which entered the FDR family; low-power/undefined buckets carry no p-value (fail-closed).

## Notes

- These tools are the `mcp__gefion__*` surface consumed by agents (including 006's autonomous
  discovery, which calls `regime_define`/`regime_compute` under its stricter gate).
- Read tools (`regime_list/show/labels`) are safe/read-only; `regime_define/compute/archive` mutate
  state and are excluded from any read-only tool allowlists.
