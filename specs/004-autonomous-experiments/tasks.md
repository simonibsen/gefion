# Tasks: Autonomous AI Experimentation Framework

**Input**: Design documents from `/specs/004-autonomous-experiments/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: TDD is required per constitution. Tests are written FIRST for each module.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies, schema, project structure

- [ ] T001 Add `psutil` and `scipy` to pyproject.toml dependencies (scipy.stats covers BH-FDR and paired tests)
- [ ] T002 Create directory structure: `data/principles/` with empty YAML files for 5 domain areas
- [ ] T003 Create SQL migration `sql/migrations/YYYYMMDD_experiment_cycles.sql` for experiment_cycles table and experiment table extensions (cycle_id, principle_id, null_hypothesis, holdout_p_value, fdr_survived, risk_level, resource_usage, promoted_at, demoted_at, probation_until) — propose DDL for owner approval
- [ ] T004 Add `is_experimental`, `source_experiment_id`, `promoted_at` columns to feature_definitions in `sql/schema.sql` and migration — propose DDL for owner approval
- [ ] T005 Run `gefion db-init` to apply schema changes after approval

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core modules that ALL experiment types depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [ ] T006 [P] Write tests for holdout window management in `tests/test_experiments_holdout.py` — test window computation, structural exclusion, rolling behavior, edge cases (insufficient data)
- [ ] T007 [P] Write tests for FDR control in `tests/test_experiments_fdr.py` — test BH procedure, edge cases (no experiments, all pass, all fail), configurable rate
- [ ] T008 [P] Write tests for safety checks in `tests/test_experiments_safety.py` — test disk space, memory, DB health checks, graceful pause
- [ ] T009 [P] Write tests for experiment config serialization in `tests/test_experiments_config.py` — test to_dict/from_dict round-trip, reproducibility, reusability with different date ranges
- [ ] T010 Implement `src/gefion/experiments/holdout.py` — HoldoutManager class: compute window from max date, enforce exclusion via date filtering, roll forward, validate minimum window size
- [ ] T011 Implement `src/gefion/experiments/statistical.py` — apply_fdr() using BH procedure, compute_holdout_pvalue() via paired test across stocks, configurable FDR rate
- [ ] T012 Implement `src/gefion/experiments/safety.py` — pre-flight checks (disk, memory, DB), periodic checks during execution, graceful pause with partial result preservation
- [ ] T013 Extend ExperimentConfig dataclass in `src/gefion/experiments/core.py` — add holdout_config, data_split, principle_id, null_hypothesis, cv_config, resource_limits; implement to_dict/from_dict
- [ ] T014 Extend ExperimentRunner in `src/gefion/experiments/core.py` — add cycle support (create cycle, associate experiments, evaluate cycle with FDR), real-time status tracking, resource_usage recording
- [ ] T015 Run all foundational tests — verify T006-T009 pass

**Checkpoint**: Holdout, FDR, safety, and config infrastructure ready — experiment types can now be built

---

## Phase 3: User Story 1 — Data Discovery (Priority: P1) 🎯 MVP

**Goal**: Agent can inventory data sources, cross-reference with principles, and generate experiment hypotheses

**Independent Test**: Run `gefion experiment discover` against a database with OHLCV + fundamentals data and verify structured inventory with gap analysis

### Tests for US1

- [ ] T016 [P] [US1] Write tests for discovery module in `tests/test_experiments_discovery.py` — test data inventory from DB metadata, feature inventory, gap analysis against principles, hypothesis generation, handling of empty/missing tables

### Implementation for US1

- [ ] T017 [US1] Implement `src/gefion/experiments/discovery.py` — discover_data_sources() queries information_schema + hypertable chunk stats; discover_features() queries feature_definitions + feature_functions; discover_gaps() cross-references with principles catalog; generate_hypotheses() produces actionable experiment proposals
- [ ] T018 [US1] Add `experiment discover` CLI command in `src/gefion/cli.py` — outputs structured inventory with --json support, shows gaps and hypotheses
- [ ] T019 [US1] Add `experiment_discover` MCP tool in `mcp-server/server.py` — wraps CLI command
- [ ] T020 [US1] Add OTEL instrumentation to discovery module with `create_span("experiments.discovery")`
- [ ] T021 [US1] Run US1 tests — verify discovery works end-to-end

**Checkpoint**: Discovery module functional — agent can inventory data and identify opportunities

---

## Phase 4: User Story 3 — Principles Catalog (Priority: P1)

**Goal**: Curated principles from 11 quantitative finance works available for agent consultation

**Independent Test**: Query catalog for "feature_engineering" principles and verify actionable results with testable predictions

### Tests for US3

- [ ] T022 [P] [US3] Write tests for principles module in `tests/test_experiments_principles.py` — test YAML loading, query by experiment_type, query by domain, query by status, empirical status update, validation of principle schema

### Implementation for US3

- [ ] T023 [P] [US3] Extract principles from Campbell/Lo/MacKinlay + Hamilton into `data/principles/statistical.yaml` — variance ratio tests, stationarity testing, time series decomposition, cointegration, event study methodology
- [ ] T024 [P] [US3] Extract principles from López de Prado + Jansen into `data/principles/ml_finance.yaml` — fractional differentiation, purged CV, meta-labeling, triple-barrier, feature importance (MDA/MDI), sequential bootstrapping
- [ ] T025 [P] [US3] Extract principles from Ang + Bali/Engle/Murray into `data/principles/factor.yaml` — factor premia time-variation, factor crowding, Fama-MacBeth regressions, multiple testing corrections, value/momentum/quality factors
- [ ] T026 [P] [US3] Extract principles from Meucci + Grinold/Kahn into `data/principles/risk_portfolio.yaml` — fundamental law (IR=IC×√BR), effective number of bets, entropy pooling, Kelly criterion, information ratio decomposition
- [ ] T027 [P] [US3] Extract principles from Harris + Taleb into `data/principles/microstructure.yaml` — bid-ask spread dynamics, implementation shortfall, fat tail awareness, antifragility testing, market impact modeling
- [ ] T028 [US3] Implement `src/gefion/experiments/principles.py` — load_principles(domain), query_principles(experiment_type, status), update_empirical_status(principle_id, experiment_id, outcome), validate_principle_schema()
- [ ] T029 [US3] Add `principles list`, `principles show`, `principles suggest` CLI commands in `src/gefion/cli.py`
- [ ] T030 [US3] Add `principles_list`, `principles_suggest` MCP tools in `mcp-server/server.py`
- [ ] T031 [US3] Run US3 tests — verify catalog loads, queries return relevant results

**Checkpoint**: Principles catalog populated and queryable — agent has domain knowledge

---

## Phase 5: User Story 2 — Agent Proposes and Runs Experiments (Priority: P1)

**Goal**: Agent can propose experiments with full ML pipeline access, execute them within sandbox, evaluate on holdout

**Independent Test**: Agent proposes a feature experiment from a principle, executes it end-to-end (feature → train → eval on holdout), returns results with principle reference

### Tests for US2

- [ ] T032 [P] [US2] Write tests for feature engineering experiment type in `tests/test_experiments_types.py` — test experiment proposal, feature creation (is_experimental=true), dataset rebuild, model retrain, holdout evaluation, config serialization

### Implementation for US2

- [ ] T033 [US2] Implement `src/gefion/experiments/types/feature_engineering.py` — FeatureEngineeringExperiment: creates experimental feature definition, computes feature, rebuilds dataset (excluding holdout), retrains model, evaluates on holdout, returns p-value
- [ ] T034 [US2] Extend `experiment propose` CLI command to support `--type feature_engineering --principle <id> --null-hypothesis <text> --cycle <id>` in `src/gefion/cli.py`
- [ ] T035 [US2] Add `experiment cycle start`, `experiment cycle status`, `experiment cycle evaluate`, `experiment cycle list` CLI commands in `src/gefion/cli.py`
- [ ] T036 [US2] Add `experiment_cycle_start`, `experiment_cycle_status`, `experiment_cycle_evaluate` MCP tools in `mcp-server/server.py`
- [ ] T037 [US2] Add `experiment show-config` and `experiment rerun` CLI commands in `src/gefion/cli.py`
- [ ] T038 [US2] Add OTEL instrumentation for experiment execution with `create_span("experiments.run")` and child spans per trial
- [ ] T039 [US2] Run US2 tests — verify end-to-end experiment flow

**Checkpoint**: Core experiment loop works — agent can propose, execute, and evaluate experiments with statistical rigor

---

## Phase 6: User Story 4 — Statistical Guardrails (Priority: P1)

**Goal**: Holdout enforcement and FDR cycle evaluation integrated into experiment lifecycle

**Independent Test**: Run a cycle of experiments, apply FDR, verify correct promotion/rejection

### Implementation for US4

- [ ] T040 [US4] Integrate HoldoutManager into ExperimentRunner — dataset-build calls receive max_date from holdout; holdout evaluation as separate step in `src/gefion/experiments/core.py`
- [ ] T041 [US4] Integrate FDR into cycle evaluation — `cycle evaluate` collects all p-values, applies BH, updates fdr_survived and promotion status in `src/gefion/experiments/core.py`
- [ ] T042 [US4] Integrate safety checks into experiment execution — pre-flight before cycle start, periodic checks every N trials in `src/gefion/experiments/core.py`
- [ ] T043 [US4] Add operational guardrails: compute budget enforcement, diversity check (min 2 principles per cycle), duplicate experiment detection in `src/gefion/experiments/core.py`
- [ ] T044 [US4] Add probation window for promoted artifacts — auto-demote if model performance degrades within window in `src/gefion/experiments/core.py`
- [ ] T045 [US4] Write integration test for full cycle: propose 5 experiments → execute → holdout eval → FDR → verify correct promotions in `tests/test_experiments_types.py`
- [ ] T045b [US4] Implement duplicate experiment detection in `src/gefion/experiments/core.py` — hash experiment config (type + search_space + principle_id), reject proposals matching recent experiments in same cycle (FR-026)
- [ ] T045c [US4] Regression test: verify existing strategy_params experiments still work after core.py extensions in `tests/test_experiments.py`

**Checkpoint**: Full statistical guardrails operational — autonomous experiments are trustworthy

---

## Phase 7: User Story 5 — Hyperparameter Tuning + Model Comparison (Priority: P2)

**Goal**: New experiment types for ML model optimization

**Independent Test**: Run hyperparameter experiment with purged CV, verify different results from standard CV

### Tests for US5

- [ ] T046 [P] [US5] Write tests for purged CV in `tests/test_experiments_types.py` — test PurgedKFold splitter, embargo periods, comparison against standard KFold
- [ ] T047 [P] [US5] Write tests for model comparison in `tests/test_experiments_types.py` — test identical splits, metric comparability across model types

### Implementation for US5

- [ ] T048 [US5] Implement PurgedKFold CV splitter in `src/gefion/experiments/types/hyperparameter.py` — sklearn-compatible, configurable n_splits, embargo_pct, prediction_horizon
- [ ] T049 [US5] Implement HyperparameterExperiment in `src/gefion/experiments/types/hyperparameter.py` — uses PurgedKFold, search strategies (grid/random/bayesian), holdout evaluation
- [ ] T050 [US5] Implement ModelComparisonExperiment in `src/gefion/experiments/types/model_comparison.py` — trains multiple model types on identical purged CV splits, compares holdout metrics
- [ ] T051 [US5] Run US5 tests — verify purged CV and model comparison work correctly

**Checkpoint**: ML model experiments operational

---

## Phase 8: User Story 6 — Feature Selection (Priority: P2)

**Goal**: Agent can propose and run feature selection experiments with FDR control

**Independent Test**: Run feature selection experiment, verify optimal subset identified with FDR correction

### Tests for US6

- [ ] T052 [P] [US6] Write tests for feature selection in `tests/test_experiments_types.py` — test subset evaluation, FDR across subsets, promotion of selected features

### Implementation for US6

- [ ] T053 [US6] Implement FeatureSelectionExperiment in `src/gefion/experiments/types/feature_selection.py` — evaluates feature subsets (forward/backward/importance-based), FDR across subset tests, holdout evaluation
- [ ] T054 [US6] Run US6 tests

**Checkpoint**: Feature selection experiments operational

---

## Phase 9: User Story 7 — Label Engineering (Priority: P2)

**Goal**: Agent can propose experiments that change prediction targets, evaluated via backtest

**Independent Test**: Run triple-barrier labeling experiment, verify backtest evaluation (not prediction metrics)

### Tests for US7

- [ ] T055 [P] [US7] Write tests for label engineering in `tests/test_experiments_types.py` — test triple-barrier label generation, meta-labeling setup, backtest-based evaluation

### Implementation for US7

- [ ] T056 [US7] Implement LabelEngineeringExperiment in `src/gefion/experiments/types/label_engineering.py` — generates alternative labels (triple-barrier, meta-label), trains model, evaluates via backtest on holdout period, compares backtest metrics (Sharpe, drawdown) against current pipeline
- [ ] T057 [US7] Run US7 tests

**Checkpoint**: Label engineering experiments operational — highest-leverage experiment type available

---

## Phase 10: User Story 8 — D3 Experiment Visualization (Priority: P2)

**Goal**: Experiment results visualized with interactive D3 charts

**Independent Test**: Generate FDR cycle summary chart for a completed cycle, verify it renders with threshold line and promoted/rejected markers

### Tests for US8

- [ ] T058 [P] [US8] Write tests for experiment chart templates in `tests/test_d3_experiments.py` — test that each template renders valid HTML with embedded data, SVG elements present

### Implementation for US8

- [ ] T059 [P] [US8] Create D3 template `src/gefion/charts/d3/templates/experiment_trials.html` — scatter chart: trial number vs score, color by promoted/rejected, tooltip with parameters, best trial highlighted
- [ ] T060 [P] [US8] Create D3 template `src/gefion/charts/d3/templates/experiment_fdr.html` — cycle summary: experiments on X, p-value (log scale) on Y, horizontal FDR threshold line, green=promoted red=rejected
- [ ] T061 [P] [US8] Create D3 template `src/gefion/charts/d3/templates/experiment_heatmap.html` — parameter sensitivity heatmap for 2-parameter experiments
- [ ] T062 [P] [US8] Create D3 template `src/gefion/charts/d3/templates/experiment_features.html` — paired before/after bar chart of feature importance rankings
- [ ] T063 [US8] Add renderer functions in `src/gefion/charts/d3/renderers.py` — create_experiment_trials(), create_experiment_fdr(), create_experiment_heatmap(), create_experiment_features()
- [ ] T064 [US8] Integrate charts into Experiments UI view in `src/gefion/ui/views/experiments.py` — add Charts tab with chart selection per experiment type
- [ ] T065 [US8] Add chart CLI commands: `gefion chart experiment-trials`, `gefion chart experiment-fdr` in `src/gefion/cli.py`
- [ ] T066 [US8] Run US8 tests

**Checkpoint**: Experiment results are visually explorable

---

## Phase 11: User Story 9 — Pipeline Experiments (Priority: P3)

**Goal**: Chain multiple experiment stages into end-to-end pipelines evaluated on holdout

**Independent Test**: Create 2-stage pipeline (feature → model), verify stage 2 uses stage 1 output, end-to-end holdout evaluation

### Tests for US9

- [ ] T067 [P] [US9] Write tests for pipeline experiments in `tests/test_experiments_pipeline.py` — test stage chaining, artifact flow, failure handling, end-to-end holdout eval

### Implementation for US9

- [ ] T068 [US9] Implement PipelineExperiment in `src/gefion/experiments/types/pipeline.py` — orchestrates chained stages, passes artifacts between stages, evaluates end-to-end on holdout (not per-stage), halts on stage failure with partial results
- [ ] T069 [US9] Run US9 tests

**Checkpoint**: Pipeline experiments operational — full discover→feature→model→strategy chains possible

---

## Phase 12: User Story 10 — Feedback Loop (Priority: P3)

**Goal**: Experiment results update principle empirical status, creating a self-improving knowledge base

**Independent Test**: Run experiment derived from a principle, verify principle status updates to confirmed/contradicted

### Implementation for US10

- [ ] T070 [US10] Implement feedback integration in `src/gefion/experiments/principles.py` — after cycle evaluation, update_empirical_status() for each principle referenced by experiments in the cycle, tracking experiment_id and outcome
- [ ] T071 [US10] Add principle status display to experiment results output (CLI --json and UI)
- [ ] T072 [US10] Run feedback tests in `tests/test_experiments_principles.py`

**Checkpoint**: Feedback loop closed — principles improve over time based on empirical evidence

---

## Phase 13: Polish & Cross-Cutting Concerns

**Purpose**: Integration, documentation, observability, skill

- [ ] T073 [P] Update Experiments UI in `src/gefion/ui/views/experiments.py` — add Discovery tab, Cycles tab, principle references in experiment details, resource usage display, real-time status
- [ ] T073b [P] Update Ask Gefion page context in `src/gefion/ui/components/chat.py` — include experiment capabilities (suggest experiments, show cycle status, render experiment charts inline)
- [ ] T074 [P] Create `gefion-experiment` skill in `.claude/commands/gefion-experiment.md` — orchestrates full autonomous cycle: discover → consult principles → propose → execute → evaluate → promote → report
- [ ] T075 [P] Update README.md with experiment framework architecture diagram (mermaid)
- [ ] T076 [P] Update `.specify/memory/backlog.md` — mark experiment framework items complete
- [ ] T077 [P] Update docs/USER_GUIDE.md with new CLI commands (discover, cycle, principles)
- [ ] T078 Run full test suite — verify all experiments tests pass alongside existing tests
- [ ] T079 Run `/gefion-perf` — verify experiment execution traces appear in Tempo, no slow spans
- [ ] T080 Run quickstart.md validation — execute the full quickstart flow end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (schema must exist)
- **US1 Discovery (Phase 3)**: Depends on Phase 2 (needs principles module structure for gap analysis)
- **US3 Principles (Phase 4)**: Depends on Phase 2 only — can run in parallel with US1
- **US2 Core Experiments (Phase 5)**: Depends on Phases 2, 3, 4 (needs discovery + principles + holdout/FDR)
- **US4 Statistical Guardrails (Phase 6)**: Depends on Phase 5 (integrates into experiment runner)
- **US5-US7 New Types (Phases 7-9)**: Depend on Phase 6 — can run in parallel with each other
- **US8 Visualization (Phase 10)**: Depends on at least one experiment type being complete (Phase 5+)
- **US9 Pipeline (Phase 11)**: Depends on Phases 7-9 (needs multiple experiment types to chain)
- **US10 Feedback (Phase 12)**: Depends on Phase 5 + Phase 4
- **Polish (Phase 13)**: Depends on all desired phases being complete

### User Story Dependencies

- **US1 (Discovery)**: Independent after foundational — no dependencies on other stories
- **US3 (Principles)**: Independent after foundational — can parallel with US1
- **US2 (Core Experiments)**: Depends on US1 + US3
- **US4 (Guardrails)**: Depends on US2
- **US5 (Hyperparameter)**: Depends on US4 — parallel with US6, US7
- **US6 (Feature Selection)**: Depends on US4 — parallel with US5, US7
- **US7 (Label Engineering)**: Depends on US4 — parallel with US5, US6
- **US8 (Visualization)**: Depends on US2 — parallel with US5-US7
- **US9 (Pipeline)**: Depends on US5, US6, US7
- **US10 (Feedback)**: Depends on US2 + US3

### Parallel Opportunities

- **Phase 2**: T006, T007, T008, T009 (all test files) in parallel
- **Phase 4**: T023, T024, T025, T026, T027 (all principle extractions) in parallel
- **Phases 7-9**: US5, US6, US7 can all run in parallel after Phase 6
- **Phase 10**: T059, T060, T061, T062 (all D3 templates) in parallel
- **Phase 13**: T073, T074, T075, T076, T077 all in parallel

---

## Implementation Strategy

### MVP First (Phases 1-6)

1. Complete Phase 1: Setup (schema, dependencies)
2. Complete Phase 2: Foundational (holdout, FDR, safety, config)
3. Complete Phase 3: US1 Discovery
4. Complete Phase 4: US3 Principles (parallel with Phase 3)
5. Complete Phase 5: US2 Core Experiments
6. Complete Phase 6: US4 Statistical Guardrails
7. **STOP and VALIDATE**: Run a full experiment cycle — discover → propose → execute → evaluate → promote

### Incremental Delivery

8. Add US5-US7 (new experiment types) → Test each independently
9. Add US8 (D3 charts) → Verify charts render for completed experiments
10. Add US9 (Pipeline) → Test 2-stage chain end-to-end
11. Add US10 (Feedback) → Verify principles update after experiments
12. Polish → Docs, skill, observability

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- TDD is mandatory per constitution — tests written and failing before implementation
- Schema changes (T003, T004) require owner approval before execution
- Principle extractions (T023-T027) use Claude's training data knowledge — no external documents needed
- Each checkpoint should include running the existing test suite to verify no regressions
