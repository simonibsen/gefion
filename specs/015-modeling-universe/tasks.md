# Tasks: Modeling Universe Membership (015)

**Input**: plan.md, spec.md, research.md, data-model.md, contracts/cli-mcp.md, quickstart.md
**TDD**: NON-NEGOTIABLE — within every phase, test tasks precede implementation tasks; tests must fail (Red) before implementation (Green); tests + implementation commit together.
**DDL**: approved by owner 2026-07-19 (data-model.md).

## Phase 1: Setup — schema (blocking everything)

- [ ] T001 Write failing schema tests: creators exist + idempotent + mirror schema.sql (both new tables) in tests/test_universe_definitions.py (schema section); fixtures call canonical creators per house rule
- [ ] T002 Add approved DDL to sql/schema.sql AND sql/migrations/20260719_000001_universe_membership.sql (two-file rule, identical final state)
- [ ] T003 Add create_universe_definitions_table + create_universe_exclusions_table to src/gefion/db/schema.py; wire into db-init table creation; run db-init on dev DB; T001 tests green

## Phase 2: Foundational — core package (blocks all user stories)

- [ ] T004 [P] Write failing definition tests in tests/test_universe_definitions.py: create/update/get/list, validation refusals (unknown attribute, unknown op, missing reason, reserved name `all`), fingerprint stable under key order + changes iff rules/pins change, pins validation, single-default constraint, enable/disable, disable-default refusal
- [ ] T005 [P] Write failing membership tests in tests/test_universe_membership.py: static rule → one open-ended interval per matching symbol; close-rule gaps-and-islands intervals; as-of membership on both sides of a crossing date; determinism (re-refresh = identical rows + delta of zero); reconcile delta (add/remove rule); FR-010 guard (empty → refuse always; >25pp shrink → refuse unless --force); explain_symbol member + excluded cases
- [ ] T006 Implement src/gefion/universe/definitions.py (CRUD, validation vs attribute registry, canonical-JSON sha256 fingerprint, seed_default_universe for db-init) — T004 green
- [ ] T007 Implement src/gefion/universe/evaluate.py (attribute registry: stocks statics + close time-varying; per-rule interval generation: static SQL + islands over stock_ohlcv) — first half of T005 green
- [ ] T008 Implement src/gefion/universe/membership.py (refresh reconcile, guard, as-of queries, explain_symbol, flap counts) — T005 fully green
- [ ] T009 Write failing resolve-API tests in tests/test_universe_chokepoint.py (name > `all` > default resolution; unknown/disabled → refusal listing valid names; universe_exclusion_clause SQL shape) then implement src/gefion/universe/__init__.py public API — green

**Checkpoint**: core machinery complete; observability spans (create_span) on refresh/evaluate/members with counts.

## Phase 3: User Story 1 — clean default modeling universe (P1) 🎯 MVP

- [ ] T010 [US1] Extend tests/test_universe_definitions.py: db-init seeds modeling_default (two rules, is_default, idempotent re-seed) — red, then implement seeding in db-init path — green
- [ ] T011 [US1] Write failing consumer-routing tests in tests/test_universe_chokepoint.py: dataset symbols resolution + export fallback respect default universe and `all` control; run_market_function cross-section excludes excluded symbols; cross-sectional ranking population excludes; backtest loader excludes; cycle_runner experimental-feature population excludes; discovery base list routed
- [ ] T012 [US1] Route src/gefion/ml/dataset.py (resolve_universe_symbols + export fallback at dataset.py:69/191) through chokepoint with --universe manifest key
- [ ] T013 [US1] [P] Add universe_exclusion_clause to run_market_function SQL in src/gefion/features/dispatcher.py (~line 1572); derive passes universe context from src/gefion/macro/derived.py; add --recompute full-history path if absent
- [ ] T014 [US1] [P] Route src/gefion/compute/cross_sectional.py fetch_feature_with_sectors (~line 346) via exclusion clause (replaces status-Inactive ad-hoc filter, kept as belt-and-braces)
- [ ] T015 [US1] [P] Route src/gefion/backtest/data_loader.py (both selectors) through chokepoint
- [ ] T016 [US1] [P] Route src/gefion/experiments/cycle_runner.py:1079 experimental-feature population through chokepoint
- [ ] T017 [US1] [P] Route regime discovery: base list sites (src/gefion/cli.py:~13805, src/gefion/regimes/discovery/spa.py:~262) + market-mean SQL (src/gefion/regimes/discovery/signals.py:~271) + chain records universe name/fingerprint in search_space
- [ ] T018 [US1] [P] Route remaining symbol-list sites: ml-predict population (src/gefion/cli.py:~1754), src/gefion/ml/e2e.py:~323, volatility compute (src/gefion/cli.py:~10101)
- [ ] T019 [US1] db-health/system_status universe headline + missing-default warning (test in tests/test_universe_cli.py first); Tempo span-check on market-function traces before/after (constitution IV)

**Checkpoint**: US1 independently testable — dataset + breadth series exclude shells/ETFs; `all` control includes them.

## Phase 4: User Story 2 — add a rule without code (P2)

- [ ] T020 [US2] Write failing CLI tests in tests/test_universe_cli.py: universe define --rules-file (create + update + fingerprint change), refusal messages name valid attributes/ops, refresh prints delta + guard refusal + --force, show displays rules/reasons/flap counts
- [ ] T021 [US2] Implement CLI command group core in src/gefion/cli.py: universe define/list/show/refresh/enable/disable (shared presentation module, --json) — T020 green

## Phase 5: User Story 3 — date-aware membership (P3)

- [ ] T022 [US3] Write failing tests in tests/test_universe_cli.py: universe members --as-of, universe explain --as-of (both sides of a crossing date); then implement members/explain CLI — green (core as-of machinery already green from T005/T008)

## Phase 6: User Story 4 — provenance (P4)

- [ ] T023 [US4] Write failing tests in tests/test_universe_provenance.py: ml_datasets.universe JSONB gains name/fingerprint/resolved_count; train result dict + save/load artifact metadata round-trip universe stamp (device pattern); experiments.config stamp inherited from dataset
- [ ] T024 [US4] Implement stamps: src/gefion/ml/dataset.py (manifest), src/gefion/ml/models.py (result + artifact metadata), src/gefion/experiments/cycle_runner.py (config) — green

## Phase 7: Polish — surfaces, docs, learning (same-increment rule)

- [ ] T025 Write failing tests then implement export/import (YAML round-trip, --dry-run diff) in src/gefion/universe/definitions.py + CLI in src/gefion/cli.py (tests/test_universe_cli.py)
- [ ] T026 Write failing deletion-door tests then implement src/gefion/universe/deletion.py + universe delete CLI (dry-run default, dependency enumeration by fingerprint refs, refusal while referenced, --confirm)
- [ ] T027 MCP tools (8) + dispatch in mcp-server/server.py: universe_list/show/members/explain/refresh/define/delete/export+import; destructive/human-directed flags per contracts/cli-mcp.md; /gefion operator skill routing updated (.claude/commands/gefion.md)
- [ ] T028 [P] UI read-only universe card on system page (src/gefion/ui/views/) — counts + by-rule breakdown
- [ ] T029 [P] Docs: docs/USER_GUIDE.md (universe section + consumer --universe flags), README.md, docs/MCP_WORKFLOWS.md, docs/ARCHITECTURE.md (package + tables + chokepoint convention), docs/DEVELOPMENT.md (new cross-section consumers MUST route through gefion.universe), regenerate DATA_DICTIONARY
- [ ] T030 [P] Learning: .claude/commands/gefion-learn.md aside "the modeling universe" (concept-first) + checkpoint question
- [ ] T031 Full suite on fresh gefion_test (drop first per pre-flight rule); fix stragglers; verify docs-drift test green

## Phase 8: Rollout (prod, after PR merge — quickstart.md runbook)

- [ ] T032 Deploy to sloth (git pull + gefion db-init), universe refresh, inspect delta (~458 shells + ~1268 ETFs)
- [ ] T033 FR-013 vintage change: recompute derived market series full history; re-derive regime labels conditioned on them; run 013 signal SPA re-verdict; record outcome
- [ ] T034 Add universe refresh to nightly cron (after feat-compute, before macro derive); record vintage-change observation on the operating ledger; update memory

## Dependencies

- Phase 1 → 2 → 3 strictly ordered (schema → core → consumers).
- Phase 4/5/6 depend on Phase 2; 4 and 6 also touch files from Phase 3 (sequence after T012).
- Within Phase 3: T012 first (dataset is the reference consumer), then T013–T018 parallelizable [P] (different files), T019 last.
- Phase 7 after all stories; T028–T030 parallelizable. Phase 8 after merge.

## MVP scope

Phases 1–3 (T001–T019): the default universe cleans every cross-section and is independently demonstrable via the quickstart SC-001 walk.
