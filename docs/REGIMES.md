# Regimes — Conditional Evaluation Across Market/Sector/Asset States

A **regime** is a named, causal, persistent, time-indexed description of the *state* of the
market, a sector, an industry, or an asset — e.g. "market volatility is calm / normal /
stressed", or "VIX rising AND defense-industry volume rising". Regimes let you evaluate a
signal or strategy *conditionally*: not just "does this edge exist on average?" but "*when*
does it exist?"

## Why not just a computed feature?

A regime *feature* makes the model's **prediction** conditional (the model can learn to use it).
Regime **slicing** makes the statistical **gate itself** conditional — it can report
"significant in high-dispersion regimes (p=0.01), noise elsewhere (p=0.42)", which an aggregate
holdout p-value structurally cannot. Slicing also surfaces the *cancellation problem* (an edge
that nets flat overall but is real in one regime and masked by noise in another) and forces
honest power accounting. Rule of thumb: **slicing is discovery and inference; a regime feature
is exploitation.** You slice to find where an edge lives, then (optionally) build the feature.

## Terminology: market regime ≠ ML regime

These are two different uses of the word "regime":

- **Market regime** (this doc): a property of the *environment being modeled*, indexed by
  **time** — volatility level, trend vs. chop, dispersion, macro state.
- **ML regime**: a property of the *learner*, indexed by model capacity / data / compute —
  e.g. underparameterized vs. overparameterized (double descent), lazy (NTK) vs.
  feature-learning, and scaling-law regimes.

They share only the intellectual move "condition, don't average." Everything below is about
**market** regimes.

## Core guarantees

- **Causal by construction.** Every label at time *t* uses only data at or before *t* (no
  lookahead). Rolling bucket boundaries (e.g. terciles) are fit on past-only windows — never
  over the whole evaluation window (that would be lookahead bias baked into the analysis).
- **Persistent, not scattered.** Regimes are *states you dwell in*. Optional hysteresis /
  minimum-dwell forms contiguous episodes; realized dwell-time is always measured and flicker
  is always flagged (grade, not gate).
- **Honest power.** The low-power guard is based on **effective** (independence-adjusted) sample
  size — the count of independent episodes — not raw day-count, because persistent, autocorrelated
  regimes make raw counts overstate power. Under-powered buckets are refused, not reported.

## Representation

A regime is a **declarative expression tree (AST)**: leaves are atomic causal conditions
(`comparison` over a feature, or a `reference` to another regime), nodes are boolean operators
(`AND`/`OR`/`NOT`). Under composition, a leaf resolves at its own scope and the composite's
output scope is the **finest** scope involved. A sandboxed **detector-function leaf** is a gated
escape hatch for detectors that can't be expressed declaratively (HMM, clustering) — admissible
only under stricter validation (see spec 006, agentic discovery).

## Conditional evaluation & the gate

Per-regime holdout p-values enter one **flat Benjamini-Hochberg** family over the full realized
set of (experiment × regime × bucket) tests, so the added multiple testing is corrected, never
hidden. Fail-closed: a low-power or `undefined` bucket produces no p-value and cannot survive.

For a smooth relationship ("does the edge scale with volatility?"), use the **continuous-interaction**
test — a single linear interaction term (signal × conditioning variable), one coefficient and one
p-value — instead of a grid of buckets.

## CLI

```bash
gefion regime define --name vol-regime --scope market \
  --expression expr.json --bucketing buckets.json
gefion regime compute vol-regime --dataset dev
gefion regime labels  vol-regime
gefion backtest run ... --by-regime vol-regime      # per-regime metrics (US2)
gefion regime interaction --signal momentum --by realized_vol_20   # gradient (US5)
gefion experiment run --id 1 --by-regime vol-regime # conditional verdicts (US3)
```

### Conditional experiment verdicts

`gefion experiment run --id N --by-regime <name>` additionally evaluates the holdout
*conditionally*: per-observation holdout scores are bucketed by the regime's causal labels,
each bucket earns its own one-sided holdout p-value, and every (regime × bucket) test enters
one **flat Benjamini-Hochberg family** — the added multiple testing is corrected, never
hidden. Fail-closed: a bucket below the effective-sample floor, or with undefined labels,
gets **no p-value and cannot survive**. Experiment types that do not emit per-observation
holdout scores report "conditional evaluation unavailable" honestly instead of fabricating
a verdict.

Every operation is reachable via CLI, MCP (`regime_*` tools), and the UI **Regimes** page.

## See also

- Spec: [specs/005-regime-slicing/](../specs/005-regime-slicing/)
- Backtesting metrics: [docs/BACKTESTING.md](BACKTESTING.md)
