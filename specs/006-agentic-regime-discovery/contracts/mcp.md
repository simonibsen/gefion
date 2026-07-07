# MCP Contract — Agentic Regime Discovery (006)

Each tool wraps the CLI (Constitution III); payloads identical to `--json`. All tools
documented in docs/MCP_WORKFLOWS.md (docs-drift). Adding these requires a `/gefion`
operator-skill routing update.

| Tool | Args | Wraps |
|---|---|---|
| `regime_discover_start` | name, atoms, depth?, budget?, tiers?, signal_source?, grading_scheme?, universe_filter?, fresh_holdout?, seed?, dataset? | `regime discover start` |
| `regime_discover_list` | status? | `regime discover list` |
| `regime_discover_show` | run | `regime discover show` |
| `regime_discover_ledger` | run, verdict? | `regime discover ledger` |
| `regime_discover_diagnostics` | run, kind? | `regime discover diagnostics` |
| `regime_discover_verdicts` | run | `regime discover verdicts` |
| `regime_discover_grades` | candidate? | `regime discover grades` |

`regime_discover_start` is a mutating, potentially long operation — excluded from read-only
allowlists; the operator skill must confirm before invoking (same class as experiment runs).
