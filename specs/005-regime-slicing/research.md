# Phase 0 Research: Regime Slicing (005)

The five load-bearing decisions were fixed in `/speckit.clarify` (see spec Clarifications).
This document resolves the remaining *technical how* unknowns those decisions imply.

## R1 — Effective (independence-adjusted) sample size

**Decision**: Effective-N = **number of independent episodes** (contiguous runs) a regime label
occupies in the evaluated window, used as the low-power floor for per-regime *metrics*. For the
conditional *edge test*, use block-aware standard errors (stationary/moving-block bootstrap, or
Newey-West/HAC) so within-episode autocorrelation does not inflate significance. Default floor:
configurable, **≥ 20 independent episodes** (equivalently a block-adjusted N≥30) — expressed in
effective terms, tunable per deployment.

**Rationale**: A 40-day episode is ~one independent observation for regime-level inference;
counting raw days overstates power (spec Clarification Q3). Episode count is a natural, honest
effective-N once regimes are persistent, and it is cheap to compute from the label run-lengths.

**Alternatives considered**: raw day-count (rejected — overstates power); autocorrelation-time
adjusted N via ACF integral (kept as an optional refinement for the interaction test, heavier);
fixed calendar minimum (rejected — ignores episode structure).

## R2 — Persistence / hysteresis mechanism

**Decision**: **Minimum-dwell smoothing** as the default optional control — a raw label change is
confirmed only after it persists K consecutive periods (K configurable, suggested default 3 when
opted in); a symmetric enter/exit variant (Schmitt trigger) is available for asymmetric cases.
Realized dwell-time (mean run-length) is **always** computed and stored; flicker (mean dwell below
a small floor) is **always** flagged (spec Clarification Q5, FR-021).

**Rationale**: Min-dwell is the simplest transform that turns scattered threshold matches into
economically meaningful episodes, and it composes with any leaf. Always-measure/always-flag keeps
persistence a grade, not a gate.

**Alternatives considered**: HMM smoothing (rejected as a *default* — that is a detector-function
leaf, not a universal control); exponential smoothing of the underlying signal (rejected — changes
the semantics of the condition rather than the label).

## R3 — RegimeExpression AST vocabulary

**Decision**: Leaves ∈ { **comparison**(feature_ref, op, threshold|quantile), **reference**(named
atomic regime), **detector_function**(sandboxed, gated) }. Nodes ∈ { AND, OR, NOT }. Directional
conditions ("rising") are ordinary comparisons over a derived causal feature (sign of rolling
slope). Operators: `< <= > >= == in`. Atomic numeric leaves bucket via causal quantiles/terciles
or fixed thresholds; a composite yields the cross-product of child labels, or a boolean →
{true,false}. All feature refs resolve to existing causal computed features / cross-sectional
features, guaranteeing no-lookahead at the leaf.

**Rationale**: A small, closed vocabulary keeps the search space countable (006's FDR needs this)
and causal by construction, while covering the manual examples (e.g. "VIX rising AND defense
volume rising"). The detector-function leaf preserves full expressiveness where declarative form
cannot reach (spec Clarification Q1).

**Alternatives considered**: full DSL grammar with arithmetic (deferred — syntax sugar, R-item for
006); arbitrary code as the primary form (rejected — not countable, not causal-by-construction).

## R4 — Storage shape

**Decision**: `regime_definitions` (relational; JSONB columns for the AST, persistence config,
dataset provenance, and descriptive metadata). `regime_labels` (**TimescaleDB hypertable** keyed
by (regime_id, date[, entity_id]), one label + `undefined` sentinel per row). Definitions exported
to `regime-definitions/*.json` (Database-First backup, like `feature-definitions/`). **All DDL is
PROPOSED for owner approval** (Schema Governance); two-file rule (schema.sql + migration).

**Rationale**: Labels are time-series → hypertable, consistent with `computed_features`/`predictions`.
Definitions are low-cardinality config → relational with JSONB for the flexible AST/metadata.

**Alternatives considered**: computing labels on the fly every query (rejected — repeated cost,
and undermines the single causality-enforcement point); storing labels in `computed_features`
(rejected — different semantics, conflates features with regime state).

## R5 — Continuous-interaction test

**Decision**: OLS of realized forward return on `signal + conditioning + signal×conditioning`, with
**HAC (Newey-West) standard errors** for autocorrelation; report the interaction coefficient and
its p-value (spec Clarification Q4). Runs inside the existing evaluation path so it shares data
loading and the holdout window.

**Rationale**: One coefficient, one p-value, interpretable, standard; HAC keeps inference honest
under serial correlation without extra machinery.

**Alternatives considered**: rank/Spearman monotonic test (deferred robustness extension); spline
interaction (deferred — nonparametric, heavier).

## R6 — Conditional evaluation + FDR wiring

**Decision**: Extend the holdout-evaluation path to compute a per-regime holdout p-value per
(experiment × regime × bucket) via the existing `experiments.holdout` slice + `compute_holdout_pvalue`,
then pass the **entire realized family** to `experiments.statistical.apply_fdr` (flat BH, spec
Clarification Q2). Low-power / undefined buckets emit no p-value and fail closed (FR-012).

**Rationale**: Reuses the two statistical primitives already trusted by the experiment framework;
the only new logic is slicing the holdout by label and assembling the family — the exact seam the
spec's "make the gate conditional" requires.

**Alternatives considered**: a bespoke FDR implementation (rejected — duplicates `apply_fdr`);
per-regime independent gates (rejected — hides the multiple testing, the cardinal sin).

## R7 — Reconciliation

**Decision**: Per-regime trade counts and summed return MUST equal the un-sliced totals within a
rounding tolerance; enforced by an assertion in `slicing.py` and a dedicated test. `undefined`
periods are excluded from buckets but counted in a residual line so the sum still closes.

**Rationale**: Reconciliation (FR-009) is the guard against a slicing bug silently dropping or
double-counting trades at bucket boundaries.
