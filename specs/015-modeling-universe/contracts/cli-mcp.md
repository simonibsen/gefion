# Interface Contracts: Modeling Universe (015)

Every command supports `--json`; MCP tools wrap the CLI (constitution III).

## CLI: `gefion universe ...`

| Command | Contract |
|---|---|
| `universe define NAME --description D --rules-file rules.yaml [--default]` | Create/update a definition. Refuses unknown attribute/op with the valid lists; refuses reserved name `all`. Prints fingerprint. Updating recomputes fingerprint. |
| `universe list` | Name, enabled, default flag, rule count, pin count, fingerprint (short), current excluded count. |
| `universe show NAME` | Full rules and pins with reasons, fingerprint, membership summary (members / excluded, by rule), flap counts for time-varying rules. |
| `universe members NAME [--as-of DATE] [--limit N]` | Member symbols as of date (default today). |
| `universe explain SYMBOL [--universe NAME] [--as-of DATE]` | Member or not; if excluded: rule/pin name + reason + interval. SC-003. |
| `universe refresh [NAME] [--force]` | Re-evaluate rules → reconcile intervals. Prints delta (added/removed exclusions, by rule). Applies FR-010 guard; `--force` overrides the shrink guard (never the empty-universe refusal). |
| `universe enable NAME` / `universe disable NAME` | Toggle. Disabling the default universe refuses (consumers would have no resolution). |
| `universe export [-o FILE]` / `universe import FILE [--dry-run]` | YAML round-trip, same shape as regime definition export/import. Import validates before writing; `--dry-run` reports the diff. |
| `universe delete NAME [--confirm]` | Deletion door: dry-run by default, enumerates referencing datasets/experiments/models by fingerprint, refuses while referenced. |

Consumer flags (added, all defaulting to the default universe):
`dataset-build --universe NAME|all`, `backtest run --universe`,
`ml predict --universe`, `cross-sectional compute --universe`,
`regime discover start --universe` (feeds the filter chain),
`macro derive` (uses default; `--universe` for experiments' control runs).

## MCP tools (mcp-server/server.py)

| Tool | Wraps |
|---|---|
| `universe_list` | `universe list --json` |
| `universe_show` | `universe show --json` |
| `universe_members` | `universe members --json` |
| `universe_explain` | `universe explain --json` |
| `universe_refresh` | `universe refresh --json` (flagged: changes modeling population — human-directed) |
| `universe_define` | `universe define --json` (owner-gated: definition changes follow definition-review discipline) |
| `universe_delete` | `universe delete --json` (DESTRUCTIVE, human-directed, dry-run default) |
| `universe_export` / `universe_import` | export/import |

`system_status` / `db-health`: gains universe headline (default universe name,
members, excluded, by-rule counts; warning if default universe missing or
refresh stale > 7 days).

## UI

System page: universe summary card (default universe, member/excluded counts,
by-rule breakdown) — read-only; definition changes stay CLI/MCP (owner-gated).

## Chokepoint API (internal contract, `gefion.universe`)

```python
def universe_members(conn, name: str | None = None, as_of: date | None = None) -> list[str]
def universe_member_ids(conn, name: str | None = None, as_of: date | None = None) -> list[int]
def universe_exclusion_clause(universe_id: int, date_expr: str, data_id_expr: str) -> tuple[str, list]
def explain_symbol(conn, symbol: str, name: str | None = None, as_of: date | None = None) -> dict
def resolve_universe(conn, name: str | None) -> ResolvedUniverse   # name > 'all' > default; refusal on unknown/disabled
def refresh_universe(conn, name: str | None = None, force: bool = False) -> dict  # delta report; FR-010 guard
```

Provenance stamp shape (datasets / experiments / model artifacts):

```json
{"universe_name": "modeling_default", "universe_fingerprint": "sha256:...", "resolved_count": 4470}
```
