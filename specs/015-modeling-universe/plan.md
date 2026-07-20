# Implementation Plan: Modeling Universe Membership

**Branch**: `015-modeling-universe` | **Date**: 2026-07-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/015-modeling-universe/spec.md`

## Summary

Introduce a first-class **universe** object — named, rule-defined subsets of
the stock population — and route every modeling cross-section consumer through
one chokepoint (`gefion.universe`). Rules are generic attribute/operator/value
predicates stored in `universe_definitions`; membership is materialized as
**exclusion intervals** in `universe_exclusions` (member = no covering
exclusion), giving compact storage, per-row "why", and as-of queries. The
default universe `modeling_default` ships excluding shell companies and ETFs
(~29% of symbols) from every dataset, derived market series, ranking, backtest
and experiment cross-section, with universe name + fingerprint stamped into
result provenance. Derived-series history recomputes under the cleaned
universe (FR-013), and the 013 admitted signal re-checks via the existing SPA
re-verdict machinery.

## Technical Context

**Language/Version**: Python 3.10+ (existing codebase)
**Primary Dependencies**: psycopg (definitions/membership), existing `gefion.features.dispatcher`, `gefion.ml.dataset`, `gefion.regimes.discovery`, typer (CLI), PyYAML (export/import)
**Storage**: PostgreSQL + TimescaleDB. TWO new tables — `universe_definitions`, `universe_exclusions` (owner approval required; DDL in data-model.md). Provenance rides existing JSONB columns (`ml_datasets.universe`, `experiments.config`) and model artifact metadata — no result-table DDL.
**Testing**: pytest; DB tests via `ENABLE_DB_TESTS=1` + `schema.test_db_url()`; fixtures call canonical `schema.py` creators for every table touched
**Target Platform**: Linux server (sloth prod), macOS dev
**Project Type**: single project (CLI + MCP + Streamlit UI)
**Performance Goals**: nightly `universe refresh` ≤ 5 min (SC-006); market-function SQL gains one indexed `NOT EXISTS` probe — verify via Tempo before/after
**Constraints**: deterministic materialization (SC-004); refusal semantics for empty/outsized-shrink refresh (FR-010); observing-plane consumers never filtered (FR-006)
**Scale/Scope**: 6.2k symbols × 26y; ~1.7k excluded symbols under initial rules (mostly single open-ended intervals); ~15 consumer sites, 2 of them SQL-composed

## Constitution Check

| Principle | Status |
|---|---|
| I. Database-First | PASS — definitions live in DB, YAML export is backup; schema via schema.sql + migration (two-file rule); db-init seeds `modeling_default` idempotently |
| II. TDD (NON-NEGOTIABLE) | PASS — every phase lists test files first; red before green; committed together |
| III. CLI-First | PASS — full `gefion universe` command group before MCP wrappers; UI read-only card; parity matrix in contracts/cli-mcp.md |
| IV. Observability | PASS — `gefion.observability` spans on refresh/evaluate/members with counts; parent-context propagation; Tempo check on dispatcher SQL change |
| V. Consistent CLI Presentation | PASS — shared presentation helpers; `--json` bypasses formatting |
| VI. Simplicity | PASS — complement-form membership avoids a 40M-row table; attribute registry is a dict, not a table; no rank rules, no hysteresis in v1 (spec Out of Scope) |
| Schema Governance | GATE — exact DDL in data-model.md presented for owner approval BEFORE touching sql/schema.sql; two-file rule on approval |
| Secrets | N/A — no new secrets |

Post-design re-check: PASS (no violations; Complexity Tracking empty).

## Project Structure

### Documentation (this feature)

```text
specs/015-modeling-universe/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0 — landscape survey + decisions R1-R9
├── data-model.md        # Phase 1 — DDL proposal + provenance shape + attribute registry
├── quickstart.md        # Phase 1 — usage + prod rollout runbook (FR-013)
├── contracts/
│   └── cli-mcp.md       # CLI/MCP/UI parity + chokepoint API contract
└── tasks.md             # Phase 2 (/speckit.tasks — not yet created)
```

### Source Code (repository root)

```text
tests/                                  # TESTS FIRST (TDD)
├── test_universe_definitions.py        # NEW — CRUD, validation refusals, fingerprint stability, pins, reserved 'all', single-default
├── test_universe_membership.py         # NEW — static + close-rule intervals, islands, as-of, determinism, guard refusals, reconcile delta
├── test_universe_chokepoint.py         # NEW — resolve order (name > all > default), refusals, exclusion clause SQL, consumers routed (dataset build, market fn, cross-sectional, backtest loader, cycle_runner backfill, discovery base)
├── test_universe_provenance.py         # NEW — dataset stamp, artifact metadata round-trip, experiments.config stamp
├── test_universe_cli.py                # NEW — command group, --json, explain, export/import round-trip, deletion door
└── test_docs_drift.py                  # EXISTING — picks up new commands/MCP tools automatically

src/gefion/
├── universe/                           # NEW package (the chokepoint)
│   ├── __init__.py                     # public API: resolve/members/clause/explain/refresh
│   ├── definitions.py                  # CRUD, validation, fingerprint, export/import, seed
│   ├── evaluate.py                     # attribute registry, rule → intervals (static + islands)
│   ├── membership.py                   # reconcile, as-of queries, guard, explain
│   └── deletion.py                     # deletion door (mirrors existing deletion modules)
├── db/schema.py                        # MODIFIED — create_universe_definitions_table, create_universe_exclusions_table (mirror schema.sql exactly)
├── ml/dataset.py                       # MODIFIED — resolve_universe_symbols + export fallback route through chokepoint; stamp
├── ml/models.py                        # MODIFIED — universe stamp in result dict + artifact metadata (device pattern)
├── features/dispatcher.py              # MODIFIED — run_market_function gains exclusion clause
├── compute/cross_sectional.py          # MODIFIED — fetch_feature_with_sectors gains exclusion clause
├── backtest/data_loader.py             # MODIFIED — both selectors route through chokepoint
├── experiments/cycle_runner.py         # MODIFIED — experimental-feature population + config stamp
├── regimes/discovery/universe.py       # MODIFIED — chain gains universe step; base list from chokepoint
├── regimes/discovery/signals.py        # MODIFIED — market-mean SQL gains exclusion clause
├── macro/derived.py                    # MODIFIED — derive passes universe context; --recompute path
├── cli.py                              # MODIFIED — universe command group + --universe flags + db-health headline
sql/
├── schema.sql                          # MODIFIED (after DDL approval) — two new tables
└── migrations/2026MMDD_NNNNNN_universe_membership.sql   # NEW (after DDL approval)
mcp-server/server.py                    # MODIFIED — 8 universe tools + dispatch
src/gefion/ui/views/                    # MODIFIED — system page universe card (read-only)
```

**Structure Decision**: single project; new `src/gefion/universe/` package with
four small modules (definitions / evaluate / membership / deletion) matching
the deletion-door and macro package layout; consumers modified in place.

## Implementation Phases (TDD order; each increment = tests red → code green → docs, committed together)

1. **Core definitions + membership** — test_universe_definitions,
   test_universe_membership red → `universe/` package + schema creators
   (after DDL approval: schema.sql + migration) → green.
2. **Chokepoint + consumer sweep** — test_universe_chokepoint red → resolve
   API + route the ~15 sites (symbol-list sites via members; dispatcher +
   discovery market-mean via exclusion clause) → green; Tempo check on
   market-function spans.
3. **Provenance** — test_universe_provenance red → dataset/model/experiment
   stamps → green.
4. **Surfaces** — test_universe_cli red → CLI group + `--universe` flags +
   MCP tools + UI card + db-health headline → green; docs (USER_GUIDE,
   README, MCP_WORKFLOWS, ARCHITECTURE, DATA_DICTIONARY regen) + gefion-learn
   aside + /gefion routing in the same increment.
5. **Rollout** — quickstart runbook on prod: refresh, derived-series history
   recompute, regime re-derive, 013 SPA re-verdict, cron line, observation
   entry.

## Interfaces, Documentation & Learning Impact *(mandatory)*

- **Three interfaces**: parity matrix in [contracts/cli-mcp.md](contracts/cli-mcp.md) —
  full CLI command group, 8 MCP tools, read-only UI card; definition changes
  deliberately CLI/MCP-only (owner-gated).
- **Documentation**: USER_GUIDE (universe section + consumer flags), README
  (command list), MCP_WORKFLOWS (tool routing + WHEN TO USE), ARCHITECTURE
  (universe package + tables + chokepoint diagram), DATA_DICTIONARY (regen
  after DDL), DEVELOPMENT.md (chokepoint convention: new cross-section
  consumers MUST route through `gefion.universe`).
- **Learning materials**: `.claude/commands/gefion-learn.md` — new aside in
  the market/features module: "the modeling universe" (concept-first: why
  denominators matter, shells/ETFs example), checkpoint question on
  as-of membership.
- **Delivery rule**: each phase lands surfaces + docs with the code.

## Complexity Tracking

*No constitution violations — table intentionally empty.*
