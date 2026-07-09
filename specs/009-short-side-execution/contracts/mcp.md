# MCP Contract — Short-Side Execution (009)

Extends the existing `backtest_run` (and `backtest_compare`) tools with mode +
short parameters; payloads identical to `--json`. Documented in
docs/MCP_WORKFLOWS.md (drift-checked). `/gefion` operator-skill routing updated.

| Tool | New args | Notes |
|---|---|---|
| `backtest_run` | `mode` (long_only default \| long_short), `borrow_rate?`, `initial_margin?`, `maintenance_margin?`, `max_gross_exposure?`, `max_short_exposure?` | default long_only = today's behavior; short params only bite in long_short |
| `backtest_compare` | `mode?` | compare within a mode |

Honesty rules for the operator skill:
- Default is long_only; short is opt-in via `mode=long_short`.
- A long_short result carries `exposure`, `margin_events`, and `short_costs` —
  surface margin events and borrow/dividend costs, never present a short's return
  without them (a short that ignores borrow/dividends/margin is dishonestly rosy).
- Equity may be negative on an adverse short — that is a modeled outcome, not an
  error.
