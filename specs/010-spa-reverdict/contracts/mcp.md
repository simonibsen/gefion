# MCP Contract — SPA Re-Verdict (010)

Each tool wraps the CLI (Constitution III); payloads identical to `--json`.
Documented in docs/MCP_WORKFLOWS.md (drift-checked); `/gefion` operator
routing updated in the same increment.

| Tool | Args | Wraps | Notes |
|---|---|---|---|
| `regime_discover_spa` | run, iterations?, seed?, level?, block_length? | `regime discover spa` | **Mutating** (appends one re-verdict row; changes nothing else) and compute-bound (seconds–minutes) — operator confirms first |
| `regime_discover_show` / `verdicts` / `grades` | — | existing | gain the SPA line / flag automatically |
| `regime_discover_start` | — | existing | enforces the budget gate; refusal payload names it |

Honesty rules for the operator skill:
- Present p_consistent as the verdict, with lower/upper as brackets — never
  cherry-pick the friendlier bracket.
- A refusal (reconstruction mismatch, missing data, empty family) is not a
  failure of the run — report the reason verbatim; never retry with a looser
  tolerance.
- An SPA failure on an admitted edge is a flag, not a demotion — say exactly
  that; forward fold evidence remains the demotion mechanism.
