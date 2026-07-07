# Interface Parity Matrix — Agentic Regime Discovery (006)

Constitution III + FR-042 + FR-115: **every discovery operation reachable via CLI, MCP, and
UI.** CLI is canonical; MCP wraps the CLI; UI calls the same service functions. Enforced by
`tests/test_discovery_interfaces.py`. No operation ships without all three columns green —
surfaces land **per increment**, with the code (owner requirement; see plan).

| # | Operation | CLI | MCP tool | UI surface |
|---|---|---|---|---|
| 1 | Pre-register + start a discovery run | `gefion regime discover start` | `regime_discover_start` | Regimes → Discovery tab → "New run" form |
| 2 | List discovery runs | `gefion regime discover list` | `regime_discover_list` | Discovery tab → runs table |
| 3 | Inspect a run (pre-registration, segregation, family size, status) | `gefion regime discover show <run>` | `regime_discover_show` | Discovery tab → run detail |
| 4 | Candidate ledger (all candidates + verdicts, incl. losers) | `gefion regime discover ledger <run>` | `regime_discover_ledger` | Run detail → ledger table |
| 5 | Diagnostics ledger (limits hit; sample-dependent vs structural) | `gefion regime discover diagnostics <run>` | `regime_discover_diagnostics` | Run detail → diagnostics panel |
| 6 | Verdicts / admitted edges | `gefion regime discover verdicts <run>` | `regime_discover_verdicts` | Run detail → verdicts (admitted highlighted) |
| 7 | Trust grades (accrued walk-forward folds) | `gefion regime discover grades [<candidate>]` | `regime_discover_grades` | Regimes → admitted regime detail → grade timeline |
| 8 | Run within an experiment cycle | `gefion experiment propose --type regime_discovery` → approve → run | `experiment_propose/approve/run` | Experiments page (existing cycle flow) |
| 9 | Admitted regime as ordinary regime (chart/slice/labels) | 005 surfaces (`regime show/labels`, `chart regime`, `--by-regime`) | 005 tools | Regimes page (origin=machine badge) |

## Cross-surface rules

- **Canonical logic once**: CLI → `regimes.discovery.*` service functions; MCP wraps CLI;
  UI imports the same services. No re-implementation.
- **`--json` parity** on every read command; MCP returns the identical payload; UI renders it.
- **Honesty rules surface everywhere**: low-power/degenerate/unstable refusals are visibly
  flagged in all three; an unadmitted candidate is never presented as a finding; descriptive
  (backward) grades are visually distinct from forward confirmations.
- **Operator skill**: adding `regime_discover_*` MCP tools requires updating `/gefion`
  routing (Constitution III).
- **Docs-drift**: all new commands/tools documented so `tests/test_docs_drift.py` passes.
