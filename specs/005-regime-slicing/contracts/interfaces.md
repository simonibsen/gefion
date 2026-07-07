# Interface Parity Matrix — Regime Slicing (005)

Constitution III (CLI-First) + FR-013 + User Story 4: **every operation MUST be reachable via
CLI, MCP, and UI.** The CLI is canonical; the MCP tool wraps the CLI; the UI calls the same
service functions. This matrix is the single source of truth for interface coverage — no operation
ships without all three columns filled and green.

| # | Operation | CLI | MCP tool | UI surface |
|---|---|---|---|---|
| 1 | Create/define a regime | `gefion regime define …` | `regime_define` | Regimes page → "New regime" form (AST builder) |
| 2 | List regimes | `gefion regime list [--json]` | `regime_list` | Regimes page → table |
| 3 | Show one regime | `gefion regime show <name> [--json]` | `regime_show` | Regimes page → detail drawer (AST + metadata) |
| 4 | Compute labels | `gefion regime compute <name> [--dataset …]` | `regime_compute` | Regimes page → "Compute" action + coverage bars |
| 5 | Inspect labels / coverage | `gefion regime labels <name> [--json]` | `regime_labels` | Regimes page → episode timeline + bucket-frequency chart |
| 6 | Import/export definitions | `gefion regime import\|export` | `regime_definitions_import\|export` | Regimes page → import/export buttons |
| 7 | Archive a regime | `gefion regime archive <name>` | `regime_archive` | Regimes page → archive action |
| 8 | Slice a backtest by regime | `gefion backtest run … --by-regime <name> [--json]` | `backtest_run` (adds `by_regime` arg) | Backtesting page → "Slice by regime" selector + per-regime metric blocks |
| 9 | Continuous-interaction test | `gefion regime interaction --signal … --by <var> [--json]` | `regime_interaction` | Regimes/Backtesting page → interaction panel (coef + p-value) |
| 10 | Conditional experiment eval | `gefion experiment run … --by-regime <name>` | `experiment_run` (adds `by_regime` arg) | Experiments page → per-regime p-value column + FDR chart |

## Contract rules across surfaces

- **Canonical behavior lives once.** CLI commands call `src/gefion/regimes/*` service functions;
  MCP tools shell/wrap the CLI (per Constitution III — "MCP wraps CLI, does not bypass"); UI views
  call the same service functions directly. No interface re-implements logic.
- **`--json` parity.** Every read/compute CLI command supports `--json`; the MCP tool returns that
  same structured payload; the UI renders from it. `--json` bypasses all presentation (Constitution V).
- **Error/empty/loading states** MUST be handled in every surface: unknown regime, uncomputable
  regime (missing feature refs), zero labels yet, low-power/undefined buckets, and reconciliation
  failure each have a defined message and are surfaced identically in meaning across CLI/MCP/UI.
- **New MCP tools ⇒ operator-skill review.** Adding these MCP tools requires reviewing/updating the
  `/gefion` operator skill's tool routing (Constitution III).
- **Docs-drift.** Every new CLI command and MCP tool MUST appear in README/USER_GUIDE/MCP_WORKFLOWS
  so `tests/test_docs_drift.py` passes (FR-016).

See `cli.md`, `mcp.md`, `ui.md` for per-surface detail, and `sql.md` for proposed DDL.
