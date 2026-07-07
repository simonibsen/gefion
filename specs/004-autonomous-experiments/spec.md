# Feature Specification: Autonomous AI Experimentation Framework

**Feature Branch**: `004-autonomous-experiments`
**Created**: 2026-03-29
**Status**: Draft
**Input**: Autonomous AI experimentation framework with principles catalog from quantitative finance literature, enabling an AI agent to autonomously propose, design, execute, and evaluate experiments across Gefion's full pipeline.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Data Discovery and Experiment Hypothesis Generation (Priority: P1)

Before proposing experiments, the agent performs data discovery: inventorying available data sources (OHLCV, fundamentals, computed features, cross-sectional features), their coverage, freshness, and schema. It cross-references the inventory against the principles catalog to identify gaps — data that exists but has no features derived from it, or principles that suggest useful features from available data. This discovery step produces experiment hypotheses grounded in both domain knowledge and actual data availability.

**Why this priority**: Discovery is Step 0. Without knowing what data exists, the agent proposes experiments that can't be executed or misses opportunities sitting in underutilized data. This must work before any experiment can be designed.

**Independent Test**: Can be tested by running discovery against the current database and verifying it produces a structured inventory with gap analysis and at least one actionable hypothesis.

**Acceptance Scenarios**:

1. **Given** a database with OHLCV data and fundamentals for 5000 stocks, **When** the agent runs discovery, **Then** it produces an inventory listing: tables, columns, date ranges, coverage percentages, and staleness per data source
2. **Given** fundamentals data with `operating_margin` and `book_value` columns, **When** the agent consults factor investing principles, **Then** it identifies that book-to-market ratio can be computed from available data and proposes it as a feature experiment
3. **Given** a feature definition that references a data source with no data (e.g., sentiment scores not yet ingested), **When** the agent encounters this during discovery, **Then** it flags the dependency gap and excludes experiments that require that data
4. **Given** computed features with 90% coverage but fundamentals with only 40% coverage, **When** the agent plans experiments, **Then** it accounts for coverage differences when estimating statistical power

---

### User Story 2 — AI Agent Proposes and Runs Experiments with Full Pipeline Access (Priority: P1)

The AI agent examines the current model's performance, consults the principles catalog, and proposes experiments that can touch any part of the pipeline: feature engineering, model training, evaluation, or strategy optimization. The agent has full access to the ML pipeline (dataset-build, train, predict, eval, calibrate) within a sandboxed environment. Experiments execute autonomously — low-risk experiments auto-approve and run without human intervention.

**Why this priority**: This is the core value proposition — the agent autonomously improving the pipeline using domain knowledge. Full pipeline access is required because features alone can't answer "does this improve predictions?" — only training and evaluating a model can.

**Independent Test**: Can be tested by having the agent propose one experiment, execute it end-to-end (feature → train → eval), and compare results against the current baseline.

**Acceptance Scenarios**:

1. **Given** a trained model with feature importance scores, **When** the agent is asked to suggest experiments, **Then** it proposes at least one experiment grounded in a specific principle with a testable hypothesis
2. **Given** an approved feature engineering experiment, **When** it executes, **Then** it computes the new feature, rebuilds the dataset, retrains the model, and evaluates on the holdout — all within the experiment sandbox
3. **Given** a completed experiment, **When** the user views results, **Then** they see which principle motivated it, the hypothesis, baseline vs experimental metrics, holdout evaluation, and statistical significance

---

### User Story 3 — Principles Catalog as Agent Context (Priority: P1)

A curated catalog of principles extracted from ~10 quantitative finance works is available for the AI agent to consult when planning experiments. Principles are structured with claims, mechanisms, implications for experiment design, and testable predictions. The agent retrieves relevant principles based on the experiment type, current system state, and discovery results rather than loading the entire catalog.

**Why this priority**: Without domain knowledge, the agent is just doing random search. The principles catalog is what makes autonomy useful. This is co-P1 with Stories 1 and 2 because they depend on each other.

**Independent Test**: Can be tested by querying the catalog for principles relevant to a given experiment type (e.g., "feature_engineering") and verifying that returned principles contain actionable experiment designs.

**Acceptance Scenarios**:

1. **Given** a principles catalog with extractions from at least 3 works, **When** the agent queries for principles relevant to "feature_engineering", **Then** it receives principles that include specific testable predictions and experiment design implications
2. **Given** a principle with a testable prediction, **When** the agent designs an experiment from it, **Then** the experiment configuration explicitly references the source principle and its predicted outcome
3. **Given** experiment results that contradict a principle's prediction, **When** the experiment completes, **Then** the principle's empirical status is updated to reflect the contradicting evidence

---

### User Story 4 — Statistical Guardrails: Holdout and FDR Control (Priority: P1)

All experiments are evaluated against a mandatory out-of-sample holdout period that is structurally excluded from training and validation data. The agent cannot access holdout data during any phase of experiment design or execution. When multiple experiments run in a cycle, their results are evaluated together using False Discovery Rate control (Benjamini-Hochberg) to prevent accumulation of false positives. Promotion is automatic when experiments survive FDR correction — no manual approval needed.

**Why this priority**: This is the guardrail that makes autonomy trustworthy. Without it, the agent will find patterns that look good but aren't real. Co-P1 because every experiment depends on honest evaluation.

**Independent Test**: Can be tested by running a set of experiments where some have genuine signal and some are noise, then verifying that FDR correctly separates them and only promotes the real ones.

**Acceptance Scenarios**:

1. **Given** a 1-year dataset, **When** an experiment is configured, **Then** the most recent N weeks are reserved as holdout and structurally excluded from all training, validation, and feature engineering data
2. **Given** an experiment with a new feature, **When** it is evaluated on holdout data, **Then** it produces a p-value measuring statistical significance of the improvement over baseline
3. **Given** an experiment cycle with 15 experiments, **When** FDR control is applied at 10%, **Then** experiments that survive correction are auto-promoted and experiments that don't are logged as non-significant
4. **Given** an agent that runs 20 experiments where none have real signal, **When** FDR is applied, **Then** at most 2 (10% FDR) are falsely promoted — and likely fewer
5. **Given** a completed cycle, **When** results are reviewed, **Then** all experiments (successes and failures) are visible, not just the promoted ones

---

### User Story 5 — New Experiment Types: Hyperparameter Tuning and Model Comparison (Priority: P2)

Beyond the existing strategy_params type, the framework supports hyperparameter tuning (with purged cross-validation) and model comparison experiments. The agent can propose tuning experiments when model performance degrades, or comparison experiments to evaluate whether a different model architecture would perform better.

**Why this priority**: Extends the experiment framework to cover the full ML pipeline. Strategy_params already works; these types complete the picture for model improvement.

**Independent Test**: Can be tested by proposing a hyperparameter tuning experiment for an existing model, running it with purged CV, and verifying the best parameters differ from defaults.

**Acceptance Scenarios**:

1. **Given** a trained quantile model, **When** a hyperparameter experiment is proposed, **Then** it uses purged cross-validation (not standard k-fold) to prevent information leakage
2. **Given** two model types (quantile regressor, ensemble), **When** a model comparison experiment runs, **Then** both are trained on the same dataset with the same CV strategy, and metrics are directly comparable
3. **Given** a completed hyperparameter experiment with improved results, **When** it survives FDR evaluation on the holdout, **Then** it is auto-promoted to production

---

### User Story 6 — Feature Selection Experiments (Priority: P2)

The agent can propose feature selection experiments to find optimal feature subsets. This includes testing whether removing low-importance features improves out-of-sample performance, and whether adding cross-sectional features (sector-relative rankings) improves predictions — guided by principles from the factor investing literature.

**Why this priority**: Feature selection directly impacts model quality and training time. Co-P2 with hyperparameter tuning as they address different parts of the same pipeline.

**Independent Test**: Can be tested by running a feature selection experiment that compares the current feature set against a reduced set, measuring out-of-sample performance on the holdout.

**Acceptance Scenarios**:

1. **Given** a model with 20+ features, **When** a feature selection experiment runs, **Then** it evaluates multiple subsets and identifies the optimal set by out-of-sample metric
2. **Given** multiple hypothesis tests from feature selection, **When** results are evaluated, **Then** FDR control is applied across the full cycle to prevent selection of spuriously significant features
3. **Given** a feature subset that outperforms the full set and survives FDR, **When** it is auto-promoted, **Then** the feature definitions are updated and downstream models are flagged for retraining

---

### User Story 7 — Label Engineering Experiments (Priority: P2)

The agent can propose experiments that change the prediction target itself, not just the input features. For example: replacing fixed-horizon return labels with triple-barrier labels (stop-loss, take-profit, time expiry), adding meta-labeling (a second model that predicts whether to act on the first model's signal), or adjusting label horizons based on volatility regime. Because label experiments change what is being predicted, they cannot be evaluated by comparing model metrics against the old target — evaluation must go through backtesting to measure actual trading outcomes.

**Why this priority**: Label engineering is potentially the highest-leverage experiment type. Everyone uses similar features (RSI, MACD, momentum from OHLCV), but what you *predict* differentiates. Changing from fixed-horizon returns to path-dependent labels (triple-barrier) or adding bet sizing (meta-labeling) fundamentally changes the pipeline's effectiveness. Co-P2 because it requires the core experiment infrastructure (P1) to be solid first.

**Independent Test**: Can be tested by proposing a triple-barrier labeling experiment, generating new labels, training a model on them, backtesting the signals, and comparing backtest performance against the current pipeline's backtest.

**Acceptance Scenarios**:

1. **Given** the current pipeline using fixed 5-day return labels, **When** a triple-barrier label experiment is proposed, **Then** new labels are generated using configurable stop-loss, take-profit, and time expiry parameters
2. **Given** a model trained on triple-barrier labels, **When** it is evaluated, **Then** evaluation uses backtest metrics (Sharpe ratio, max drawdown, win rate) rather than prediction accuracy — since the prediction target is different from the baseline
3. **Given** a meta-labeling experiment, **When** it executes, **Then** a primary directional model produces signals, a secondary model is trained to predict signal quality (bet/no-bet), and combined performance is backtested
4. **Given** a label experiment that improves backtest Sharpe ratio on holdout data, **When** it survives FDR correction, **Then** the new labeling scheme is auto-promoted and the pipeline switches to the new labels

---

### User Story 8 — Experiment Visualization with D3 Charts (Priority: P2)

Experiment results are visualized using the existing D3 chart framework. Charts include: trial performance comparison (bar/scatter), parameter sensitivity (heatmap), experiment cycle summary (FDR-corrected results), feature importance before/after (bar chart), holdout vs in-sample performance (comparison), and principle-to-experiment lineage (network/tree). Charts render in the Experiments UI and can be generated by Ask Gefion conversationally.

**Why this priority**: Without visualization, experiment results are just numbers in JSON. Charts make it possible to understand what the agent did, why, and whether the results are trustworthy. Reuses the D3 infrastructure already built.

**Independent Test**: Can be tested by generating a chart for a completed experiment and verifying it renders correctly with meaningful data.

**Acceptance Scenarios**:

1. **Given** a completed experiment with 20 trials, **When** the user views results, **Then** a D3 scatter chart shows trial parameters vs performance with the best trial highlighted
2. **Given** a completed experiment cycle, **When** the cycle summary is displayed, **Then** a chart shows all experiments with their p-values, the FDR threshold line, and which experiments were promoted
3. **Given** a hyperparameter experiment, **When** the user views results, **Then** a heatmap shows parameter combinations vs performance, revealing sensitivity and interactions
4. **Given** a feature engineering experiment, **When** results are displayed, **Then** a before/after bar chart shows feature importance rankings with the new feature's position highlighted

---

### User Story 9 — Pipeline Experiments (Priority: P3)

End-to-end pipeline experiments that chain feature engineering → model training → strategy optimization. The agent can propose a composite experiment: "Add fractional differentiation features, retrain the quantile model, and re-optimize the momentum strategy." Each stage depends on the previous stage's output. The entire pipeline is evaluated end-to-end on the holdout.

**Why this priority**: This is the most ambitious experiment type — it orchestrates multiple stages. Depends on Stories 1-6 being solid first.

**Independent Test**: Can be tested by creating a 2-stage chained experiment (feature → model) and verifying that stage 2 uses stage 1's output.

**Acceptance Scenarios**:

1. **Given** a pipeline experiment with 3 stages, **When** stage 1 completes, **Then** stage 2 automatically begins using stage 1's output artifacts
2. **Given** a pipeline experiment where stage 2 fails, **When** the failure is detected, **Then** the pipeline halts, partial results are preserved, and the user is notified
3. **Given** a completed pipeline experiment, **When** it is evaluated, **Then** end-to-end holdout metrics determine promotion — not per-stage metrics (which could compound overfitting)

---

### User Story 10 — Experiment Results Feed Back into Principles (Priority: P3)

When experiments confirm or contradict principles from the catalog, the results update the principle's empirical status. Over time, the catalog becomes a living knowledge base tuned to Gefion's actual data and market conditions rather than just textbook theory.

**Why this priority**: This closes the feedback loop and makes the system self-improving, but requires the core experiment loop (Stories 1-6) to be working first.

**Independent Test**: Can be tested by running an experiment derived from a principle, then verifying the principle's status reflects the result.

**Acceptance Scenarios**:

1. **Given** a principle predicting that fractional differentiation improves feature stationarity, **When** an experiment confirms this with statistically significant results, **Then** the principle's status updates to "confirmed" with a reference to the experiment
2. **Given** a principle that has been contradicted by 2+ experiments, **When** the agent consults the catalog, **Then** it deprioritizes experiments based on that principle
3. **Given** an experiment that reveals a novel finding not covered by existing principles, **When** the user reviews results, **Then** the system suggests adding a new empirical principle to the catalog

---

### Edge Cases

- What happens when the agent proposes an experiment but the required data doesn't exist (e.g., features not yet computed)?
- What happens when two concurrent experiments modify the same feature definitions?
- How does the system handle experiments on an empty database (no price data)?
- What happens when a promoted experiment's improvements degrade over time (regime change)?
- How does the system prevent the agent from running the same experiment repeatedly?
- What happens when the principles catalog contains contradictory principles from different sources?
- What happens when the holdout period is too short for statistical significance (e.g., only a few days of data)?
- How does the system handle an experiment cycle where all experiments fail FDR correction?
- What happens when the agent proposes a feature that duplicates an existing feature under a different name?

## Requirements *(mandatory)*

### Functional Requirements

**Data Discovery**

- **FR-001**: System MUST provide a data discovery step that inventories all available data sources, their schemas, date ranges, coverage percentages, and freshness
- **FR-002**: Discovery MUST identify gaps between available data and principles catalog — data that exists but has no derived features, or principles that suggest features from available data
- **FR-003**: Discovery MUST detect when proposed experiments depend on data that is missing or has insufficient coverage
- **FR-004**: Discovery results MUST be available to the agent as structured context when planning experiments
- **FR-004b**: System MUST maintain a data source registry (YAML) describing available tables, columns, their semantic meaning, and how they can be used (feature input, label input, cross-sectional). New data sources (e.g., sentiment, macro indicators) are registered here so the agent knows what they are and how to use them

**Principles Catalog**

- **FR-005**: System MUST store principles extracted from quantitative finance works in a structured format with: source attribution, claim, mechanism, implication for experiment design, testable prediction, known limitations, relevant experiment types, and empirical status
- **FR-006**: System MUST support querying principles by experiment type, relevant layer (feature engineering, model selection, portfolio construction), and empirical status
- **FR-007**: System MUST track empirical status of each principle: untested, confirmed, partially confirmed, contradicted, with references to supporting/contradicting experiments
- **FR-008**: System MUST include principles from at least 8 of the identified works at launch, covering: statistical foundations, ML for finance, factor models, risk/portfolio, and market microstructure

**Experiment Types**

- **FR-009**: System MUST support feature_engineering experiments that create and evaluate new computed features. The agent MAY create new feature definitions tagged as experimental; promotion is automatic via statistical gates
- **FR-010**: System MUST support feature_selection experiments that evaluate feature subsets
- **FR-011**: System MUST support hyperparameter experiments that use purged cross-validation for time-series data
- **FR-012**: System MUST support model_comparison experiments that evaluate multiple model types on identical data splits
- **FR-013**: System MUST support label_engineering experiments that change the prediction target (e.g., triple-barrier labeling, meta-labeling, regime-adjusted horizons). Label experiments MUST be evaluated via backtest performance, not model prediction metrics, since the prediction target differs from the baseline
- **FR-014**: System MUST support pipeline experiments that chain multiple experiment stages with dependency tracking
- **FR-015**: System MUST continue to support existing strategy_params experiments
- **FR-016**: The agent MUST have access to the full ML pipeline (dataset-build, train, predict, eval, calibrate) within the experiment sandbox

**Statistical Guardrails**

- **FR-017**: System MUST enforce a mandatory out-of-sample holdout period that is structurally excluded from all training, validation, and feature engineering data
- **FR-018**: The holdout window MUST be the most recent data and MUST roll forward as new data arrives
- **FR-019**: The agent MUST NOT be able to access holdout data during any phase of experiment design or execution; holdout is used exactly once at final evaluation
- **FR-020**: System MUST apply Benjamini-Hochberg FDR control across all experiments within a cycle, at a configurable FDR rate (default 10%)
- **FR-021**: All experiments in a cycle MUST be recorded (successes and failures) so FDR correction operates on the complete family of tests
- **FR-022**: Experiments that survive FDR correction on holdout data MUST auto-promote without manual approval
- **FR-023**: Experiments that fail FDR correction MUST be logged with full results but not promoted

**Operational Guardrails**

- **FR-024**: System MUST enforce compute guardrails: maximum trials per experiment, maximum wall time per cycle, and maximum concurrent experiments (default: 3 concurrent)
- **FR-025**: System MUST enforce a diversity requirement: the agent must draw from at least 2 different principles per experiment cycle to prevent fixation
- **FR-026**: System MUST detect when the agent is proposing duplicate or near-duplicate experiments and prevent redundant execution
- **FR-027**: Promoted artifacts MUST have a configurable probation period (default: 7 days) during which they are auto-demoted if model performance degrades
- **FR-028**: System MUST perform safety checks before and during experiment execution: disk space availability, memory usage, and database connection health. Experiments MUST pause gracefully if any resource threshold is breached
- **FR-029**: System MUST provide real-time status for running experiments: current trial, elapsed time, resource usage, and partial results. Status MUST be queryable via CLI, MCP, and UI

**Experiment Configuration**

- **FR-030**: Every experiment MUST have a serializable configuration object that fully describes it: experiment type, parameters, search space, data split definitions, holdout window, null hypothesis, and guardrail settings. Principle reference and discovery context are OPTIONAL (experiments may originate from principles, data discovery, performance observations, or user requests)
- **FR-031**: Experiment configurations MUST be reproducible — re-running an experiment with the same configuration on the same data MUST produce identical results (given deterministic settings)
- **FR-032**: Experiment configurations MUST be reusable — a successful experiment's configuration can be re-applied to a different time period or stock universe without modification beyond the data parameters

**Experiment Lifecycle**

- **FR-033**: System MUST track artifacts as experimental or production, preventing accidental use of unvalidated results
- **FR-034**: System MUST prevent deletion of artifacts that are referenced by other experiments or production models
- **FR-035**: Completed experiments MUST store: hypothesis, baseline metrics, experimental metrics, holdout p-value, FDR-corrected status, and promotion decision. Principle reference, discovery context, and experiment origin (principle/discovery/performance/user) are stored when available
- **FR-036**: The experiment lifecycle MUST follow: discover → hypothesize → design → execute → evaluate (holdout + FDR) → promote/reject

**Feedback Loop**

- **FR-037**: System MUST update principle empirical status when experiments produce results relevant to a principle's testable prediction
- **FR-038**: System MUST track which experiments were derived from which principles for traceability

**Visualization**

- **FR-039**: System MUST provide D3 charts for experiment results: trial performance scatter, parameter sensitivity heatmap, FDR cycle summary, feature importance before/after, and holdout vs in-sample comparison
- **FR-040**: Experiment charts MUST be available in the Experiments UI and renderable by Ask Gefion conversationally
- **FR-041**: The FDR cycle summary chart MUST show all experiments with their p-values, the FDR threshold line, and which experiments were promoted vs rejected

**Integration**

- **FR-042**: All experiment operations MUST be accessible via CLI, MCP tools, and UI
- **FR-043**: Ask Gefion MUST be able to suggest and run experiments conversationally based on current system state and discovery results
- **FR-044**: Experiment execution MUST produce observability traces for performance monitoring

### Key Entities

- **Data Inventory**: A structured snapshot of available data sources, their schemas, coverage, freshness, and derived features. Produced by the discovery step and consumed by the agent when planning experiments.
- **Principle**: A structured claim extracted from a quantitative finance work, with source attribution, mechanism, testable prediction, and empirical status. Related to zero or more experiments.
- **Experiment Cycle**: A batch of experiments proposed, executed, and evaluated together. FDR control is applied per cycle. Contains a holdout window definition.
- **Experiment**: An investigation with a hypothesis, configuration, trials, and results. References a motivating principle and discovery context. Classified by type. May be part of a pipeline chain.
- **Trial**: A single execution within an experiment with specific parameters and measured metrics.
- **Experimental Artifact**: A feature definition, model, or strategy configuration produced by an experiment. Tagged as experimental until auto-promoted via statistical gates or auto-demoted during probation.
- **Work/Source**: A quantitative finance book or paper from which principles are extracted. Has metadata: author, title, year, domain area.

## Clarifications

### Session 2026-03-29

- Q: What storage format should the principles catalog use? → A: YAML files split by 5 domain areas (statistical, ml_finance, factor, risk_portfolio, microstructure)
- Q: Can the agent create new feature definitions autonomously? → A: Yes, tagged as experimental; auto-promoted via statistical gates (holdout + FDR), no manual approval required
- Q: What are the primary guardrails for autonomous experiments? → A: Mandatory out-of-sample holdout (structurally enforced), FDR control across experiment cycles, compute budgets, diversity requirements, probation period for promoted artifacts
- Q: Should the agent have full ML pipeline access? → A: Yes, the agent can orchestrate dataset-build, train, predict, eval, calibrate within the experiment sandbox
- Q: Is data discovery a first-class step? → A: Yes, discovery is Step 0 in the experiment lifecycle: discover → hypothesize → design → execute → evaluate → promote/reject
- Q: How should experiment results be visualized? → A: D3 charts using the existing chart framework, including trial scatter, parameter heatmaps, FDR cycle summaries, and feature importance comparisons

## Assumptions

- The AI agent operates through existing MCP tools and Ask Gefion; no separate agent process is needed
- The existing experiment database schema (experiments, experiment_trials tables) will be extended rather than replaced
- The principles catalog is stored as 5 YAML files split by domain area (statistical, ml_finance, factor, risk_portfolio, microstructure) in the repository, not in the database, since principles are curated knowledge rather than runtime data
- Purged cross-validation will be implemented as part of the hyperparameter experiment type, using the existing ML pipeline's train/eval infrastructure
- Experiment compute budgets are enforced by the experiment runner, not by external resource managers
- The ~10 works listed in the description are available in Claude's training data for principle extraction; no OCR or PDF parsing is needed
- The holdout window is configurable but defaults to the most recent 6 weeks of data
- FDR rate is configurable but defaults to 10%
- D3 experiment charts reuse the existing chart framework (base.py, templates, theme) established in the D3 migration

## Automation

- **Proposed skill**: `gefion-experiment` — AI-driven experiment loop: discover data → consult principles → propose experiments → execute → evaluate on holdout → apply FDR → promote survivors → report
- **Rationale**: The autonomous experiment workflow is a natural fit for a skill that can be invoked conversationally ("run an experiment cycle") or on a schedule. It orchestrates multiple existing tools (feature compute, model train, backtest) into a coherent research loop with statistical rigor.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The AI agent can propose at least 5 distinct experiment types grounded in specific principles from the catalog, without human prompting beyond "suggest experiments"
- **SC-002**: Data discovery correctly identifies available data sources, coverage gaps, and at least one actionable experiment hypothesis from underutilized data
- **SC-003**: Experiments using purged cross-validation produce different (typically lower) performance estimates than standard cross-validation on the same data, validating the methodology
- **SC-004**: FDR control at 10% correctly limits false discovery rate: in cycles with no genuine signal, no more than 10% of experiments are falsely promoted
- **SC-005**: Holdout evaluation produces meaningfully different results from in-sample evaluation, confirming that the holdout is genuinely out-of-sample
- **SC-006**: At least 3 principles from the catalog are empirically tested within the first experiment cycle, with their status updated to confirmed or contradicted
- **SC-007**: Pipeline experiments successfully chain 2+ stages with artifacts flowing between stages and end-to-end holdout evaluation
- **SC-008**: The principles catalog covers all 5 domain areas (statistical foundations, ML for finance, factor models, risk/portfolio, microstructure) with at least 5 actionable principles per area
- **SC-009**: Users can review any experiment's full lineage: discovery context, motivating principle, hypothesis, holdout results, FDR status, and promotion decision
- **SC-010**: Experiment results render as D3 charts in the UI, including at minimum: trial performance scatter, FDR cycle summary with threshold line, and feature importance comparison

## Cross-references (added later)

- **`regime_discovery` experiment type (spec 006, 2026-07)** — agentic regime discovery
  runs inside the cycle framework as a first-class experiment type, but with a
  deliberately STRICTER gate than standard experiments: risk class **high** and never
  auto-approved by `cycle_runner` (a human owns discovery's gate); the cycle's budget
  maps onto the run's per-cycle *candidate* budget; and the experiment earns **no
  cycle-level holdout p-value** (it stays NULL, failing closed at the cycle level) —
  its honest verdicts live in the discovery run's own ledgers, behind nested
  segregation, an inner evidence screen, and one flat FDR family at 0.01 that counts
  every candidate including the losers. See
  [specs/006-agentic-regime-discovery/](../006-agentic-regime-discovery/) and
  docs/REGIMES.md § Agentic discovery.
