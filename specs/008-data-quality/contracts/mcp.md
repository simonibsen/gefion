# MCP Contract — Provider-Garbage Detection & Quarantine (008)

Each tool wraps the CLI (Constitution III); payloads identical to `--json`. All
documented in docs/MCP_WORKFLOWS.md (drift-checked — the widened 2026-07-08
enforcement covers every tool). `/gefion` operator-skill routing updated in the
same increment.

| Tool | Args | Wraps | Notes |
|---|---|---|---|
| `quality_findings` | metric?, symbol?, entity_table?, entity_id?, verdict?, since?, limit? | `quality findings` | read-only |
| `quality_catalog` | — | `quality catalog` | read-only; includes the coverage-gap listing |
| `quality_backfill` | metric?, entity_table? | `quality backfill` | **Mutating (ledger only)** — creates findings, changes no stored values; may take minutes on full history; operator confirms first |
| `quality_resolve` | finding_id, reason | `quality resolve` | **Mutating** — supersedes a finding; operator MUST confirm; reason required |
| `health_check` / db-health surfaces | — | existing | gains the `data_quality` section automatically |
| `cross_sectional_compute`, `ml_dataset_build` | + include_flagged? | existing commands | opt-in mirrors the CLI flag; recorded in the artifact |

Honesty rules: `quality_backfill` and `quality_resolve` never alter stored data
values; the ledger is append/supersede only. When presenting findings, always
show the verdict tier — a suspect is not a conviction, and the operator skill
must not describe suspects as trash.
