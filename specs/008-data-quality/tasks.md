# Tasks: Provider-Garbage Detection & Quarantine (Data Quality)

**Input**: Design documents from `/specs/008-data-quality/`
**Prerequisites**: plan.md, spec.md, research.md (R1–R9), data-model.md,
contracts/ (all present); DDL owner-approved 2026-07-08

**Tests**: INCLUDED — TDD is non-negotiable (Constitution II). Every
implementation task is preceded by a failing test (Red → Green), committed
together.

**Delivery rule** (plan, mandatory): each story lands its CLI/MCP surfaces and
docs *in the same increment* — not deferred to polish. The widened docs-drift
tests (2026-07-08) enforce the mechanical part automatically.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Create package scaffold `src/gefion/quality/__init__.py` (imports from `gefion.observability`; package docstring stating the two-populations model) and the initial `data-quality/catalog.yaml` skeleton (defaults block only — metric stanzas arrive with their tests)

## Phase 2: Foundational (blocking all stories)

- [ ] T002 Write `tests/test_quality_catalog.py`: loader parses defaults + metric stanzas (bounds, derivation, why); rejects unknown keys, non-numeric bounds, and metrics naming nonexistent table/column pairs; coverage listing enumerates covered metrics AND uncovered numeric columns on validated tables; the `universe:` block (test tickers, selectors) loads (RED)
- [ ] T003 Implement `src/gefion/quality/catalog.py` + populate `data-quality/catalog.yaml` with the initial scope: twelve fundamentals ratio metrics (loose envelopes for margins/ROE, each with its `why`), derivations for dividend_yield/pe_ratio/market_cap, and the vix stanza (GREEN)
- [ ] T004 [P] Write `tests/test_quality_rules.py` (pure functions, no DB): tier-1 bounds convict the #79 quartet values (beta −503341.44, −165013.73; dividend_yield 1000000.0) and do NOT convict the shell counter-case (ROE −615% within its loose envelope); tier-2 cross-field convicts on >10× observed-vs-recomputed disagreement, abstains when inputs are missing; tier-3 spike requires magnitude × reversion (a level shift is NOT a spike); tier-4 robust z (median/MAD) at threshold; **only tiers 1–2 may return a trash verdict** — tiers 3–4 structurally cap at suspect (RED)
- [ ] T005 Implement `src/gefion/quality/rules.py` (GREEN)

**Checkpoint**: the catalog and every rule evaluator exist and are proven against
the live prod garbage — no database, no schema yet.

---

## Phase 3: US1 — Trash convicted at write time and recorded (P1) 🎯 MVP

**Goal**: covered write paths validate as they store; every detection is an
idempotent row in the findings ledger; the write is never blocked.
**Independent test**: re-ingest the #79 quartet → verbatim storage + trash
findings; the shell counter-case row produces none.

- [ ] T006 [US1] Write `tests/test_quality_findings.py` (part 1, schema): after db-init, `data_quality_findings` exists with the CHECK on verdict, the UNIQUE (entity_table, entity_id, metric, date, rule), and both indexes; observed/expected accept ±1e15 (DOUBLE PRECISION — the ledger cannot overflow on what it convicts) (RED, DB)
- [ ] T007 [US1] Apply the approved DDL via the two-file rule: `sql/schema.sql` + `sql/migrations/20260709_000001_data_quality_findings.sql`; add the `TABLE_PURPOSE` entry in `scripts/gen_data_dictionary.py`; regenerate `docs/DATA_DICTIONARY.md` in the same commit (GREEN)
- [ ] T008 [US1] Extend `tests/test_quality_findings.py` (part 2, ledger API): `record_findings` upserts idempotently (re-validation refreshes, never duplicates); `list_findings` filters by metric/entity/verdict/since; findings survive `gefion data entity-delete` of the flagged entity (007 audit exception); resolution sets resolved_at/resolution and never deletes (RED, DB)
- [ ] T009 [US1] Implement `src/gefion/quality/findings.py` (GREEN)
- [ ] T010 [US1] Write `tests/test_quality_write_paths.py`: `_write_fundamentals_results` batch produces findings for garbage rows while `write_errors` stays 0 and all rows land verbatim; a validation-internal exception is counted in the summary (`quality_findings_errors`) and never raises into the write (FR-303); `macro.ingest.ingest_series` validates against the vix stanza; summaries carry `quality_findings: N` (RED, DB)
- [ ] T011 [US1] Implement `src/gefion/quality/validate.py` (batch pass: tiers 1–2 + cheap corroboration when history is at hand) and hook it into `src/gefion/cli.py::_write_fundamentals_results` and `src/gefion/macro/ingest.py` (GREEN)

**Checkpoint**: MVP — the exact garbage observed on prod convicts at write time,
visibly, without a single blocked or mutated write.

---

## Phase 4: US2 — Research consumers exclude trash by default (P1)

**Goal**: convicted values are missing-by-default in cross-sectional compute,
ML dataset builds, and fundamentals-derived features; opt-in is explicit and
recorded.
**Independent test**: same computation twice over a universe containing a
convicted value — default excludes (median unpolluted), `--include-flagged`
includes and records the choice.

- [ ] T012 [US2] Write `tests/test_quality_consumers.py`: the shared exclusion helper returns the convicted (entity_id, column, date) set from catalog mapping + unresolved trash findings (resolved findings drop out; suspect findings never appear); cross-sectional compute treats a convicted beta as NULL (symbol unranked that day, median unpolluted); `ml dataset-build` omits convicted values and writes `quality_filtering: active` into the manifest; with `--include-flagged` the verbatim value is present and the manifest records `opted-out`; fundamentals-derived feature computation (forward_fill_quarterly class) skips convicted source values (RED, DB)
- [ ] T013 [US2] Implement the exclusion helper in `src/gefion/quality/findings.py` (or `validate.py`) and apply it in `src/gefion/db/cross_sectional.py`, the dataset-build path, and the fundamentals-derived feature path; add `--include-flagged` to `cross-sectional-compute` and `ml dataset-build` in `src/gefion/cli.py` (GREEN)
- [ ] T014 [US2] Surfaces + docs for the increment: `include_flagged` arg on the corresponding MCP tools in `mcp-server/server.py` (interface assertions first in `tests/test_quality_consumers.py`); README/USER_GUIDE notes on default quarantine + opt-in; docs-drift green

---

## Phase 5: US3 — Research universes stop seeing junk instruments (P2)

**Goal**: test tickers excluded from every research universe unconditionally;
asset_type/exchange are declared, fail-closed selectors; one source of truth
(the catalog `universe:` block) shared with 006's chain.
**Independent test**: with ZVZZT in stocks, cross-sectional compute and a
dataset build exclude it; `asset_type:common` yields only common stock; NULL
asset_type is excluded and counted.

- [ ] T015 [US3] Write `tests/test_quality_universe.py`: helper excludes catalog-listed test tickers everywhere; `asset_type:common` and `exchange:<X>` selectors; NULL asset_type fail-closed with exclusion counts; 006's discovery chain and the quality helper agree on the ticker list (single source of truth — read from the catalog by both) (RED, DB)
- [ ] T016 [US3] Implement `src/gefion/quality/universe.py`; wire into cross-sectional compute and dataset build; point the discovery filter chain's test-ticker list at the catalog `universe:` block (`src/gefion/regimes/discovery/universe.py` — definitions shared, chain code untouched) (GREEN)
- [ ] T017 [US3] Docs: README/USER_GUIDE universe-quality notes; `docs/DEVELOPMENT.md` gains "research universes are quality-filtered by default" under Patterns & Gotchas; docs-drift green

---

## Phase 6: US4 — Quality visible and operable (P2)

**Goal**: db-health `data_quality` section; `gefion quality findings|catalog|backfill|resolve`
+ MCP tools; idempotent, value-preserving backfill.
**Independent test**: seeded garbage → backfill creates findings, re-run
changes nothing, stored values byte-identical; db-health counts exactly the
seeded metrics.

- [ ] T018 [US4] Write `tests/test_quality_surfaces.py`: `db-health` JSON carries `data_quality` (per-metric unresolved counts by verdict; warning on nonzero trash; zeros on a clean DB); CLI `quality findings/catalog/resolve` behaviors (resolve requires --reason; refuses without); backfill creates findings for pre-seeded stored garbage, is idempotent, and a before/after checksum of the validated tables is identical (SC-305); MCP source assertions for `quality_findings`/`quality_catalog`/`quality_backfill`/`quality_resolve` (RED, DB)
- [ ] T019 [US4] Implement `src/gefion/quality/backfill.py`, the `quality` CLI group in `src/gefion/cli.py`, the db-health `data_quality` section, and the four MCP tools in `mcp-server/server.py` (GREEN)
- [ ] T020 [US4] Docs + routing: README command rows; USER_GUIDE quality section (two populations, hierarchy, opt-in, backfill); MCP_WORKFLOWS entries; `/gefion` operator-skill routing (“is this data trustworthy” → `quality_findings`; suspects are not convictions); docs-drift green

---

## Phase 7: US5 — Corroboration tiers + the macro family proof (P3)

**Goal**: temporal + cross-sectional tiers produce suspect findings (never solo
trash); macro series validate through identical machinery.
**Independent test**: an episodic spike earns suspect, a persistent extreme
earns nothing; VIX ≤ 0 convicts via the same ledger and db-health section.

- [ ] T021 [US5] Write corroboration integration tests (extend `tests/test_quality_write_paths.py`): history (1.2, −503341, 1.2) → suspect (or corroboration detail on an existing conviction); persistent (−6.1, −6.2, −6.0) → nothing; a robust cross-sectional outlier passing tiers 1–2 → at most suspect; a synthetic macro value of −3 against the vix stanza → trash finding through the same ledger, visible in the same db-health section (SC-307) (RED, DB)
- [ ] T022 [US5] Wire tiers 3–4 into `src/gefion/quality/validate.py` (write-path: when history/cross-section already at hand; backfill: always) (GREEN)
- [ ] T023 [US5] **Learning materials** (owner directive): `.claude/commands/gefion-learn.md` Module 1 data-quality aside (two populations; why outlierness never convicts) + checkpoint “why does a beta of −503,341 get convicted but an ROE of −615% doesn’t?”; verify curriculum drift test green

---

## Phase 8: Polish & Cross-Cutting

- [ ] T024 Observability pass: run validation, backfill, findings listing with `OTEL_ENABLED=true`; `gefion span-check` — spans parented, no orphans, batch pass overhead visible in trace (<5% of the write span)
- [ ] T025 Full-suite pre-flight: drop `gefion_test`, complete suite against a fresh DB (capture the exit code — the pipe-masking lesson); docs-drift green; data-dictionary `--check` green
- [ ] T026 Prod rollout on sloth (post-merge): pull + `db-migrate`; `gefion quality backfill` over full history; confirm findings for the known pre-spec garbage (MDXH/ELOX beta, CTAA dividend_yield) and **zero false convictions** spot-check; `db-health` data_quality populated; stored values verified unchanged
- [ ] T027 Update `.specify/memory/progress.md` + `backlog.md` (universe-quality item closed by 008; note the #79 → 008 arc); update memory of record

---

## Dependencies & Story Completion Order

```
Setup (T001)
  └─> Foundational (T002–T005: catalog + rule evaluators, no DB)
        └─> US1 (T006–T011: ledger DDL + write paths)  🎯 MVP
              ├─> US2 (T012–T014: consumer exclusion)
              ├─> US3 (T015–T017: universe hardening — independent of US2)
              └─> US4 (T018–T020: surfaces + backfill — needs ledger only)
                    └─> US5 (T021–T023: corroboration + macro proof)
                          └─> Polish (T024–T027)
```

Parallel opportunities: T004 alongside T002/T003; US2, US3, US4 are mutually
independent after US1 (different files); T023 alongside T021/T022.

## Implementation Strategy

- **MVP = through Phase 3**: the live prod garbage convicts at write time with
  a visible audit trail — value delivered even before consumers change.
- **The payoff = Phase 4**: research outputs stop ingesting trash by default.
- **Prevention = Phase 5**: the junk never enters the peer group at all.
- **Operability = Phase 6**: the backfill retroactively flags prod history.
- Everything through T025 runs on this machine (dev DB + synthetic); T026 is
  the only sloth step.

## Success Criteria Mapping

SC-301 (quartet convicts, shell doesn't) → T004–T005, T010–T011 · SC-302
(default exclusion + recorded opt-in) → T012–T014 · SC-303 (db-health counts) →
T018–T019 · SC-304 (test tickers gone; asset_type selector) → T015–T017 ·
SC-305 (idempotent, value-preserving backfill) → T018–T019, T026 · SC-306
(family test: catalog edit only) → T002–T003 · SC-307 (macro through identical
machinery) → T021–T022
