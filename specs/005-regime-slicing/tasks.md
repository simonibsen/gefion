# Tasks: Regime Slicing — Conditional Evaluation Across Market/Sector/Asset States

**Input**: Design documents from `/specs/005-regime-slicing/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (all present)

**Tests**: INCLUDED — TDD is non-negotiable per Constitution II. Every implementation task is
preceded by a failing test task (Red → Green). Tests and implementation are committed together.

**Organization**: Tasks are grouped by user story (spec.md priorities) for independent delivery.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US5 map to spec.md user stories
- DB tests use `schema.test_db_url()` with `ENABLE_DB_TESTS` guards; `OTEL_ENABLED=false`

---

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Create module scaffold `src/gefion/regimes/__init__.py` importing from `gefion.observability` (create_span/set_attributes)
- [ ] T002 [P] Create `regime-definitions/` export directory with a `.gitkeep` (Database-First backup target)
- [ ] T003 [P] Add empty test files `tests/test_regime_schema.py`, `tests/test_regime_definitions.py`, `tests/test_regime_labels.py`, `tests/test_regime_slicing.py`, `tests/test_regime_interaction.py`, `tests/test_regime_conditional.py`, `tests/test_regime_interfaces.py` with `ENABLE_DB_TESTS` pytestmark where DB is needed

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Approved schema + the RegimeDefinition/AST + persistence layer that every story needs.

- [ ] T004 Write `tests/test_regime_schema.py`: after db-init, assert `regime_definitions` and `regime_labels` exist, `regime_labels` is a hypertable, PK is `(regime_id, entity_id, date)`, BRIN index on `date` (RED)
- [ ] T005 Apply the **owner-approved** DDL (contracts/sql.md) via the two-file rule: add both tables to `sql/schema.sql` (CREATE IF NOT EXISTS) and create migration `sql/migrations/20260704_000001_regime_slicing.sql`, kept in sync
- [ ] T006 Run `gefion db-init`/migrate against `gefion_test`; verify T004 passes (GREEN)
- [ ] T007 [P] Write `tests/test_regime_definitions.py`: RegimeExpression AST validation, feature-ref resolution, scope enum, finest-scope-under-composition (FR-020), causality-by-construction, JSON round-trip (RED)
- [ ] T008 Implement `src/gefion/regimes/definitions.py`: `RegimeDefinition`, `RegimeExpression` AST (comparison/reference/detector_function leaves; AND/OR/NOT nodes), validation, JSON export/import to `regime-definitions/` (GREEN)
- [ ] T009 Add DB persistence tests to `tests/test_regime_definitions.py`: store/load a definition, parameterized SQL, `Json()` JSONB wrapping (RED)
- [ ] T010 Implement definition persistence in `src/gefion/regimes/definitions.py` (store/load/list/archive; `Json()` adapter; spans) (GREEN)

**Checkpoint**: schema live in test DB; definitions can be created, validated, persisted, exported.

---

## Phase 3: User Story 1 — Describe & Compute a Regime (Priority: P1) 🎯 MVP

**Goal**: Define a regime and compute causal, persistent labels; inspect episodes and coverage.
**Independent test**: Define a market vol regime, compute it, verify one causal label per date,
`undefined` lookback, contiguous episodes, recorded dwell-time — via CLI, MCP, and UI.

- [ ] T011 [P] [US1] Write `tests/test_regime_labels.py`: causal labels (no lookahead, FR-004), `undefined` lookback periods, min-dwell/schmitt persistence forms contiguous episodes, effective-N = independent-episode count, dwell-time recorded, flicker flag, dataset provenance stamped (RED)
- [ ] T012 [US1] Implement `src/gefion/regimes/labels.py`: causal label computation, persistence transform, effective-N, dwell-time, flicker detection, provenance/descriptive-metadata stamping; write to `regime_labels` (GREEN)
- [ ] T013 [US1] Write CLI tests (in `tests/test_regime_interfaces.py`): `regime define|compute|list|show|labels|import|export|archive` incl. `--json` (RED)
- [ ] T014 [US1] Implement the `regime` command group in `src/gefion/cli.py` via `output.py`/`cli_helpers` (define/compute/list/show/labels/import/export/archive) (GREEN)
- [ ] T015 [P] [US1] Write MCP tests: `regime_define/compute/list/show/labels`, `regime_definitions_import/export`, `regime_archive` return CLI-`--json` payloads (RED)
- [ ] T016 [US1] Implement the MCP tools wrapping the CLI (mirror contracts/mcp.md) (GREEN)
- [ ] T017 [P] [US1] Write UI test: `render_regimes()` exists and page registers in `app.py` (RED)
- [ ] T018 [US1] Implement `src/gefion/ui/views/regimes.py` (list, AST-builder form, detail drawer, compute action, episode timeline + bucket-frequency chart) and register in `src/gefion/ui/app.py` (GREEN)
- [ ] T019 [US1] Docs: add `regime` commands to README CLI Reference + `docs/USER_GUIDE.md`; add MCP tools to `docs/MCP_WORKFLOWS.md`; create `docs/REGIMES.md` (rationale, causal-label rule, compositional/continuous conditioning, persistence, **market-regime ≠ ML-regime** note); ensure `tests/test_docs_drift.py` passes

**Checkpoint**: MVP — regimes defined and computed end-to-end across all three surfaces.

---

## Phase 4: User Story 2 — Regime-Sliced Backtest Reporting (Priority: P1)

**Goal**: Slice an existing backtest by a regime; per-regime metrics that reconcile to aggregate.
**Independent test**: Run a backtest with `--by-regime`; per-bucket metrics carry effective-N +
low-power flags and reconcile to the un-sliced totals; runs without the flag are unchanged.

- [ ] T020 [P] [US2] Write `tests/test_regime_slicing.py`: per-regime metrics via `backtest.metrics` on label-filtered equity segments, reconciliation to aggregate (FR-009), opt-in causes zero change when absent (FR-007), low-power/flicker flags, `undefined` residual line (RED)
- [ ] T021 [US2] Implement `src/gefion/regimes/slicing.py`: join dated equity/trades to labels, compute per-bucket metrics (reuse `backtest/metrics.py`), effective-N, reconciliation assertion, flags (GREEN)
- [ ] T022 [US2] Add `--by-regime` option to `backtest run` in `src/gefion/cli.py` (test in `test_regime_interfaces.py` first, then impl) — additive `by_regime` output block
- [ ] T023 [US2] Extend `backtest_run` MCP tool with optional `by_regime` arg returning the sliced block (test → impl)
- [ ] T024 [US2] Extend `src/gefion/ui/views/backtest.py`: "Slice by regime" selector + per-regime metric blocks with sample-size/low-power badges + reconciliation indicator (test → impl)
- [ ] T025 [US2] Docs: `docs/BACKTESTING.md` per-regime metrics + reconciliation + low-power flagging; README/USER_GUIDE `--by-regime`; docs-drift green

**Checkpoint**: "does trend follow pay only in high-vol?" answerable across CLI/MCP/UI.

---

## Phase 5: User Story 5 — Continuous (Graded) Conditioning (Priority: P2)

**Goal**: One-coefficient interaction test for how a signal's edge scales with a variable.
**Independent test**: Recovers a planted linear gradient with one p-value; silent on a flat edge.

- [ ] T026 [P] [US5] Write `tests/test_regime_interaction.py`: OLS `return ~ signal + var + signal×var` with HAC/Newey-West SE recovers planted gradient; reports no significant interaction when flat (RED)
- [ ] T027 [US5] Implement `src/gefion/regimes/interaction.py` (statsmodels OLS + HAC errors; returns coef, p-value, n, effective_n) (GREEN)
- [ ] T028 [US5] `gefion regime interaction` CLI (test → impl) in `src/gefion/cli.py`
- [ ] T029 [US5] `regime_interaction` MCP tool (test → impl)
- [ ] T030 [US5] UI interaction panel in `src/gefion/ui/views/regimes.py` (test → impl)
- [ ] T031 [US5] Docs: interaction section in `docs/REGIMES.md` + USER_GUIDE; docs-drift green

---

## Phase 6: User Story 3 — Regime-Conditional Experiment Verdicts (Priority: P2)

**Goal**: Per-regime holdout p-values entered into one flat Benjamini-Hochberg family.
**Independent test**: A signal real only in one synthetic regime is flagged significant there and
non-significant elsewhere; the family size = realized K×R×buckets; low-power/undefined fail closed.

- [ ] T032 [P] [US3] Write `tests/test_regime_conditional.py`: per-regime holdout p-values, family = realized K×R×buckets fed to `experiments.statistical.apply_fdr` (flat BH, FR-011), fail-closed on low-power/undefined (FR-012), holdout labels causal/no-lookahead (RED)
- [ ] T033 [US3] Implement `src/gefion/regimes/conditional.py`: slice the holdout window by label (reuse `experiments.holdout`), `compute_holdout_pvalue` per bucket, assemble the family, call `apply_fdr` (GREEN)
- [ ] T034 [US3] Add `--by-regime` to `experiment run` in `src/gefion/cli.py` (test → impl)
- [ ] T035 [US3] Extend `experiment_run` MCP tool with `by_regime` (test → impl)
- [ ] T036 [US3] Extend `src/gefion/ui/views/experiments.py`: per-regime p-value column + FDR chart; low-power shown as "no verdict (fail-closed)" (test → impl)
- [ ] T037 [US3] Docs: conditional-FDR section in `docs/REGIMES.md` + USER_GUIDE; docs-drift green

---

## Phase 7: User Story 4 — Interface Parity & Cross-Cutting Polish (Priority: P3)

**Goal**: Prove CLI/MCP/UI parity, update operator surfaces, finalize observability/docs.
**Independent test**: Every operation returns consistent results across all three surfaces.

- [ ] T038 [US4] Write `tests/test_regime_interfaces.py` parity assertions: each operation reachable and equivalent across CLI `--json`, MCP, and UI service calls (RED → GREEN as surfaces land)
- [ ] T039 [US4] Update the `/gefion` operator skill (`.claude/commands/gefion.md`) tool routing to include the new `regime_*` MCP tools (Constitution III)
- [ ] T040 [US4] Add a short regime aside to `.claude/commands/gefion-learn.md` (curriculum already threads CLI/MCP/UI + linked terms)
- [ ] T041 [US4] Update `.specify/memory/progress.md` (new capability) and `.specify/memory/backlog.md` (remove done; note 006 next)
- [ ] T042 Observability pass: run each `regimes/` operation with `OTEL_ENABLED=true`, `gefion span-check` — confirm spans present, no orphaned/unparented spans
- [ ] T043 Run full suite against a freshly-created `gefion_test` (drop first, conftest recreates); confirm `tests/test_docs_drift.py` and all regime tests pass

---

## Dependencies & Story Completion Order

```
Setup (P1: T001–T003)
   └─> Foundational (P2: T004–T010)   ← schema + AST + persistence; BLOCKS all stories
          ├─> US1 (P3: T011–T019) 🎯 MVP — define & compute
          │       └─> US2 (P4: T020–T025) — slicing needs labels (US1)
          │       └─> US5 (P5: T026–T031) — interaction needs conditioning vars (US1)
          │       └─> US3 (P6: T032–T037) — conditional eval needs labels (US1)
          └─> US4 (P7: T038–T043) — parity/polish; verifies surfaces from US1–US3
```

- **US1 is the hard prerequisite** for US2/US3/US5 (all need computed labels). US2, US5, US3 are
  mutually independent once US1 lands and can proceed in parallel by different contributors.
- US4 (parity/polish) closes after the surfaces exist.

## Parallel Execution Examples

- **Foundational**: T007 (definitions test) ∥ nothing else until T004–T006 (schema) land; then T008→T010.
- **Within US1**: T011 (labels test) ∥ T015 (MCP test) ∥ T017 (UI test) — different files. Impl
  tasks T012/T014/T016/T018 follow their tests.
- **Across stories (post-US1)**: US2 (T020–T025) ∥ US5 (T026–T031) ∥ US3 (T032–T037) — separate files.

## Implementation Strategy

- **MVP = Phase 1 + 2 + US1** (T001–T019): a working "define → compute → inspect" regime across
  CLI/MCP/UI. Independently valuable and demoable on the current dataset.
- **Increment 2 = US2** (the empirical payoff — sliced backtests).
- **Increment 3 = US5 + US3** (graded conditioning + honest conditional verdicts).
- **Increment 4 = US4** (parity proof + operator/docs polish).

## Success Criteria Mapping

- SC-001 → US1 (T011–T019) · SC-002/006 → US2 (T020–T025) · SC-003/004 → US3 (T032–T037) ·
  SC-007 → US5 (T026–T031) · SC-005 → US4 (T038, T043) · SC-008 → provenance in T012/T021/T033.
