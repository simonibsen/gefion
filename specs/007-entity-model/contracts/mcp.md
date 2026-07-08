# MCP Contract — First-Class Entities (007)

Each tool wraps the CLI (Constitution III); payloads identical to `--json`. All
documented in docs/MCP_WORKFLOWS.md (drift-checked). Adding these requires a
`/gefion` operator-skill routing update.

| Tool | Args | Wraps | Notes |
|---|---|---|---|
| `macro_ingest` | name, provider?, kind?, cadence?, full? | `macro ingest` | **Mutating**, may take minutes on `--full`; operator confirms first |
| `macro_list` | — | `macro list` | read-only |
| `entity_delete` | entity_table, key, confirm? | `data entity-delete` | **Mutating & destructive**; operator MUST confirm; dry-run (confirm=false) is the default and safe |
| `health_check` / db-health surfaces | — | existing | gains the `entity_integrity` section automatically |

Honesty rules: `entity_delete` without `confirm` never changes anything and its
dry-run payload is the full blast radius; audit ledgers are never in scope.
