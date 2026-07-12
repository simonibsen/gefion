# Implementation Plan: Model-Prediction Signals for Discovery (012)

**Branch**: `012-model-signals` | **Date**: 2026-07-12 | **Spec**: [spec.md](spec.md)
**Epic**: #105 (first rung)

## Summary

Foundation-first: train the standard quantile model on prod with a declared
cutoff (2022-12-31), backfill point-in-time predictions over the post-cutoff
span, then MAXIMAL REUSE for everything downstream — predictions materialize
as per-stock features, the 011 market dispatcher derives the market-level
signal series, and discovery consumes them through the existing loaders. The
`model_predictions` rung adds pre-registration semantics + lookahead/coverage
refusals + the conservative entanglement rule. NO DDL.

## Technical Context

**Reused wholesale**: ml pipeline (dataset.py/train/predict, `predictions`
hypertable with model_id/prediction_date/horizon PK — idempotent by PK);
computed_features store; 011 market-function mode (scope='market' DB bodies)
for the derived series; discovery seams (signal_source already pre-registered
in search_space since 006); backup `irreplaceable` type already covers
ml_datasets/ml_runs/ml_models.
**New surface**: `ml predict-backfill` CLI (point-in-time loop, resumable);
prediction→feature materialization (`pred_q50_h30__<model>` etc. as per-stock
feature definitions with provenance metadata); two 011-mode market bodies
(`model_outlook_q50`, `model_confidence_width`); rung enforcement in
`discover start` (window > cutoff, coverage floor, entanglement vs the
model's dataset feature list).
**Causality spine**: dataset built with end ≤ cutoff; cutoff recorded in the
model's metadata; backfill refuses dates ≤ cutoff; discovery refuses windows
touching ≤ cutoff.

## Constitution Check

I Database-First PASS · II TDD PASS · III CLI-First PASS · IV Observability
PASS (spans on backfill/materialize/rung) · V Presentation PASS · VI
Simplicity PASS (reuse-over-new throughout) · Schema Governance PASS (no DDL).

## Increments

1. **Feasibility probe (sloth, early)**: time one day's predict over the
   universe → extrapolate backfill budget vs SC-1202 (≤4h); report before the
   full run.
2. **Vintage training** (T-tests first on synthetic): dataset build with
   declared cutoff; cutoff recorded in model metadata; refusals for missing
   pieces name build commands.
3. **Point-in-time backfill**: `ml predict-backfill` — per-date loop,
   refuses dates ≤ cutoff, resumable from max(prediction_date), idempotent
   via PK; nightly cron top-up line (post-merge).
4. **Materialize + derive**: predictions → per-stock features (idempotent,
   provenance metadata incl. model identity + cutoff); two market bodies
   seeded via 011 (`model_outlook_q50` = median pred_q50; 
   `model_confidence_width` = median (q90−q10)); gaps preserved end-to-end.
5. **The rung**: `discover start --signal-source model_predictions` — signal
   namespace switches to model-derived series; refuses window ≤ cutoff;
   coverage floor; entanglement = atom feature ∈ model's dataset features ∪
   model-derived series; recorded in search_space with model identity.
6. **Meta-hunt + polish**: docs/curriculum/MCP; fresh-DB suite; PR/merge;
   prod: train → backfill (measured) → materialize/derive → the meta-hunt
   (h=20d, holdout 80wk, states: ADX/RSI-30/breadth/dispersion terciles) →
   report verdict → #105 update.

## Interfaces, Documentation & Learning Impact

CLI: `ml predict-backfill`, `discover start --signal-source`; MCP parity
(backfill tool; discover_start arg passes through); docs USER_GUIDE ML +
REGIMES rung section; curriculum Module 10 aside (ML as signal, statistics as
judge) + Module 3 pointer; /gefion routing "when does the model work?".

## Complexity Tracking

- Backfill runtime is THE risk → increment 1 measures before committing.
- Horizon note: model horizon 30d (standard), hunt horizon 20d (proven
  geometry) — independent by design; verdicts state the hunt horizon, the
  signal series carries the model horizon in its name.
- Prediction-as-feature materialization must not confuse per-stock feature
  consumers → names are namespaced (`pred_*__<model>`), inactive for
  feat-compute (function rows registered, scope stock, body pointer).
