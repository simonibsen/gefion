# Feature Specification: Model-Prediction Signals for Discovery (the ML Meta-Question)

**Feature Branch**: `012-model-signals`
**Created**: 2026-07-12
**Status**: Draft
**Epic**: #105 (first rung)
**Input**: Owner directive — "do the ML meta-question next." Grounding verified
2026-07-12: production has ZERO trained models, ZERO datasets, and no stored
predictions; the ML layer has never run on prod, so the honest foundation is
most of this feature.

## Why (context)

Discovery can currently ask "when does an *indicator* work?" This feature lets
it ask the more valuable question: **"when is the ML model actually
predictive?"** — treating the model's own predictions as the signal being
conditioned, judged by the exact same honesty machinery as every indicator
hunt (pre-registration, inner/outer segregation, one BH family, in-run SPA).
ML generates; statistics judges. The answer converts directly into
prediction-gating: knowing the market states in which to trust the model.

The hard prerequisite is causal honesty: a meta-hunt over predictions is
garbage if the model saw the future. v1 uses a **single-vintage design** —
train strictly on data up to a declared cutoff, then produce point-in-time
predictions for every trading day after it. The whole post-cutoff span is
genuinely out-of-sample; hunts are confined to it by construction.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The prod ML foundation exists and is causally clean (Priority: P1)

An operator builds the production dataset, trains the standard quantile model
with a declared training cutoff, and backfills point-in-time predictions for
the full post-cutoff span — each day's prediction using only information
available that day, from a model that never saw any post-cutoff data.

**Why this priority**: without this, there is nothing to hunt on; with it
done wrong, every downstream verdict is poisoned.

**Independent Test**: on a synthetic world with a planted post-cutoff regime
change, verify every stored prediction's provenance (model trained ≤ cutoff;
prediction inputs ≤ prediction date) and that predictions exist for ≥95% of
post-cutoff trading days.

**Acceptance Scenarios**:

1. **Given** the trained vintage model, **When** the backfill runs, **Then**
   every stored prediction records its model version, training cutoff, and
   prediction date, with prediction date strictly after the cutoff.
2. **Given** a backfill re-run, **Then** it is idempotent and incremental
   (resumes from the last predicted day; nightly top-up ready).
3. **Given** an attempt to predict a date ≤ the training cutoff via this
   door, **Then** it refuses (in-sample predictions are not signals).

### User Story 2 - Prediction signals are first-class discovery signals (Priority: P1)

A hunt pre-registers `signal_source = model_predictions` and names
prediction-derived series (the market's median model outlook; the model's
confidence width) exactly as it names indicator signals today. The loader
resolves them; every downstream guarantee (freeze, family counting, SPA,
horizon in verdicts) applies unchanged.

**Independent Test**: run a full synthetic discovery with model-prediction
signals; the run's ledger shows the declared source, the family counts every
test, and the in-run SPA verdict computes.

**Acceptance Scenarios**:

1. **Given** a hunt declaring model-prediction signals, **When** it runs,
   **Then** the pre-registration records the signal source, model version,
   and training cutoff — auditable like every other seam.
2. **Given** a hunt window extending at or before the training cutoff,
   **Then** the run refuses at pre-registration: lookahead by construction.
3. **Given** prediction coverage below a declared floor inside the hunt
   window, **Then** the run refuses naming the gap and the backfill command.

### User Story 3 - The meta-hunt runs on production (Priority: P2)

The operator runs the first real meta-hunt: model-prediction signals
conditioned on the proven state vocabulary (trend-strength, breadth,
dispersion) within the out-of-sample span, at a geometry that fits it. The
verdict — admitted or honestly rejected — lands with the same reporting as
every hunt, and the run records that its world is the out-of-sample span.

**Acceptance Scenarios**:

1. **Given** the meta-hunt completes, **Then** its verdicts state the
   horizon and the signal source, and the in-run SPA verdict is recorded.
2. **Given** entanglement between a conditioning state and the model's own
   input features, **Then** the screen treats model signals as derived from
   ALL the model's declared input features (the conservative rule), refusing
   states that would condition the model on itself.

### Edge Cases

- Days where the model produced no prediction (halted names, thin data)
  propagate as gaps in the derived series — never fabricated.
- A second model/vintage later: series names carry the model identity so
  hunts can't silently mix vintages.
- The nightly top-up failing must not corrupt the series (write-on-success,
  as everywhere).
- Curriculum/docs must not imply the model predictions are validated — the
  meta-hunt judges them; until then they are signals under test.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-1201**: The system MUST support building the production dataset and
  training the standard quantile model with a DECLARED training cutoff,
  recorded durably with the model.
- **FR-1202**: The system MUST provide a point-in-time prediction backfill
  for the post-cutoff span: each prediction uses only data available at its
  date; idempotent, incremental, resumable; runtime on the production host
  measured and documented.
- **FR-1203**: Prediction-derived market-level series (at minimum: median
  outlook, median confidence width) MUST be exposed as pre-registrable
  discovery signals, with gaps preserved and the model identity in the
  series provenance.
- **FR-1204**: `signal_source='model_predictions'` MUST be a real rung:
  declared at pre-registration, recorded in the run row, resolved by the
  signal loader; all existing discovery guarantees apply unchanged.
- **FR-1205**: Hunts using model signals MUST be confined to the
  out-of-sample span: any window touching the training cutoff or earlier
  refuses at pre-registration.
- **FR-1206**: The entanglement screen MUST treat a model signal as derived
  from all of the model's declared input features (conservative rule).
- **FR-1207**: Insufficient prediction coverage in the hunt window MUST
  refuse at pre-registration, naming the gap and the fixing command.
- **FR-1208**: Surfaces per house rules: CLI doors (backfill; discover
  start's signal-source flag), MCP parity, docs, curriculum (Module 10
  aside: ML as signal, statistics as judge; Module 3 pointer), /gefion
  routing, observability spans; nightly cron top-up after the backfill.
- **FR-1209**: Honest refusals throughout: missing model/dataset/predictions
  name the exact build command; nothing silently degrades to indicator
  signals.

### Key Entities

- **Vintage model**: the trained quantile model + its declared training
  cutoff (existing ML storage conventions; already covered by the
  `irreplaceable` backup type).
- **Point-in-time prediction**: per stock, per day, post-cutoff only, with
  model identity.
- **Prediction-derived series**: market-level daily series computed from the
  cross-section of predictions (median outlook; confidence width), gaps
  preserved.
- **Meta-hunt run**: a discovery run whose pre-registration records the
  signal source, model identity, and out-of-sample span.

## Automation *(consider)*

- Nightly cron gains a prediction top-up step after feat-compute (keeps the
  meta-huntable span growing daily).
- `/gefion` routing: "when does the model work?" → the meta-hunt recipe.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-1201**: Every stored production prediction is verifiably causal:
  model trained ≤ cutoff, prediction date > cutoff, inputs ≤ prediction
  date — enforced by tests and auditable from stored provenance.
- **SC-1202**: Prediction coverage ≥95% of post-cutoff trading days for the
  tradable universe; the backfill completes on the production host within a
  measured, documented budget (target ≤ 4 hours).
- **SC-1203**: A synthetic end-to-end discovery run with model-prediction
  signals completes with all guarantees intact (family counted, SPA
  computed, horizon stated) — CI-tested.
- **SC-1204**: Lookahead refusals proven: a hunt window at or before the
  cutoff cannot start (tested); in-sample prediction requests refuse
  (tested).
- **SC-1205**: The production meta-hunt completes and its verdict —
  whatever it is — is recorded with full provenance; zero changes to any
  existing indicator-hunt behavior (regression suites green).

## Out of Scope (v1 — remains on #105)

- Walk-forward / rolling retraining vintages (the upgrade path when one
  vintage's span is exhausted).
- The `strategy_backtests` signal rung.
- Automated forward-fold accrual.
- Prediction-gated strategy exploitation (a later backtest increment).

## Assumptions

- The existing quantile-model pipeline (dataset build, train, predict at a
  date) is functionally sound and only needs the vintage/backfill discipline
  and prod execution added around it.
- One declared horizon for v1 (the plan will fix it; the 30-day model
  horizon is the working default, nearest the proven 20-day regime finding).
- The post-cutoff span (~3.5 years with a 2022-12-31 cutoff) is long enough
  for a hunt at the declared horizon with the long-holdout geometry lesson
  applied; the spec accepts reduced statistical power as the price of
  causal honesty in v1.
- Prediction storage reuses existing prediction tables/conventions; no new
  value-storage surfaces expected (DDL only if unavoidable, owner approval
  then).
