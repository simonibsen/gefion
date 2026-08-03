# Adding a Universe (Exchange) — Runbook

The repeatable process for growing the modeling universe beyond its current set
(e.g. adding NYSE to a NASDAQ-only system). This is a **playbook, not a
one-off**: the goal of the first addition is to build the motion so every later
exchange is cheap.

> Program of record: **#179** (multi-exchange universe expansion). This runbook
> is the generic procedure; #179 is the NYSE-specific execution.

## Why expand

More names per date buys **statistical power** — more independent draws for
cross-sectional features, regime discovery, and the SPA (Superior Predictive
Ability) bootstrap. It is a bet, not a guarantee: it only pays when the
constraint is *samples*, not *signal*, and only in proportion to **effective-N**
(correlation-discounted), not raw symbol count. Measure it, don't assume it
(see Phase 1).

## First ask: how *different* is this universe?

The payoff bends with the **character** of the addition, not the count. Assess
where the candidate sits on the similarity axis before expecting a given kind of
value — there is a real tension:

> The more *different* a universe, the more it adds as independent information and
> diversification — but the **less** its edges transfer and the **harder** it is to
> pool. Similar = easy but low-info; different = high-info but hard.

- **Similar universe** (another US equity exchange — NYSE, NYSE American, IEX):
  ~all correlated with what you already have, so it **does not lower the average
  correlation** → no new discovery power, and **diminishing returns** with each
  addition. Its value is the **opportunity set** (more names to pick winners/losers
  from, fatter tails, capacity), edges **transfer** well, and pooling is easy with
  light conditioning.
- **Different universe** (international equities, other asset classes): less
  correlated → **raises the effective-N ceiling → real new discovery power**. But
  the tension bites: edges **transfer less** (different drivers/regimes), pooling is
  **harder** (very different universes may need *separate* models, not one pooled
  brain), the scope carry-over **breaks** (non-equity lacks the same fundamentals/
  indicator scaffolding), and **measurement artifacts** creep in (non-synchronous
  trading hours make cross-market correlations look artificially low).

**The effective-N ceiling is not universal.** Whatever the current universe's
correlation-bound ceiling is (measured ~10–13 for all-US-equity as of 2026-08),
it is a property of *that* universe — it **rises as you genuinely diversify**, and
does not carry over to a cross-asset or cross-geography program.

## The core rule: existing work carries over by *scope*

Work already done does **not** migrate uniformly. How each feature carries over
is decided by its scope (the spec-007 entity axis). Three buckets, three fates:

| Scope | Examples | What happens |
|-------|----------|--------------|
| **Per-stock** | raw fundamentals → forward-filled features (PE, market cap, EPS); per-symbol indicators | **Additive, near-free.** Computed per stock, peer-independent. Ingest the new symbols; the same feature functions apply unchanged. |
| **Membership-dependent** | cross-sectional ranks/z-scores; sector/industry breadth & signals (013/016) | **Recompute as a new vintage.** Aggregates over a peer set, so their meaning changes when membership changes. Cross-sectional compute is universe-scoped (015): the new names enter only when a universe rule admits them. |
| **Economy-wide** | macro series — FRED, VIX, oil, dollar index, composites | **Untouched.** Describes the economy, not a stock set. Identical for every exchange; do not re-derive. |

## Honesty invariants (do not break these)

- **The universe definition (015) is the switch.** New names never leak in via
  raw ingest; they enter a computation only when a universe *rule* admits them.
- **The vintage is the accounting.** Everything membership-dependent is
  recomputed and stamped with a new universe vintage. Provenance (015) records
  which universe a dataset/model/experiment used.
- **Re-vintage is a FULL-history recompute, never a splice.** Recompute
  cross-sectional and sector/industry features across *all* history under the
  new membership. Splicing (old rows on the old peer set, new rows on the new
  one) hands the model a silent discontinuity in what a feature *means*.
- **The SPA drift-refusal fires by design.** Edges admitted on the old universe
  must be **re-verdicted** on the new vintage (spec 010); the gate refusing to
  compare across incompatible peer sets is the machinery working, not a bug.
- **Point-in-time or nothing.** Sector/industry labels, fundamentals, and
  universe membership must be *as-of*. Applying current classifications
  historically is lookahead — it inflates backtests silently rather than
  throwing.
- **Models stay pooled; validation is per-universe.** Adding an exchange grows
  the single universe the model trains on — one pooled model, not one per
  exchange. "Works for exchange X" is an *evaluation* criterion: validate
  out-of-sample per exchange; split only on demonstrated negative transfer.

## The sequence (each phase has an exit gate)

Ordered cheapest-and-most-decisive first, so a "no" costs an afternoon, not a week.

1. **Baseline power on the current universe.** Record discovery/modeling power
   and vary *effective*-N synthetically. The anchor the power-per-universe curve
   (and every future exchange) is measured against.
   → *Gate: baseline captured — steers pace, not a blocker.*
2. **Confirm data availability.** Probe the data provider for the new exchange's
   listing + history depth at the production key; settle the **survivorship
   scope** (delisted names in, or claims explicitly bounded).
   → *Gate (hard): entitled + scope set → continue; else stop or scope down.*
3. **Ingest prices, measure.** Ingest daily prices for the exchange; enable
   compression *before* the bulk lands; verify storage tracks the projection.
   → *Gate: bars land clean.*
4. **Fundamentals + features, measure the scale tripwire.** Backfill
   fundamentals; compute features over the widened universe; capture real
   feat-compute duration and market-series query spans (Tempo).
   → *Gate: record the #154 triggers against their thresholds.*
5. **Universe rule + vintage.** Add the inclusive universe rule; **full-history
   recompute** of membership-dependent features; cut a new vintage; expect the
   SPA drift-refusal to fire.
   → *Gate: universe refreshes, provenance recorded, no splice at the boundary.*
6. **Re-evaluate #154.** With triggers measured, execute the pre-designed
   `macro_series` hypertable split **iff** real degradation shows (owner-approved
   DDL). With a multi-exchange roadmap, prefer doing it once — at the first
   exchange that shows pressure — so it absorbs every later exchange.
   → *Gate: real degradation → split; else leave the god-table as-is.*
7. **Re-run discovery on the wider cross-section.** Backfill predictions; re-run
   discovery/meta-hunt; compare admitted-edge power vs the baseline.
   → *Gate: did power actually improve? The retrospective verdict on Phase 1's bet.*

## Pitfalls checklist

- [ ] **Breadth ≠ effective-N** — judge each addition by effective-N gain
      (spec 005), not symbol count.
- [ ] **Entitlement & history depth** — probe before any bulk run.
- [ ] **Survivorship bias** — delisted names in, or claims bounded; decide
      *before* ingest (it changes what you fetch).
- [ ] **Point-in-time classification** — as-of sector/industry + fundamentals.
- [ ] **Data quality at scale** — run the spec-008 quality catalog + chokepoint
      exclusion over the new names before admitting them.
- [ ] **Exchange-specific test symbols** — each exchange publishes its own
      test/placeholder symbol family (NASDAQ's `Z*ZZT` set; NYSE has its own).
      Add the new exchange's to `universe.test_tickers` in
      `data-quality/catalog.yaml`, or they leak in as junk rows. (Most other
      catalog bounds are definitional and exchange-agnostic — tune only what a
      quality run flags.)
- [ ] **Cross-sectional / regime drift** — recompute + re-derive regimes as a
      new vintage.
- [ ] **Re-vintage = full recompute, not splice** — verify no discontinuity at
      the vintage boundary.
- [ ] **Exchange scoping** — universe rules already filter by exchange (the 015
      `exchange` predicate); this needs `stocks.exchange` populated, which the
      ingest now does. Admit the new exchange via a universe rule (config, not code).
- [ ] **Scale posture (#154)** — feat-compute likely crosses ~60 min; the
      `macro_series` split is the pre-designed escape hatch.

## Honest assessment

The design is **clean where it counts** — the entity (007) / universe (015) /
vintage seams already anticipate a second exchange, so an addition is mostly
data + config, not rework, and the honesty layer has survived a universe change
before (run-16). The risk has moved *off* the plumbing and onto two things:
whether pooling actually helps a **directional** target (negative transfer is
possible — validate per-exchange), and keeping the new data **point-in-time
honest** (where clean designs silently rot). Plan around those, not the build.

## References

- Epic **#179** — multi-exchange universe expansion.
- **#154** — `computed_features` scale posture / the `macro_series` split.
- **#192** — `stocks.exchange` persistence on ingest (enables exchange-scoped
  universe rules).
- Specs: 005 (effective-N), 006 (discovery), 007 (entity model), 010 (SPA
  re-verdict), 011 (market dispatcher scope), 013 (sector signals), 015
  (modeling universe), 016 (industry series), 017 (fundamentals vintage).
