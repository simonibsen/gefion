# Implementation Plan: Provider-Garbage Detection & Quarantine (Data Quality)

**Branch**: `008-data-quality` | **Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/008-data-quality/spec.md`

## Summary

Separate provider trash (definitionally impossible or self-contradictory values)
from degenerate-but-real extremes, without ever mutating stored data. A declarative
per-metric validation catalog drives two convicting tiers (definitional bounds,
cross-field recompute against price ground truth) and two corroborating tiers
(temporal spike, cross-sectional outlier → suspect only). Every detection is an
append-only row in a new `data_quality_findings` audit ledger; research consumers
(cross-sectional compute, ML dataset build, fundamentals-derived features) treat
trash-verdict values as missing by default with recorded opt-in; `db-health` gains
a `data_quality` section; an idempotent backfill flags already-stored history.
Universe hardening (test-ticker exclusion + asset_type/exchange selectors) rides
along and closes the standing universe-quality backlog item. One system across
entity kinds via 007's declared entity model — fundamentals and macro series
validate through identical machinery.

## Technical Context

**Language/Version**: Python 3.10+ (existing codebase)
**Primary Dependencies**: psycopg (ledger + reads), numpy (robust z: median/MAD),
PyYAML (catalog file — already a dependency via principles catalog), typer (CLI),
existing `gefion.observability`
**Storage**: PostgreSQL + TimescaleDB. ONE new table — `data_quality_findings`
(plain relational audit ledger, same reasoning as 006's ledger tables). No changes
to any existing table. DDL is PROPOSED in contracts/sql.md — **awaiting owner
approval** (Schema Governance; nothing written to sql/ until approved).
**Testing**: pytest; DB tests via `schema.test_db_url()` under `ENABLE_DB_TESTS=1`
**Target Platform**: dev (localhost) + prod (sloth)
**Project Type**: single project — new `src/gefion/quality/` package + catalog
config in `data-quality/catalog.yaml`
**Performance Goals**: validation adds a bounded per-batch pass to write paths
(target: <5% overhead on `fundamentals-update`); full-history backfill on prod
completes in minutes, not hours; consumer exclusion joins are bounded by the
findings ledger's size (sparse — thousands, not millions)
**Constraints**: never mutate/reject stored values; validation failure never blocks
a write; findings idempotent per (entity_table, entity_id, metric, date, rule);
audit ledger survives entity deletion (007 `entity-delete` never touches it);
parameterized SQL only; catalog is configuration, not code
**Scale/Scope**: ~6.2k symbols × 12 cataloged fundamentals metrics weekly; ~9.2k
macro values per series; findings expected in the hundreds on current prod data

## Constitution Check

*GATE: evaluated against constitution v1.9.0 — PASS (one approval pending, by design)*

| Principle | Status | Notes |
|---|---|---|
| I. Database-First | PASS | Findings are DB rows; the catalog is repo configuration *about* validation, not feature logic (same standing as the principles catalog). Two-file rule for the one new table. |
| II. TDD | PASS | Every phase below is Red→Green; tests listed before implementation files. |
| III. CLI-First | PASS | New `gefion quality` group lands with MCP wrappers in the same increment; db-health extension surfaces in existing CLI/MCP/UI paths. |
| IV. Observability | PASS | `gefion.quality` modules import observability; validation pass and backfill are spanned with counts; span-check gate in polish. |
| V. CLI Presentation | PASS | Output via `get_output`/`out.*` helpers, `--json` bypasses formatting. |
| VI. Simplicity | PASS | One table, one package, one catalog file. Suspect tiers are v1-informational (no consumer machinery for them). No trigger/scan-job infrastructure — validation rides existing write paths. |
| Schema Governance | **PENDING** | `data_quality_findings` DDL is proposed, not executed (contracts/sql.md). Implementation of Phase 1+ blocks on owner approval. |
| Secrets | PASS | No secrets involved. |

*Post-Phase-1 re-check (design complete): no new violations introduced; the
pending item remains the single DDL approval.*

## Project Structure

### Documentation (this feature)

```text
specs/008-data-quality/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R9
├── data-model.md        # Phase 1: findings ledger + catalog schema
├── quickstart.md        # Phase 1: end-to-end walkthrough
├── contracts/
│   ├── sql.md           # PROPOSED DDL (owner approval required)
│   ├── cli.md           # gefion quality …, db-health extension, consumer flags
│   └── mcp.md           # quality_* tools
└── tasks.md             # Phase 2 (/speckit.tasks — not created here)
```

### Source Code (repository root)

```text
data-quality/
└── catalog.yaml                    # validation catalog (configuration, versioned)

src/gefion/quality/
├── __init__.py                     # package doc; observability import
├── catalog.py                      # load/validate catalog; list covered + uncovered metrics
├── rules.py                        # tier evaluators: bounds, cross-field, temporal, cross-sectional
├── findings.py                     # ledger writes (idempotent upsert), queries, resolution
├── validate.py                     # batch validation pass (write-path hook + shared core)
├── backfill.py                     # on-demand validation over stored history
└── universe.py                     # quality universe helpers (test tickers, asset_type/exchange)

src/gefion/cli.py                   # quality group (findings|catalog|backfill); db-health section;
                                    # consumer opt-in flags (cross-sectional, ml dataset-build)
mcp-server/server.py                # quality_findings / quality_catalog / quality_backfill

tests/
├── test_quality_catalog.py         # catalog load, coverage listing, family test (SC-306)
├── test_quality_rules.py           # tier evaluators incl. the #79 quartet + shell counter-case
├── test_quality_findings.py        # ledger idempotence, resolution, deletion survival
├── test_quality_write_paths.py     # fundamentals-update + macro ingest integration
├── test_quality_consumers.py       # default exclusion + opt-in (cross-sectional, dataset)
├── test_quality_universe.py        # test tickers, asset_type selectors, fail-closed
└── test_quality_surfaces.py        # CLI/MCP interface assertions, db-health section, backfill
```

**Structure Decision**: one new package (`gefion.quality`) so detection, ledger,
and universe helpers have one home; catalog at repo root beside `feature-definitions/`
and `principles/` following the established configuration-directory convention.

## Interfaces, Documentation & Learning Impact *(mandatory)*

- **Three interfaces (Constitution III)**:
  | Operation | CLI | MCP | UI |
  |---|---|---|---|
  | List/inspect findings | `gefion quality findings` | `quality_findings` | Data page: quality panel (db-health payload) |
  | Catalog + coverage | `gefion quality catalog` | `quality_catalog` | — (operator concern; JSON via db-health) |
  | Backfill stored history | `gefion quality backfill` | `quality_backfill` (mutating — ledger only) | — |
  | Quality at a glance | `gefion db-health` (data_quality section) | `health_check`/db-health surfaces | existing System/Data health views |
  | Consumer opt-in | `--include-flagged` on `cross-sectional-compute`, `ml dataset-build` | same-named tool args | recorded in artifact metadata |
- **Documentation**: README (quality command rows), docs/USER_GUIDE.md (quality
  section: two populations, hierarchy, opt-in), docs/MCP_WORKFLOWS.md (quality_*
  tools), docs/ARCHITECTURE.md (quality pass in the data flow), DATA_DICTIONARY
  regen (new table), docs/DEVELOPMENT.md (how to add catalog rules — the SC-306
  recipe). Drift tests enforce all of it (the widened 2026-07-08 enforcement).
- **Learning materials**: `.claude/commands/gefion-learn.md` Module 1 gains the
  data-quality aside (two populations; why outlierness never convicts) and a
  checkpoint: *"why does a beta of −503,341 get convicted but an ROE of −615%
  doesn't?"*
- **Delivery rule**: each increment lands CLI+MCP+docs together (enforced).

## Increment plan (safety & value ordering)

1. **Foundational** — catalog file + loader + tier-1/tier-2 rule evaluators
   (pure functions, no DB) — the #79 quartet convicts, the shell case doesn't
   (tests first; no schema needed yet).
2. **US1 core** — findings ledger (after DDL approval) + write-path integration
   (fundamentals-update, macro ingest); idempotence + never-blocks-the-write.
3. **US2** — consumer exclusion via a shared quality-filter helper: cross-sectional
   compute, `ml dataset-build`, fundamentals-derived features; `--include-flagged`
   opt-in recorded in artifacts.
4. **US3** — `gefion.quality.universe`: test-ticker exclusion everywhere +
   asset_type/exchange selectors (reusing 006's declared-filter vocabulary);
   wire into cross-sectional compute and dataset build.
5. **US4** — `db-health` data_quality section; `gefion quality findings|catalog|backfill`
   + MCP tools + docs; backfill proven idempotent and value-preserving.
6. **US5** — temporal + cross-sectional suspect tiers; macro bounds proof (VIX ≤ 0)
   through identical machinery (SC-307).
7. **Polish** — observability pass (span-check), fresh-DB full suite (exit code
   captured — the pipe-masking lesson), prod rollout: migrate sloth, run backfill
   over prod history, confirm findings for the pre-spec garbage, update
   progress/backlog (universe-quality item closed by 008).

## Complexity Tracking

*No constitutional violations to justify.* The single pending item is the standard
Schema Governance approval for `data_quality_findings` (contracts/sql.md).
