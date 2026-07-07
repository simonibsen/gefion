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
gefion chart regime vol-regime --symbol SPY         # price with episode bands
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

## Agentic discovery (spec 006)

Spec 005 lets a **human** specify a regime; discovery lets the **agent** propose and test
regimes itself. Done naively this is a false-positive machine — the entire design is about
making the failure modes *structurally impossible*, not merely discouraged.

### The six traps

- **T1 — Double-dipping / outcome leakage.** Fitting a regime on the data that then judges
  its edge fits the regime *to the outcome*. Invisible in the result; the most dangerous trap.
- **T2 — Unbounded search.** "Search until something conditions well" guarantees false
  positives unless the *full* search — including every loser — is counted in the correction.
- **T3 — Fitted-boundary degrees of freedom.** Thresholds/HMM parameters/cluster boundaries
  are themselves fitted; ignoring that under-charges for complexity.
- **T4 — Selection after peeking.** Choosing which regime or bucket to report after seeing
  results invalidates naive p-values.
- **T5 — Silent survivorship.** Dropping failed candidates shrinks the correction's
  denominator below the true search size.
- **T6 — Non-reproducible search.** If a run can't be reproduced, its accounting can't be
  audited, so its verdicts can't be trusted.

### The defense stack

1. **Nested segregation** (T1/T3): discovery and fitting see **inner-window data only** —
   the `DiscoveryDataContext` is the sole data path and raises on any outer-holdout touch.
   Detectors then label the holdout *causally* (labels at *t* use data ≤ *t* only).
   Discovery is a real selection step, not a formality: a candidate must first show
   evidence on the inner window (p ≤ the declared `inner_screen`, default 0.05) before it
   is allowed to *spend* the outer holdout at all. Because selection and judgment use
   disjoint data, the conjunction multiplies the false-admission probability down
   (≈ P(inner pass) × FDR rate) instead of leaving it at the FDR rate alone.
2. **Pre-registration** (T2/T5): the search space — atom library, depth cap *K*, budget, and
   the three declared seams (`signal_source`, `grading_scheme`, `universe_filter`) — is
   written to the run row **before** anything is evaluated. The universe filter chain is
   always declared; an unfiltered run requires explicit `passthrough`, never a silent fallback.
3. **Candidate freeze** (T4): the run lifecycle is
   `pre_registered → enumerated → evaluated → complete` (or `invalid`); evaluation is
   API-impossible before the candidate set is frozen at `enumerated`.
4. **One flat FDR family that counts the losers** (T2/T4/T5): every
   (signal × candidate × bucket) test actually spent on the outer holdout enters a single
   Benjamini-Hochberg call; the realized `family_size` is recorded on the run. Candidates
   screened out on inner data spent no outer test (their inner evidence is still ledgered);
   refused tests (low power, degenerate) get **no p-value and cannot survive**
   (fail-closed). Discovery's gate runs at a much stricter FDR rate (0.01) than standard
   experiments (0.10) — search volume is the risk.
5. **Seeded, auditable runs** (T6): every run records its seed and full candidate +
   diagnostics ledgers; identical inputs reproduce identical verdicts.

### Expressiveness tiers (scale with data, not a free-for-all)

- **interaction** (default): the gradient question — one HAC interaction coefficient per
  (signal × candidate); cheap, honest, no bucket search.
- **grammar**: compositions from the pre-registered atom library up to depth *K* (hard cap
  2 in v1; raising it is gated on the data-snooping-robust bootstrap fast-follow). The
  enumeration is deterministic and exact, so the FDR denominator is exact.
- **expressive**: free-form expressions and sandboxed detector functions (HMM/clustering) —
  admissible **only** against a declared, single-use fresh-holdout reserve.

### The fresh-holdout reserve (expressive tier)

Free-form search is unbounded, and no correction can rescue an unbounded, data-reusing
search — so expressive candidates are judged on a **reserve**: a declared, dated block,
distinct from the outer holdout and *excluded from the inner window* (the
`DiscoveryDataContext` refuses reserve dates during discovery). Reserve honesty is
entirely about non-reuse:

- A block is **single-use**: consumption is recorded on the run
  (`reserve_consumed`), and re-declaring a consumed (or overlapping) block is refused.
- The only escape is an **explicit, recorded justification**
  (`--reserve-justification`), stored in the new run's pre-registration with the run
  ids it overrides — auditable, never silent.
- Detector candidates run in the same whitelisted-import sandbox as AI-generated
  features, fit strictly on inner data (fitted parameters are ledgered — T3's degrees
  of freedom are recorded), and face three screens before any reserve data is spent:
  **degeneracy** (a bucket holding >90% or <2%), **instability** (seeded refits that
  disagree), and **non-causality** (labels that change when the future is truncated
  away). Refusals are verdicts and diagnostics, not silent drops.
- An admitted detector becomes a `feature_functions` row plus a `RegimeDefinition`
  with a `detector_function` leaf — the 005 FR-019a gated escape hatch, whose runtime
  this feature provides.

### Discovery inside experiment cycles

`gefion experiment propose --type regime_discovery` runs discovery under cycle budgets
(the cycle budget maps to the candidate budget). Risk class **high** — cycles never
auto-approve it — and the experiment earns no cycle-level holdout p-value (NULL fails
closed); the honest verdicts live in the run's own ledgers. Principle seeding
(`--principles hurst-exponent-regime,regime-detection-hmm`, or `principle_ids` in the
experiment config) builds atom libraries and detector candidates from the catalog,
each carrying provenance to its seeding principle.

### Reading a run

```bash
gefion regime discover start --name first-hunt --atoms atoms.json \
  --depth 1 --budget 50 --tier interaction --tier grammar --seed 42
gefion regime discover list                  # runs, status, family size
gefion regime discover show first-hunt       # pre-registration + segregation
gefion regime discover ledger first-hunt     # every candidate, every verdict
gefion regime discover verdicts first-hunt   # survivors (if any) + family size
```

### Counting the losers

The candidate ledger is the honesty mechanism, not an audit log. Every candidate the
search evaluated is persisted with its verdict — `admitted`, `rejected`, or a refusal
(`refused_low_power` / `refused_degenerate` / `refused_unstable`) — and the run records
`family_size`, the number of p-valued tests that entered the single Benjamini-Hochberg
call. Two invariants make cherry-picking impossible at the API level:

- **Refusals never enter the family** (they have no p-value and cannot survive), but they
  are always persisted and visible — a refusal is a diagnostic, not a deletion.
- **Evaluated candidates are always counted.** `counted_in_family` is derived from the
  verdict by the ledger itself; a caller cannot un-count a loser to shrink the
  denominator, and verdicts are only derivable from the one recorded family run —
  there is no API to re-test a single hand-picked bucket after peeking.

When reading `verdicts`, the family size is always shown beside the survivors: "1
admitted out of a 240-test family" and "1 admitted out of 3 tests" are very different
claims.

**Expect mostly (often entirely) rejections.** A discovery loop that admits often is broken.

### The standing negative control (CI)

`tests/test_discovery_negative_control.py` runs the FULL pipeline in CI, every PR:

- **SC-101**: pure-noise data, both tiers, 20 fixed seeds → **zero** admissions, with the
  ledgers audited (family recorded, losers persisted and counted).
- **SC-102**: an edge planted in exactly one regime (cancellation design: concentrated
  inside, anti-edge outside, flat overall) among decoys → exact recovery in ≥ 95% of 20
  seeded runs (measured: 40/40 on a wider scan).

Read the fine print honestly: the seed sets were fixed a priori and runs are
deterministic, so CI proves the *machinery* (segregation, freeze, counting, fail-closed)
as a stable regression guarantee. The measured false-admission rate at v1 defaults over a
wider 100-seed noise scan was **1/100** — that ~1% is the configured error rate working
as specified, not a defect; no gate with a nonzero admission rate is zero everywhere.
The Reality-Check/SPA bootstrap fast-follow (FR-108) must land before per-cycle search
budgets are raised beyond v1 defaults.

An admitted
candidate becomes an ordinary `regime_definitions` row (`origin='machine'`) with full 005
affordances (labels, chart, slicing). Structural limits the search hits (missing inputs,
budget/depth exhaustion) and sample-dependent refusals are recorded in the diagnostics
ledger — the negative space is a learning signal, not noise.

Every discovery operation is reachable via CLI (`gefion regime discover …`), MCP
(`regime_discover_*` tools), and the UI **Regimes → Discovery** tab.

## See also

- Spec: [specs/005-regime-slicing/](../specs/005-regime-slicing/)
- Discovery spec: [specs/006-agentic-regime-discovery/](../specs/006-agentic-regime-discovery/)
- Backtesting metrics: [docs/BACKTESTING.md](BACKTESTING.md)
