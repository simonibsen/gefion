# Research — Model Signals (012)

## R1 — Causality vintage
**Decision**: single vintage; dataset end-date = cutoff 2022-12-31; cutoff in
model metadata; every downstream door validates against it.
**Alternatives**: walk-forward (out of scope, the honest upgrade), no-cutoff
(rejected: poisons the meta-question).

## R2 — Backfill door
**Decision**: `ml predict-backfill --model-name --model-version --end?` loops
trading days from max(prediction_date)+1 (or cutoff+1) to end, calling the
existing predict path per date; PK dedup = idempotency; per-date spans.
**Alternatives**: bulk matrix predict (rejected: existing per-date path
guarantees point-in-time feature reads).

## R3 — Predictions as signals: maximal reuse
**Decision**: materialize per-stock prediction values into computed_features
as namespaced features (`pred_q50_h30__<model>`, `pred_q10...`, `pred_q90...`)
with provenance in the definition metadata; derive market-level series via
TWO 011 market bodies (`model_outlook_q50`, `model_confidence_width`);
discovery loads them like any feature. Zero new loaders/storage.
**Alternatives**: bespoke predictions-table loader in discovery (rejected:
new path, new tests, no lifecycle); market bodies reading predictions table
directly (rejected: 011 executor reads features by design — keep its contract
closed).

## R4 — Rung semantics
**Decision**: `--signal-source model_predictions` switches the allowed signal
namespace to model-derived series, requires window start > cutoff, enforces a
coverage floor (≥95% trading days in window), and extends entanglement:
atoms whose feature is in the model's dataset feature list (from the dataset
manifest) OR is itself model-derived are refused.
**Rationale**: conditioning the model on its own inputs asks "does the model
work when its inputs say X" — outcome leakage T1-style for the meta-question.

## R5 — Meta-hunt geometry
**Decision**: hunt horizon 20d, holdout 80wk, inner = cutoff+1 → holdout
start (~2y). States: adx/rsi_30/breadth/dispersion terciles (all
non-entangled iff absent from the model's feature list — verify at plan-time
of the hunt; drop any that collide).
