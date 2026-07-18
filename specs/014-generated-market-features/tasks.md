# Tasks: Generated Market-Level Features with an Owner Gate

**Input**: Design documents from `/specs/014-generated-market-features/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: MANDATORY (Constitution II — TDD is non-negotiable). Every
implementation task is preceded by a test task in the same phase; tests must
FAIL before implementation begins and commit together with it.

**Organization**: Grouped by user story; each story is an independently
testable, shippable increment. DDL was owner-approved 2026-07-18.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup (Schema — owner-approved DDL)

**Purpose**: The `market_function_candidates` table exists everywhere a
database can be initialized.

- [X] T001 Write failing schema tests (table created by db-init; columns, CHECKs, UNIQUE(name,version)) in tests/test_market_candidates.py
- [X] T002 Add approved DDL to sql/schema.sql AND sql/migrations/20260718_000001_market_function_candidates.sql (two-file rule, exact DDL from data-model.md)
- [X] T003 Canonical creator for market_function_candidates in src/gefion/db/schema.py wired into db-init; T001 tests green

---

## Phase 2: Foundational (Candidate store)

**Purpose**: The store primitives US1 and US3 build on. (US2 does NOT depend
on this phase — composites never touch the candidates table.)

- [X] T004 Write failing store tests (create with provenance; auto version-bump on same name — never overwrite; list by state; get; record_dry_run; review-state guardrails: no transitions out of rejected) in tests/test_market_candidates.py
- [X] T005 Implement candidate store (create/list/get/record_dry_run, observability spans with parent propagation) in src/gefion/macro/candidates.py

**Checkpoint**: store green — US1 can start; US2 can start any time after Phase 1

---

## Phase 3: User Story 1 — Machine-proposed market series behind an owner gate (Priority: P1) 🎯 MVP

**Goal**: Generate candidate market bodies (cycle or explicit), review them
(code + inputs + provenance + seeded dry-run), approve→promote or
reject→audit; approved series join the nightly derive untouched.

**Independent Test**: quickstart.md "gate walk" — propose, verify refusal
pre-approval on every execution path, review, approve, derive computes it;
reject path retains audit.

### Tests for User Story 1 (write FIRST, verify RED)

- [X] T006 [P] [US1] Write failing dry-run tests (seeded synthetic cross-section is deterministic; ok result with sample values; sandbox violation and wrong-shape mark dry_run failed) in tests/test_market_candidate_dryrun.py
- [X] T007 [US1] Write failing gate tests (approve refuses on failed dry-run or non-pending state; approve atomically promotes — feature_functions row scope='market' active + paired feature_definitions, zero orphans, promoted_function_id recorded; reject requires reason, terminal, retained; pending/rejected candidates produce zero stored values through scheduled derive, explicit derive, and full recompute — SC-1401) in tests/test_market_candidates.py
- [X] T008 [P] [US1] Write failing generation tests (cycle runner market path writes ONLY candidates, never feature_functions; provenance recorded; template fallback when synthesis unavailable; honest no-candidate on total failure; cycle summary reports candidate id) in tests/test_cycle_runner_market_gen.py
- [X] T009 [P] [US1] Write failing CLI tests (macro candidate list|show|approve|reject, macro propose; refusal wording names the gate; --json on all) in tests/test_market_candidates_cli.py

### Implementation for User Story 1

- [X] T010 [US1] Seeded synthetic cross-section generator + sandbox dry-run runner (stores dry_run JSONB) in src/gefion/macro/candidates.py
- [X] T011 [P] [US1] Market-scope generation templates (participation/concentration/breadth classes, market contract compute(rows)) in src/gefion/macro/market_bodies.py
- [X] T012 [US1] Cycle-runner market-scope generation path (Claude prompt variant stating the market contract + template fallback → candidate store; per-stock path untouched) in src/gefion/experiments/cycle_runner.py
- [X] T013 [US1] approve/reject/promote (atomic promotion via existing upsert_feature_function + definition pairing; approver/timestamp/reason recording) in src/gefion/macro/candidates.py
- [X] T014 [US1] CLI commands macro candidate list|show|approve|reject + macro propose (get_output presentation, --json) in src/gefion/cli.py
- [X] T015 [US1] MCP tools macro_candidate_list/show/approve/reject + macro_propose in mcp-server/server.py; parity assertions extended in tests/test_regime_interfaces.py (write assertions first, RED, then implement)
- [X] T016 [US1] UI candidates queue + read-only review packet in src/gefion/ui/views/ (expected-view test first in tests/test_ui_components.py, RED, then implement)
- [X] T017 [US1] Docs increment: docs/USER_GUIDE.md (commands + gate concept), docs/MCP_WORKFLOWS.md (review workflow), README.md command list

**Checkpoint**: US1 shippable — the machine proposes, a human owns the gate

---

## Phase 4: User Story 2 — Market series computed from other market series (Priority: P2)

**Goal**: Owner-authored composite functions over declared macro series:
per-date named-value row in, one value or gap out, macro-home output,
cycle refusal, topological derive ordering.

**Independent Test**: register a composite over three existing series,
derive full history, verify values/gaps/idempotence — no Story 1 machinery
involved.

### Tests for User Story 2 (write FIRST, verify RED)

- [X] T018 [US2] Write failing composite tests (executor: values computed from exactly that date's stored inputs; missing-input date = gap never imputed; NaN/None = gap; non-numeric = error; failing body writes nothing; disabled input series = reported skip; registration: unknown/disabled/empty series refuse naming the series; cycle refusal incl. transitive through composite-produced series; derive: incremental only-missing-dates, idempotent rerun, --full recompute; topological ordering after inputs) in tests/test_macro_composites.py
- [X] T019 [P] [US2] Write failing CLI test (macro register-composite validates and registers; refusals surface; --json) in tests/test_market_candidates_cli.py

### Implementation for User Story 2

- [X] T020 [US2] run_composite_function (per-date named-series rows, pivot query over macro_series_values, 011 value/gap/failure semantics, spans) in src/gefion/features/dispatcher.py
- [X] T021 [US2] Composite registration + input validation + DFS cycle refusal + topological order helper in src/gefion/macro/composites.py
- [X] T022 [US2] Derive orchestration: input-shape dispatch (series→composite executor) and composites-after-inputs ordering in src/gefion/macro/derived.py
- [X] T023 [US2] CLI macro register-composite in src/gefion/cli.py; MCP tool macro_register_composite in mcp-server/server.py (parity assertions first, RED)
- [X] T024 [US2] Docs increment: docs/USER_GUIDE.md composite section (contract, gap honesty, ordering), README.md

**Checkpoint**: US1 and US2 independently shippable

---

## Phase 5: User Story 3 — Generation targets composites too (Priority: P3)

**Goal**: Machine-proposed composite candidates: same queue, same gate,
dry-run over seeded series values, promotion validates declared inputs.

**Independent Test**: propose --kind composite, verify identical gate
semantics, approve, observe nightly computation.

### Tests for User Story 3 (write FIRST, verify RED)

- [X] T025 [P] [US3] Write failing composite-candidate tests (dry-run executes over seeded values for declared series; kind='composite' candidates declare only existing macro series — generation refuses otherwise; promotion runs composite input validation incl. cycle refusal) in tests/test_market_candidate_dryrun.py
- [X] T026 [P] [US3] Write failing generation tests (composite templates + Claude prompt variant for --kind composite; candidate lands pending with series inputs) in tests/test_cycle_runner_market_gen.py

### Implementation for User Story 3

- [X] T027 [US3] Composite dry-run input shape (seeded series values) + promotion-time composite validation hookup in src/gefion/macro/candidates.py
- [X] T028 [US3] Composite generation templates in src/gefion/macro/market_bodies.py + --kind composite through cycle runner and macro propose in src/gefion/experiments/cycle_runner.py and src/gefion/cli.py
- [X] T029 [US3] Docs increment: quickstart Story 3 flow reflected in docs/USER_GUIDE.md

**Checkpoint**: all three stories independently functional

---

## Phase 6: Polish & Cross-Cutting

- [X] T030 [P] Learning materials: gate + composite aside in .claude/commands/gefion-learn.md
- [X] T031 [P] Operator skill routing for new MCP tools in .claude/commands/gefion.md
- [X] T032 [P] docs/ARCHITECTURE.md (candidate flow + composite mode) and DATA_DICTIONARY regen for the new table
- [X] T033 Trace verification: gefion span-check over propose/review/derive paths (orphaned spans are defects); fix any slow spans surfaced by the Tempo hook
- [X] T034 Pre-flight: drop gefion_test, full suite green against a fresh DB (ENABLE_DB_TESTS=1); docs-drift test covers all new commands/tools
- [X] T035 Quickstart validation: walk specs/014-generated-market-features/quickstart.md end-to-end on dev

---

## Dependencies & Execution Order

- **Phase 1 (schema)** blocks everything.
- **Phase 2 (store)** blocks US1 and US3; **US2 depends only on Phase 1**
  (it never touches the candidates table) and can run in parallel with
  Phase 2 + US1.
- **US3 depends on US1 (gate) AND US2 (composite validation/executor).**
- Polish depends on all delivered stories.
- Within every story: test tasks precede implementation tasks (RED first);
  tests and implementation commit together.

### Parallel opportunities

- T006/T008/T009 (different test files) in parallel; T011 parallel to T010.
- The whole of Phase 4 (US2) in parallel with Phase 2+3 (different modules:
  dispatcher/composites/derived vs candidates/cycle_runner).
- T025/T026 in parallel; T030/T031/T032 in parallel.

---

## Implementation Strategy

**MVP = Phase 1 + 2 + 3 (US1)**: the gate walk shippable and demoable on its
own. Then US2 (independently valuable composites), then US3 (generation
meets composites). Ship each story's surfaces + docs in the same increment;
stop at any checkpoint and validate the story independently. Deployment to
prod is `git pull` + `gefion db-init` (migration applies the approved DDL).
