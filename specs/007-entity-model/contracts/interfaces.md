# Interface Parity Matrix — First-Class Entities (007)

Constitution III: CLI is canonical; MCP wraps the CLI; UI where a page exists.
Enforced by `tests/test_entity_*`/`test_macro_ingest.py` interface assertions.
Surfaces land per increment, with the code.

| # | Operation | CLI | MCP tool | UI surface |
|---|---|---|---|---|
| 1 | Ingest/refresh a macro series | `gefion macro ingest` | `macro_ingest` | **Deferred with justification**: operator/cron action, no visual workflow yet; the *consumption* UI is #4. Revisit at the second real series. |
| 2 | List macro series + coverage | `gefion macro list` | `macro_list` | same deferral as #1 |
| 3 | Delete an entity + its feature values | `gefion data entity-delete` | `entity_delete` | dry-run/confirm is CLI/MCP-native; destructive ops deliberately keep a narrow door |
| 4 | Consume `macro_vix` in regimes/discovery | existing 005/006 surfaces (`regime interaction --by macro_vix`, discovery atoms) | existing tools | existing Regimes/Discovery pages — zero changes needed (it's just a feature) |
| 5 | Entity-integrity visibility | `gefion db-health` | `health_check` | existing health surfaces |
| 6 | Feeds graph + ERD | generated `docs/DATA_DICTIONARY.md` | `docs_read` | rendered on the code host |

Cron: `macro ingest` joins the prod metadata-maintenance crontab (documented in
docs/DEPLOYMENT.md) once VIX lands.
