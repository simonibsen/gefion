# Tasks: Agentic Regime Discovery

**Input**: Design documents from `/specs/006-agentic-regime-discovery/`
**Prerequisites**: plan.md, spec.md (clarified), research.md, data-model.md, contracts/ (all present); DDL owner-approved 2026-07-07

**Tests**: INCLUDED — TDD is non-negotiable (Constitution II); the negative-control suite is
the feature's own acceptance proof and runs in CI. Every implementation task is preceded by a
failing test (Red → Green), committed together.

**Delivery rule** (plan, mandatory): each story lands its CLI/MCP/UI surface and docs *in the
same increment* — not deferred to polish.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on incomplete tasks)
- Stories: US1 nested segregation (P1) · US2 search-aware counting (P1) · US4 negative
  control (P1) · US3 principle-seeded proposal + expressive tier (P2) · US6 grading +
  diagnostics (P2) · US5 reproducible/auditable across surfaces (P2)

---

## Phase 1: Setup

- [ ] T001 Create package scaffold `src/gefion/regimes/discovery/__init__.py` importing from `gefion.observability`
- [ ] T002 [P] Create synthetic-data generator helpers in `tests/discovery_synth.py` (seeded GBM prices + noise features for a ~20-symbol toy universe; planted-regime injection helper) — pure test infrastructure, itself unit-tested

## Phase 2: Foundational (blocking all stories)

- [ ] T003 Write `tests/test_discovery_schema.py`: after db-init, the four approved tables exist with expected columns/constraints (status/tier/verdict CHECKs, UNIQUE(run_id,candidate_hash), UNIQUE(candidate_id,fold,descriptive)) (RED)
- [ ] T004 Apply the approved DDL via the two-file rule: `sql/schema.sql` + `sql/migrations/20260707_000001_regime_discovery.sql`; regenerate `docs/DATA_DICTIONARY.md` (`gen_data_dictionary.py --write`) in the same commit (GREEN)
- [ ] T005 [P] Write `tests/test_discovery_grammar.py`: atom-library validation, deterministic enumeration to depth K, exact candidate count (= family denominator input), canonical-AST content hashing, dedup, depth-cap refusal (RED)
- [ ] T006 Implement `src/gefion/regimes/discovery/grammar.py` (GREEN)
- [ ] T007 [P] Write `tests/test_discovery_universe.py`: filter chain composition; built-ins `test_tickers` (ZVZZT family), `asset_type`; explicit `passthrough` accepted only when declared; chain recorded for pre-registration (RED)
- [ ] T008 Implement `src/gefion/regimes/discovery/universe.py` (GREEN)
- [ ] T009 [P] Write `tests/test_discovery_ledger.py`: run pre-registration row (search_space incl. all three seams), status lifecycle (`pre_registered→enumerated→evaluated→complete/invalid`), candidate persistence incl. losers, `counted_in_family` invariants, diagnostics rows with sample_dependent tagging (RED, DB)
- [ ] T010 Implement `src/gefion/regimes/discovery/ledger.py` (GREEN)

**Checkpoint**: schema live; candidates enumerable, hashable, persistable; universe declared.

---

## Phase 3: US1 — Nested Discovery That Cannot See the Holdout (P1) 🎯 MVP

**Goal**: discovery/fitting structurally cannot touch the outer holdout; candidate set freezes
before any outer-holdout evaluation.
**Independent test**: leaked-split vs nested-split demonstration — nested path yields no
spurious significance where the leaked path (test-only) would.

- [ ] T011 [P] [US1] Write `tests/test_discovery_segregation.py`: `DiscoveryDataContext` exposes inner-window rows only; any outer-holdout access raises; boundaries recorded; evaluation API refuses to run before run status = `enumerated` (candidate freeze, the T4 guard); leaked-vs-nested negative demonstration on synthetic data (RED)
- [ ] T012 [US1] Implement `src/gefion/regimes/discovery/segregation.py` (DiscoveryDataContext over `experiments.holdout.HoldoutWindow`) (GREEN)
- [ ] T013 [P] [US1] Write tier-1 edge tests in `tests/test_discovery_edges.py`: per-candidate continuous-interaction evaluation (reuses `regimes.interaction`), per-observation records from `signal_source='features'`, causal label application to the outer holdout only after freeze (RED)
- [ ] T014 [US1] Implement `src/gefion/regimes/discovery/signals.py` (features source) and `src/gefion/regimes/discovery/edges.py` (tier-1 interaction path) (GREEN)
- [ ] T015 [US1] Write + implement minimal runner path in `src/gefion/regimes/discovery/runner.py`: pre-register → enumerate → freeze → evaluate (tier 1) → ledger (test in `tests/test_discovery_ledger.py` extension first)
- [ ] T016 [US1] CLI surface (test in `tests/test_discovery_interfaces.py` first): `gefion regime discover start|list|show` in `src/gefion/cli.py`
- [ ] T017 [P] [US1] MCP surface: `regime_discover_start|list|show` tools in `mcp-server/server.py` (source-inspection tests first)
- [ ] T018 [P] [US1] UI surface: Discovery tab skeleton in `src/gefion/ui/views/regimes.py` — runs table + run detail (pre-registration, segregation, status) (view test first in `tests/test_ui_components.py`)
- [ ] T019 [US1] Docs (same increment): `docs/REGIMES.md` discovery section (traps T1–T6 + nesting), README + `docs/USER_GUIDE.md` command entries, `docs/MCP_WORKFLOWS.md` tools; `tests/test_docs_drift.py` green

**Checkpoint (MVP)**: a bounded tier-1 discovery runs end-to-end with structural segregation,
visible on all three surfaces.

---

## Phase 4: US2 — Search-Aware Counting That Counts the Losers (P1)

**Goal**: one flat FDR family covering every test actually run; silent survivorship impossible.
**Independent test**: N candidates over pure noise → recorded family = N×signals×buckets and
zero survivors.

- [ ] T020 [P] [US2] Extend `tests/test_discovery_edges.py`: tier-2 grammar candidates through the conditional gate — causal bucket labels from candidate ASTs (reuse `regimes.labels`), per-bucket p-values via `regimes.conditional.conditional_pvalues`, family assembly = every (signal × candidate × bucket) incl. refused-low-power exclusions handled fail-closed (RED)
- [ ] T021 [US2] Implement tier-2 path in `edges.py` + family assembly in `runner.py`; `family_size` written to the run row and used as the single `apply_fdr` call's denominator (GREEN)
- [ ] T022 [P] [US2] Ledger assertions in `tests/test_discovery_ledger.py`: failed candidates persisted and counted; post-hoc bucket cherry-picking impossible (verdicts only derivable from the recorded family run) (RED→GREEN with any needed ledger hardening)
- [ ] T023 [US2] CLI/MCP/UI (tests first): `regime discover ledger|verdicts` + `regime_discover_ledger|verdicts` + ledger/verdict tables in the Discovery tab (losers visible; family size shown beside survivors)
- [ ] T024 [US2] Docs: counting-the-losers section in `docs/REGIMES.md`; USER_GUIDE/MCP_WORKFLOWS entries; docs-drift green

---

## Phase 5: US4 — Negative Control: Discovery Finds Nothing in Noise (P1)

**Goal**: the standing CI proof (SC-101/102).
**Independent test**: is itself the test.

- [ ] T025 [US4] Write `tests/test_discovery_negative_control.py`: full pipeline on pure-noise synthetic data across ≥20 seeds asserting **zero survivors** (SC-101); planted-regime recovery — injected conditional edge found, decoys rejected, in ≥95% of seeded runs (SC-102); budgeted < 5 min (tiny universe, K=1) (RED until pipeline complete → GREEN)
- [ ] T026 [US4] Wire the negative-control suite into CI as a required check (`.github/workflows/ci.yml` job or marker within the existing suite) and document the guarantee in `docs/REGIMES.md`

---

## Phase 6: US3 — Principle-Seeded Proposal + Expressive Tier (P2)

**Goal**: the agent proposes candidates from catalog principles; expressive tier (free-form +
sandboxed detectors) admissible only under the fresh-holdout reserve.
**Independent test**: a principle-seeded proposal yields bounded, provenance-carrying
candidates; a detector candidate is refused without a declared unconsumed reserve.

- [ ] T027 [P] [US3] Write `tests/test_discovery_freshhold.py`: reserve declaration (distinct from outer holdout), single-use consumption tracking, refusal on undeclared/consumed reserve, recorded justification path for re-declaration (RED)
- [ ] T028 [US3] Implement `src/gefion/regimes/discovery/freshhold.py` (GREEN)
- [ ] T029 [P] [US3] Write `tests/test_discovery_detectors.py`: detector candidates execute via the existing feature-function sandbox (`fit` on DiscoveryDataContext only, `label` causal); degeneracy screen (bucket >90%/<2%); stability screen (seeded refit agreement); refusals recorded as diagnostics; T3 accounting (fitted params recorded in provenance) (RED)
- [ ] T030 [US3] Implement `src/gefion/regimes/discovery/detectors.py` (GREEN)
- [ ] T031 [US3] Principle seeding (test first in `tests/test_discovery_grammar.py` extension): proposal builds atom libraries / detector candidates from catalog principles (`regime-detection-hmm`, `hurst-exponent-regime`, …) with provenance references
- [ ] T032 [US3] New experiment type (tests first in `tests/test_discovery_interfaces.py`): `src/gefion/experiments/types/regime_discovery.py` + dispatch in `src/gefion/experiments/core.py`; risk class high (never auto-approved); cycle budget maps to candidate budget
- [ ] T033 [US3] CLI/MCP/UI (tests first): expressive-tier options on `discover start` (`--fresh-holdout`, tier flags); `experiment propose --type regime_discovery` path visible in Experiments UI
- [ ] T034 [US3] Docs: expressive-tier + reserve semantics in `docs/REGIMES.md`; specs/004 cross-reference for the new experiment type; docs-drift green

---

## Phase 7: US6 — Walk-Forward Grading + Diagnostics Surfaces (P2)

**Goal**: trust accrues forward only; the diagnostics ledger becomes a legible learning signal.
**Independent test**: single-era synthetic edge admitted → flagged regime-limited on fold
failure; min-sample refusal recorded sample-dependent with quantitative reason.

- [ ] T035 [P] [US6] Write `tests/test_discovery_grading.py`: `GradingScheme` interface has no backward-confirmation API; walk-forward default registers admitted edges with probation (fold 1 = probation window); `record_forward_result` appends `regime_trust_grades` rows; backward era-slices stored `descriptive=true` and excluded from `grade()`; regime-limited flag on early fold failure (RED)
- [ ] T036 [US6] Implement `src/gefion/regimes/discovery/grading.py` + probation integration (GREEN)
- [ ] T037 [P] [US6] Diagnostics surfacing tests: `regime discover diagnostics` filters (sample-dependent vs structural), quantitative reasons rendered (RED)
- [ ] T038 [US6] CLI/MCP/UI (tests first): `discover diagnostics|grades` + `regime_discover_diagnostics|grades` + diagnostics panel and grade timeline (descriptive rows visually distinct) in the Discovery tab
- [ ] T039 [US6] Docs: grading (forward-only) + diagnostics-ledger reading guide in `docs/REGIMES.md`; USER_GUIDE/MCP_WORKFLOWS; docs-drift green

---

## Phase 8: US5 — Reproducibility, Parity & Learning Materials (P2)

**Goal**: byte-reproducible runs; three-surface parity proven; curriculum extended.
**Independent test**: same seed + inputs → identical ledger and verdicts; parity matrix green.

- [ ] T040 [US5] Write reproducibility test in `tests/test_discovery_ledger.py`: re-run with identical seed/inputs on synthetic data → identical candidate hashes, results, verdicts (RED→GREEN with any determinism fixes)
- [ ] T041 [US5] Write `tests/test_discovery_interfaces.py` parity matrix assertions: all 9 operations (contracts/interfaces.md) across CLI/MCP/UI; admitted regimes carry `origin=machine` badge and full 005 affordances
- [ ] T042 [US5] Update `/gefion` operator skill (`.claude/commands/gefion.md`): discovery mode, tool routing, honesty rules (confirm-before-start; never present unadmitted candidates as findings)
- [ ] T043 [US5] **Learning materials**: add **Module 10 — Agentic Regime Discovery** to `.claude/commands/gefion-learn.md` (concept-first: the six traps, nesting, counting losers, forward-accruing trust; Do: bounded synthetic discovery + read both ledgers; Checkpoint: why a backward era-slice can never raise a trust grade); linked terms per curriculum rules
- [ ] T044 [US5] Update `.specify/memory/progress.md` + `backlog.md` (capability shipped; bootstrap fast-follow filed as the follow-up item per Clarification Q1)

---

## Phase 9: Polish & Cross-Cutting

- [ ] T045 Observability pass: run each discovery operation with `OTEL_ENABLED=true`; `gefion span-check` — spans parented, batch-level attributes only (OTLP 4MB lesson: no per-candidate spans)
- [ ] T046 Full-suite pre-flight: drop `gefion_test`, run the complete suite against a fresh DB; docs-drift + negative-control jobs green
- [ ] T047 First real-data validation run on the prod host: bounded tier-1+2 discovery against the 26.7-year dataset (budget ≤ 100 candidates); review ledgers + diagnostics; record findings in the run ledger and session notes — **expect mostly/entirely rejections; that is success**

---

## Dependencies & Story Completion Order

```
Setup (T001–T002)
  └─> Foundational (T003–T010: schema→grammar/universe/ledger)   ← blocks all stories
        └─> US1 (T011–T019) 🎯 MVP — segregation + tier-1 e2e + surfaces
              └─> US2 (T020–T024) — tier-2 + family counting (needs runner/edges)
                    └─> US4 (T025–T026) — negative control (needs full P1 pipeline)
                    └─> US3 (T027–T034) — expressive tier + experiment type
                          └─> US6 (T035–T039) — grading (needs admitted edges)
                                └─> US5 (T040–T044) — reproducibility/parity/curriculum
                                      └─> Polish (T045–T047)
```

Parallel opportunities: within Foundational, T005/T007/T009 (different files); within US1,
T011/T013 then T017/T018; within US3, T027/T029; within US6, T035/T037.

## Implementation Strategy

- **MVP = Phases 1–3** (T001–T019): structurally-segregated tier-1 discovery, end-to-end,
  on all three surfaces — synthetic-testable without any real data.
- **The honesty core = Phases 4–5** (US2 + US4): after these, the loop provably cannot lie.
- **Power = Phases 6–7**: principle seeding, detectors under reserve, forward grading.
- **Ship = Phases 8–9**: parity, curriculum, prod validation run.
- Real-data runs happen only at T047 (and thereafter); everything else is synthetic — the
  dev machine is sufficient for T001–T046.

## Success Criteria Mapping

SC-101/102 → T025–T026 · SC-103 (family accounting) → T020–T022 · SC-104 (segregation) →
T011–T012 · SC-105 (reproducibility) → T040 · SC-106 (degenerate/unstable/entangled never
survive) → T029–T030 · SC-107 (parity + docs-drift) → T041, T019/T024/T034/T039 · SC-108
(tier accounting) → T021, T027–T028 · SC-109 (trust grades) → T035–T036 · SC-110
(diagnostics tagging) → T037–T038
