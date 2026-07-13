# Tasks: Sector-State Signals for Discovery (013)

**Input**: plan.md (6 increments), spec.md (3 stories), research.md (R1–R6),
contracts/cli.md. TDD is constitutional: every task pair is test-first.

## Phase 1: Setup

*(none — existing repo, no scaffolding needed)*

## Phase 2: Foundational (blocking)

- [ ] T001 Write dispatcher sector-column test (rows carry `sector`, NULL present as None; existing bodies unaffected) in tests/test_market_dispatcher.py — RED
- [ ] T002 Add `s.sector` to the streamed SELECT + row dicts in src/gefion/features/dispatcher.py `run_market_function` — GREEN; commit together

## Phase 3: User Story 1 — sector series as features (P1)

- [ ] T003 [US1] Write two-sector synthetic-world tests in tests/test_sector_signals.py: planted opposite drifts → rs signs correct (SC-1301); MIN_MEMBERS floor → gap; NULL-sector exclusion; slug normalization ("FINANCIAL SERVICES" → financial_services); idempotent re-derive — RED
- [ ] T004 [US1] Implement `sector_slug` + `sector_signal_bodies(sector)` generator (rs + breadth bodies, MIN_MEMBERS=30 inline) in src/gefion/macro/market_bodies.py — GREEN
- [ ] T005 [US1] Write seed-sectors door tests in tests/test_sector_signals.py: census floor skips thin sectors (reported); create-if-absent (DB wins on re-run); unknown --sectors refuses listing census; slug collision refuses — RED
- [ ] T006 [US1] Implement `macro seed-sectors` CLI (census from stocks.sector, parameterized) in src/gefion/cli.py + seeding helper in src/gefion/macro/derived.py — GREEN
- [ ] T007 [US1] Write `derive --series all` semantics test (planted enabled DB market fn outside SEED_BODIES derived by 'all'; disabled skipped-reported) in tests/test_sector_signals.py — RED
- [ ] T008 [US1] Change 'all' expansion to SEED_BODIES ∪ enabled scope='market' DB functions in src/gefion/cli.py macro_derive — GREEN

## Phase 4: User Story 2 — discovery-ready (P1)

- [ ] T009 [US2] Write e2e discovery test in tests/test_discovery_sector_atoms.py: synthetic hunt pre-registers macro_sector_rs_* terciles, completes with guarantees intact (SC-1303); uncomputed sector series → uncomputable-proposal diagnostic — RED (expected mostly GREEN already; pins the contract)
- [ ] T010 [US2] Fix anything the e2e test surfaces (expected: none) — GREEN

## Phase 5: Polish & surfaces

- [ ] T011 [P] Docs: USER_GUIDE macro section (seed-sectors, naming, floors, 'all' semantics), REGIMES sector vocabulary + membership-vintage caveat, DEPLOYMENT cron note — drift suite green
- [ ] T012 [P] Curriculum M10 aside (sector states as new conditioning dimension) in .claude/commands/gefion-learn.md; /gefion routing row in .claude/commands/gefion.md
- [ ] T013 [P] MCP parity: macro_seed_sectors tool in mcp-server/server.py + docs/MCP_WORKFLOWS.md entry
- [ ] T014 Fresh-DB full suite green; PR; merge

## Phase 6: User Story 3 — production (P2, post-merge)

- [ ] T015 [US3] Prod: deploy, `macro seed-sectors`, timed `macro derive --series all` full history (record duration → #120 evidence)
- [ ] T016 [US3] Launch sector hunt (h=20d, holdout 80wk, top-6 sector rs/breadth terciles + market vocabulary, declared seed) and report verdict with provenance (task #48)

## Dependencies

T001→T002 → US1 (T003..T008 sequential pairs) → US2 (T009,T010) →
Polish (T011,T012,T013 parallel; T014 last) → US3 (T015→T016).

## MVP

US1 alone (series exist, computable, honest) is shippable; US2 is a proof
test; US3 is operations.
