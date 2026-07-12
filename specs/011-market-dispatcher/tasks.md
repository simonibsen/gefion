# Tasks: Market-Level Feature Dispatcher Mode (011)

**Input**: spec.md (3 stories), plan.md (6 increments), research.md (R1–R7),
data-model.md (approved DDL), contracts/ (cli, function-contract)
**TDD**: NON-NEGOTIABLE — every implementation task is preceded by a RED test task.
**Fixtures**: canonical schema creators up front (CI collection-order lesson).

## Phase 1: Setup + approved DDL

- [ ] T001 Write schema test (RED) in `tests/test_market_dispatcher.py`: `feature_functions.scope` exists, defaults 'stock', CHECK rejects a third value; `feat-fx-list --json` payload includes scope
- [ ] T002 Apply the approved DDL via two-file rule: `sql/schema.sql` + `sql/migrations/20260713_000001_feature_function_scope.sql`; regen `docs/DATA_DICTIONARY.md`; add scope to `feat-fx-list` output in `src/gefion/cli.py` (GREEN)

## Phase 2: Foundational — market execution mode (blocking)

- [ ] T003 Write executor tests (RED) in `tests/test_market_dispatcher.py`: a scope='market' body's `compute(rows)` runs in the sandbox per date over a synthetic world (rows carry symbol/close/high/low/volume + declared `inputs.features` columns); returns floats → collected per date; None/NaN/inf → gap; raise → function-level failure with zero writes; forbidden import → sandbox refusal, zero writes; thin days (< min_stocks) never reach the body
- [ ] T004 Implement `run_market_function(conn, fn_row, start, min_stocks)` in `src/gefion/features/dispatcher.py`: ONE streamed server-side cursor (stock_ohlcv ⋈ stocks[asset_type='Stock'] ⟕ declared feature columns, plus computed `ret_20` when declared), per-date grouping, sandboxed `compute` calls via `_exec_in_sandbox`, buffered results, write-on-success only (GREEN)

## Phase 3: US1 — Market functions live and run from the registry (P1) 🎯 MVP

- [ ] T005 [US1] Write derive-orchestration tests (RED) in `tests/test_macro_derived.py`: `derive_series` executes the DB body (edit body in DB → output changes; proves DB is source of truth); seeding is create-if-absent (redeploy never clobbers); incremental from last stored date per function; values land on the same series ids/feature names as today
- [ ] T006 [US1] Rewrite `src/gefion/macro/derived.py` as seeds+orchestration over the dispatcher; create `src/gefion/macro/market_bodies.py` with the two seed Python bodies per contracts/function-contract.md (GREEN — legacy SQL stays until T008)
- [ ] T007 [US1] Write migration-equality test (RED-able) in `tests/test_market_dispatcher.py`: legacy SQL output == dispatcher output per date within 1e-9 on the same synthetic world, for both series
- [ ] T008 [US1] Delete the legacy SQL path from `derived.py` once T007 is GREEN; `macro derive` fully dispatcher-backed; `--reseed <name>` explicit-overwrite flag added to `src/gefion/cli.py` per contracts/cli.md

## Phase 4: US2 — Full lifecycle applies (P2)

- [ ] T009 [US2] Write lifecycle tests (RED) in `tests/test_market_dispatcher.py`: disabled market function → derive skips-and-reports (never computes, never silently drops); re-enable → resumes; `feat-def-validate` zero orphans post-migration; `feat-fx-export`/`import` round-trips a market function incl. scope + inputs; import refuses a market body whose `inputs.features` name unknown definitions
- [ ] T010 [US2] Implement: derive skip/report path in `src/gefion/macro/derived.py`; scope+validation in feat-fx-import (`src/gefion/cli.py`); export includes scope (GREEN)

## Phase 5: US3 — Honest failure and honest gaps (P2)

- [ ] T011 [US3] Write failure-isolation tests (RED) in `tests/test_market_dispatcher.py`: two market functions, one raising — healthy one completes and writes, failing one writes zero and is reported with reason; CLI exit non-zero on partial failure; retry after failure resumes the failed function's full pending range; wrong-shape return (str/list) → failure not garbage
- [ ] T012 [US3] Implement per-function isolation + derive report + exit status in `src/gefion/macro/derived.py` and `src/gefion/cli.py` (GREEN)

## Phase 6: Polish & Cross-Cutting

- [ ] T013 [P] Docs: USER_GUIDE (edit-a-market-body workflow, --reseed), DEVELOPMENT.md (DB-is-source-of-truth seed pattern), README row unchanged check; MCP notes in docs/MCP_WORKFLOWS.md (scope in listings); docs-drift green
- [ ] T014 [P] Curriculum: Module 2 update in `.claude/commands/gefion-learn.md` — market-level function code now lives in the database; repo seeds once; checkpoint touch
- [ ] T015 Observability pass: derive span parents per-function spans with timing/rows; `gefion span-check` no orphans; SC-1102 timing measured on sloth
- [ ] T016 Fresh-DB pre-flight (drop gefion_test, full suite, exit code captured); PR; merge on green
- [ ] T017 Prod migration: deploy → db-migrate (scope column) → seed → `macro derive --full` → spot-equality vs previously stored history (sampled dates) → `feat-def-validate` zero orphans → update #114 with v1-shipped status (machine-generation remains)

## Dependencies

```
T001→T002 → T003→T004 → [US1: T005→T006→T007→T008] → [US2: T009→T010] ∥ [US3: T011→T012] → T013/T014 [P] → T015→T016→T017
```

US2 and US3 are independent after US1; T013/T014 parallel.

## MVP scope

Phases 1–3 (T001–T008): market functions in the DB, executing, migrated, equality-proven.
