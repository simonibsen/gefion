# Tasks: First-Class Entities for the Feature Store

**Input**: Design documents from `/specs/007-entity-model/`
**Prerequisites**: plan.md, spec.md (clarified), research.md, data-model.md, contracts/ (all present); DDL owner-approved 2026-07-08

**Tests**: INCLUDED — TDD is non-negotiable (Constitution II). Every implementation
task is preceded by a failing test (Red → Green), committed together.

**Delivery rule** (plan, mandatory): each story lands its CLI/MCP surface and docs
*in the same increment* — not deferred to polish.

**Phase ordering note**: spec priorities are US1/US2 = P1, US3/US4/US5 = P2 — but
the plan's safety rule reorders execution: **US4 (detection) and US5 (deletion)
ship BEFORE US1's FK retirement**, because the constraint's replacements must exist
before the constraint is removed (spec edge case: no undetectable-orphan window).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Create package scaffolds `src/gefion/entities/__init__.py` and `src/gefion/macro/__init__.py`, each importing from `gefion.observability`

## Phase 2: Foundational (blocking all stories)

- [ ] T002 Write `tests/test_entity_schema.py` (part 1): after db-init, `feature_definitions.entity_table` exists, TEXT NOT NULL DEFAULT 'stocks', and all existing definitions read 'stocks' (RED, DB)
- [ ] T003 Apply Migration A via the two-file rule: `sql/schema.sql` + `sql/migrations/20260708_000001_entity_table.sql`; regenerate `docs/DATA_DICTIONARY.md` in the same commit (GREEN)
- [ ] T004 [P] Write `tests/test_entity_registry.py`: registration refuses a nonexistent entity table and a table without an integer `id` PK; accepts `stocks`; dynamic identifiers only via `psycopg.sql.Identifier` after validation; validation wired into the feature-definition registration/import paths (RED, DB)
- [ ] T005 Implement `src/gefion/entities/registry.py` + hook validation into feature-definition registration (GREEN)

**Checkpoint**: the entity axis exists and is validated; nothing about behavior has changed (FK still present).

---

## Phase 3: US4 — Integrity Is Detectable (P2, sequenced early by safety rule)

**Goal**: orphaned feature values are loudly detectable before the constraint that
prevented them is removed.
**Independent test**: manufacture an orphan → db-health reports table + count +
remediation; clean DB → zeros.

- [ ] T006 [US4] Write `tests/test_entity_orphans.py`: fixture creates a disposable entity table in the TEST database (product schema untouched — breaks the scan↔migration-B cycle), registers a feature declaring it, writes a value, deletes the entity row → scan reports {table, count}; clean DB reports zero per declared table; db-health JSON carries an `entity_integrity` section with an actionable warning (RED, DB)
- [ ] T007 [US4] Implement `src/gefion/entities/orphans.py` + the `entity_integrity` section in db-health (`src/gefion/cli.py`, dimension-coverage style); document the section in `docs/USER_GUIDE.md` db-health notes (GREEN; docs same increment)

---

## Phase 4: US5 — Registry-Driven Deletion (P2, sequenced early by safety rule)

**Goal**: the cascade's replacement exists before the cascade is retired — uniform
across entity kinds, dry-run first.
**Independent test**: dry-run reports full blast radius and changes nothing;
confirm deletes values→entity in order; stocks parity with the old cascade.

- [ ] T008 [US5] Write `tests/test_entity_deletion.py`: dry-run impact (per-feature value counts + hard-FK dependents from `pg_constraint`) with zero changes; `--confirm` order (feature values per registry, then entity row); natural-key resolution (`stocks`→symbol, else id); RESTRICT blockers refused with the list; stocks cleanup ≥ the existing cascade (parity test runs while the FK still exists — the strongest possible baseline); audit ledgers (`regime_candidates`/`discovery_diagnostics`/`regime_trust_grades`) never touched (RED, DB)
- [ ] T009 [US5] Implement `src/gefion/entities/deletion.py` + `gefion data entity-delete` in `src/gefion/cli.py` (GREEN)
- [ ] T010 [P] [US5] MCP `entity_delete` tool (mutating/destructive — dry-run default) in `mcp-server/server.py` (source-inspection tests first in `tests/test_entity_deletion.py`); `/gefion` operator-skill routing; docs: README + `docs/USER_GUIDE.md` + `docs/MCP_WORKFLOWS.md`; docs-drift green

---

## Phase 5: US1 — Declared Entity Identity (P1) 🎯 the load-bearing change

**Goal**: identity resolves per feature via `(entity_table, data_id)`; the hard FK
is retired now that its replacements exist; equity behavior byte-identical.
**Independent test**: a feature declaring a non-stock entity table stores and
serves values; every equity pipeline untouched; full suite green (SC-201).

- [ ] T011 [US1] Extend `tests/test_entity_schema.py` (part 2): post-Migration-B the `computed_features`→`stocks` FK is ABSENT (both fresh-db-init and migrated-existing-db paths); `macro_series` + `macro_series_values` exist with UNIQUE(name), cadence CHECK, (series_id,date) PK, and the values FK CASCADE (RED, DB)
- [ ] T012 [US1] Apply Migration B via the two-file rule: `sql/schema.sql` (drop REFERENCES clause; add macro tables) + `sql/migrations/20260708_000002_entity_model.sql` (introspected-name constraint drop + macro tables); regenerate the data dictionary in the same commit. **Gated: merges only after T007 and T009 are green** (GREEN)
- [ ] T013 [P] [US1] Write `tests/test_signals_entity_branching.py`: loader resolves each feature's entity_table in-query; stocks features byte-identical to today (regression vector); a single-entity series' market-level value is the value itself; symbol-universe filtering applies only to stocks features; cross-entity aggregation impossible (per-feature scoping) (RED, DB)
- [ ] T014 [US1] Implement loader branching in `src/gefion/regimes/discovery/signals.py` (`_feature_series`/`load_market_data`) (GREEN)
- [ ] T015 [US1] Regression gate: drop `gefion_test`, full suite against fresh DB green with zero equity-pipeline edits (SC-201); document the entity-resolution rule in `docs/ARCHITECTURE.md` (same increment)

---

## Phase 6: US2 — Macro Home + VIX Proving Case (P1)

**Goal**: VIX ingested end-to-end; `macro_vix` usable by discovery/regimes with
zero equity-pipeline changes; the family test passes.
**Independent test**: VIX-seeded discovery stops logging uncomputable proposals;
`regime interaction --by macro_vix` answers; `stocks` holds no index row.

- [ ] T016 [US2] **Live verification first**: one `INDEX_DATA&symbol=VIX` call against the prod key (on sloth); record the result in research.md — on failure, pivot the default provider config to `fred:VIXCLS` (config change, not redesign)
- [ ] T017 [US2] Write `tests/test_macro_ingest.py`: INDEX_DATA parser (OHLC→value=close) and value-only parsing (FRED/CPI class); catalog CRUD; upsert idempotence; `macro_<name>` feature materialization with `entity_table='macro_series'`, `source_table='macro_series_values'`, `source_column='value'`; **the family test** — a second synthetic series lands via catalog row + ingest + feature def with zero DDL (SC-207) (RED, DB)
- [ ] T018 [US2] Implement `src/gefion/macro/catalog.py`, `src/gefion/macro/ingest.py`, the client fetch method + `alphavantage/catalog.py` parser (GREEN)
- [ ] T019 [US2] CLI `gefion macro ingest|list` + MCP `macro_ingest`/`macro_list` + `/gefion` routing + docs (README, `docs/USER_GUIDE.md`, `docs/MCP_WORKFLOWS.md`); docs-drift green (tests first: interface assertions in `tests/test_macro_ingest.py`)
- [ ] T020 [US2] Consumption proof on synthetic/dev data: a discovery atom `{"feature":"macro_vix","form":"tercile"}` enumerates and evaluates; `regime interaction --by macro_vix` answers; a VIX-requiring principle-seeded run records **zero** uncomputable-VIX diagnostics (SC-202/203 test-level; prod exercise is T027)

---

## Phase 7: US3 — The Registry as the Feeds Graph (P2)

**Goal**: "what feeds what" is a generated artifact; governance encoded at
DDL-approval time; the curriculum teaches the model.
**Independent test**: dictionary regen shows consumers per raw table, flags
`stocks_fundamentals` as consumer-less, renders the solid/dashed Mermaid ERD.

- [ ] T021 [US3] Write `tests/test_data_dictionary_feeds.py`: hermetic generation (schema.sql FKs + `feature-definitions/*.json` exports, no DB); feeds section lists consumers per raw table; consumer-less raw tables flagged (`stocks_fundamentals` today, SC-204); Mermaid flowchart blocks present with solid (FK) vs dashed (registry) edges, grouped by layer (RED)
- [ ] T022 [US3] Implement the feeds-graph + ERD sections in `scripts/gen_data_dictionary.py`; regenerate `docs/DATA_DICTIONARY.md` (GREEN)
- [ ] T023 [P] [US3] Governance docs: `docs/DEVELOPMENT.md` add-a-table checklist additions (layer, prefix taxonomy, feeder edges, deletion story, **row-vs-table rule** with the peer-group litmus) + the "add a data source" recipe (VIX as the worked example); `docs/ARCHITECTURE.md` layering model
- [ ] T024 [US3] **Learning materials** (owner directive): `.claude/commands/gefion-learn.md` Module 1 gains the entity model, the feeds graph, and the row-vs-table rule; checkpoint: "why is CPI a `macro_series` row but sectors would be their own entity table?"

---

## Phase 8: Polish & Cross-Cutting

- [ ] T025 Observability pass: run registry validation, ingest, orphan scan, entity-delete with `OTEL_ENABLED=true`; `gefion span-check` — spans parented, no orphans
- [ ] T026 Full-suite pre-flight: drop `gefion_test`, complete suite against fresh DB (capture the exit code — the pipe-masking lesson); docs-drift green
- [ ] T027 Prod rollout on sloth (post-merge): pull + `db-migrate`; `gefion macro ingest --name vix --full`; `db-health` (entity_integrity zeros; coverage clean); one bounded VIX-atom discovery run — confirm the uncomputable-VIX diagnostic is gone from a real run's ledger (SC-203 in production)
- [ ] T028 Update `.specify/memory/progress.md` + `backlog.md` (VIX backlog item closed by 007; note relation to issues #75/#76 — entity-delete is their first landed increment)

---

## Dependencies & Story Completion Order

```
Setup (T001)
  └─> Foundational (T002–T005: entity axis + validation)
        └─> US4 (T006–T007: detection)      ─┐  safety rule: both BEFORE the drop
        └─> US5 (T008–T010: deletion)        ─┤
              └─> US1 (T011–T015: FK retirement + loader + regression gate)
                    └─> US2 (T016–T020: macro home + VIX)
                          └─> US3 (T021–T024: feeds graph + governance + curriculum)
                                └─> Polish (T025–T028)
```

Parallel opportunities: T004 alongside T002/T003; T010 alongside T008–T009 tail;
T013 alongside T011/T012; T023 alongside T021/T022.

## Implementation Strategy

- **The safety spine is the order**: detection (US4) and deletion (US5) are P2 by
  user value but land first because US1's destructive step depends on them.
- **MVP = through Phase 5**: the entity model complete and regression-proven, even
  before any macro series exists.
- **The payoff = Phase 6**: VIX lands and the diagnostics ledger's oldest standing
  request is closed.
- **The discipline = Phase 7**: sprawl control becomes generated artifacts and
  approval-time questions.
- Everything through T026 runs on this machine (dev DB + synthetic); T027 is the
  only sloth step.

## Success Criteria Mapping

SC-201 (regression no-op) → T015 · SC-202/203 (VIX usable; diagnostics silent) →
T020, T027 · SC-204/204a (feeds graph + ERD) → T021–T022 · SC-205 (orphan
detection) → T006–T007 · SC-206 (deletion completeness) → T008–T009 · SC-207
(family test) → T017
