# Phase 0 Research: Agentic Regime Discovery (006)

The five load-bearing decisions were fixed in `/speckit.clarify` (spec Clarifications).
This resolves the remaining *technical how*.

## R1 — Grammar representation & exact enumeration

**Decision**: A candidate is a 005 `RegimeExpression` AST (`origin='machine'`). The primitive
library is a declared list of **atoms** — (conditioning feature, comparator/quantile form,
threshold set) — and composition is AND/OR over atoms up to depth *K*. Enumeration is
**deterministic** (sorted atom order, canonical AST form) and **exact**: the realized candidate
count *is* the pre-registered family denominator. Each candidate gets a content hash
(canonical-JSON SHA) for dedup, ledger identity, and resume.

**Rationale**: reusing the 005 AST means discovered candidates are ordinary regime definitions
— storable, chartable, sliceable with zero new machinery. Determinism + hashing gives
reproducibility (FR-111) for free.

**Alternatives**: bespoke candidate encoding (rejected — duplicates the AST); random sampling
of a larger space (rejected for v1 — breaks exact counting; may return with the bootstrap).

## R2 — Nested segregation mechanics

**Decision**: A `DiscoveryDataContext` object is the *only* data access path during discovery.
It is constructed from an outer `HoldoutWindow` (reused from `experiments.holdout`) and exposes
inner-window rows only; its boundaries are recorded in the run row, and it raises on any
request touching the outer holdout. Edge verdicts are computed by a separate evaluation step
that alone may read the outer holdout — after the candidate set is frozen (ledger written).

**Rationale**: enforcement by construction, not convention (FR-101/102) — the same pattern as
005's causality-by-construction leaves. The "freeze then evaluate" order makes T4 selection
impossible at the API level.

**Alternatives**: trusting evaluators to filter dates (rejected — that is convention, the
thing the spec forbids).

## R3 — v1 conditional edge test (signal_source=features)

**Decision**: For each (active feature × candidate × bucket): per-observation records are
(date, feature-vs-forward-return alignment) built once per feature; the bucket test reuses
**`conditional_pvalues`** (paired per-observation contract) and the whole realized family goes
through **`apply_fdr`** in one flat call. The interaction tier uses `continuous_interaction`
(HAC) with the candidate's conditioning series — one p-value per (feature × candidate).

**Rationale**: zero new statistics in v1 (Clarification Q1 + Simplicity); every primitive is
already production-validated.

## R4 — Fresh-holdout reserve (expressive tier)

**Decision**: A declared, dated data block **distinct from the outer holdout**, registered in
the run's pre-registration and tracked in `regime_discovery_runs` (which reserve block was
consumed). A reserve block is single-use: re-declaring a consumed block for a new free-form/
detector validation is refused unless explicitly re-declared with a recorded justification.

**Rationale**: fresh-holdout honesty is entirely about non-reuse; making consumption a DB fact
makes reuse auditable and refusable (FR-118a).

## R5 — Detector runtime (expressive tier)

**Decision**: Detector candidates execute through the **existing feature-function sandbox**
(same whitelisted-import execution path as AI-generated features). Contract: `fit(inner_rows)
→ params`, `label(rows, params) → per-date labels`, fit strictly on the DiscoveryDataContext.
Guards: **degeneracy** (any bucket > 90% or < 2% share → refused), **stability** (label
agreement across ≥3 seeded refits < threshold → refused), both recorded as diagnostics.

**Rationale**: no new sandbox (Simplicity + security posture already reviewed); stability and
degeneracy are the cheap, effective T3 screens before the fresh-holdout test spends reserve.

## R6 — Ledger & diagnostics storage

**Decision**: Three new tables (PROPOSED, gated): `regime_discovery_runs` (seed, search-space
JSONB incl. signal_source/grading_scheme/universe_filter chain, budgets, segregation
boundaries, reserve consumption, status), `regime_candidates` (run FK, candidate hash, AST
JSONB, tier, per-test results JSONB, counted-in-family flag, verdict), `discovery_diagnostics`
(run FK, kind, quantitative reason JSONB, `sample_dependent` bool). Trust grades in
`regime_trust_grades` (edge FK, fold, confirmed bool, graded_at) so the grade **accrues** as
rows. Admitted regimes are upserted into `regime_definitions` with `origin='machine'`.

**Rationale**: the ledger IS the honesty mechanism (FR-104/105/106) — it must be relational and
queryable, not log lines. JSONB for the flexible shapes, exactly the 005 pattern.

## R7 — Negative controls in CI

**Decision**: A synthetic generator (seeded GBM prices + noise features for a ~20-symbol,
~500-day toy universe) lives in tests; the negative-control test runs the FULL pipeline
(enumerate → segregate → test → FDR) across ≥20 seeds asserting **zero survivors** (SC-101),
plus a planted-regime recovery test (SC-102: a feature edge injected only inside a planted
regime's dates must be found; decoys rejected). Budgeted to < 5 min in CI via the tiny
universe and depth K=1.

**Rationale**: this is the feature's own proof; running it in CI makes the guarantee standing
rather than anecdotal (FR-112).

## R8 — Walk-forward grading integration

**Decision**: `grading_scheme='walk_forward'` registers admitted edges with the existing
**probation** mechanism: the probation window is fold 1; each scheduled probation re-check
appends a `regime_trust_grades` row (confirmed/failed). Fold length is declared config in the
pre-registration. Backward era-slices (when requested) are computed via 005 slicing and stored
with `descriptive=true`, never entering the grade. The `GradingScheme` interface exposes only
`register(edge)`, `record_forward_result(edge, fold, outcome)` and `grade(edge)` — there is
structurally no API to add a backward confirmation (Clarification Q3).

**Rationale**: probation already runs on every data-update; grading rides it instead of
inventing a scheduler.

## R9 — Experiment-type integration

**Decision**: `regime_discovery` becomes an experiment type module
(`experiments/types/regime_discovery.py`) dispatched from `experiments/core.py`, so cycles can
budget/approve/run discovery like any experiment; its "trials" are candidate batches and its
holdout p-values are the family survivors. Risk class: **high** (never auto-approved).

**Rationale**: FR-109; reuses cycle budgeting/approval and keeps FR-042 parity (cycles are
already on all three surfaces).
