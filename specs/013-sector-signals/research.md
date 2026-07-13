# Research — Sector-State Signals (013)

## R1 — How sector series are computed
**Decision**: generated DB-resident market bodies (one per sector × metric)
executed by the 011 dispatcher, whose streamed cross-section gains the
`sector` column. Each body filters its sector's rows and computes against
the full cross-section available in the same call (relative strength needs
both).
**Rationale**: Constitution I (logic in the database) + the 011 owner
decision ("expensive version, correct from the start") + 012 precedent for
generated bodies carrying identity in the name. The dispatcher change is two
lines; every guarantee (sandbox, gaps, write-on-success) is inherited.
**Alternatives considered**: one-pass Python aggregator computing all
sectors in a single stream (rejected: logic leaves the DB; faster, but that
optimization belongs to #120 if the measured cost warrants); regime labels
as atoms (rejected in spec: labels stay in the conditional-evaluation
world).

## R2 — Naming
**Decision**: slug = sector lowercased, every non-alphanumeric run → `_`,
trimmed ("FINANCIAL SERVICES" → `financial_services`). Function names
`sector_rs_<slug>` / `sector_breadth_<slug>`; derived features get the
standard `macro_` prefix. Collision-free with `macro_model_*` and `pred_*`.
**Rationale**: deterministic, stable across runs; the seeding door refuses
ambiguous collisions (two sectors mapping to one slug) loudly rather than
merging them.

## R3 — Membership floor
**Decision**: `MIN_MEMBERS = 30` written INTO each generated body; a
(sector, date) with fewer members returns None (gap). The seeding door also
applies a census floor (`--min-members`, default 100) so micro-sectors
never get bodies at all.
**Rationale**: the body is the declared, operator-editable law (011); a
tercile atom over a 5-member "sector" would be noise wearing a name. 30
members ≈ the smallest cross-section whose median moves slower than its
constituents; the census floor keeps the function list to sectors that can
sustain a hunt (6 sectors ≥ 196 members on prod today).

## R4 — `derive --series all` semantics
**Decision**: 'all' = sorted(SEED_BODIES ∪ enabled scope='market' DB
functions). The DB is the source of truth; anything seeded (sector, model,
future) is covered by the nightly cron with zero crontab edits.
**Rationale**: the current SEED_BODIES-only expansion silently excludes
DB-resident series — the exact drift the 011 design exists to prevent.
Disabled functions stay skipped-and-reported, preserving the kill switch.

## R5 — Sector-hunt geometry (task #48)
**Decision**: h=20d, holdout 80wk, budget/depth within v1 caps, seed
declared at launch; atoms = sector RS + breadth terciles for the top-6
census sectors + the proven market vocabulary (ADX/RSI-30/breadth/
dispersion); signals = standard feature signals (NOT model predictions).
**Rationale**: run-13 lessons (20d horizon at floor 20 over long history);
one new dimension at a time — mixing the model rung into the first sector
hunt would confound whose verdict it is.

## R6 — Membership vintage caveat
**Decision**: current `stocks.sector` labels the past (a stock reclassified
in 2024 counts as its 2024 sector in 2015 aggregates). Recorded verbatim in
REGIMES docs beside the adjusted-price caveat; vintaged membership is out
of scope.
**Rationale**: same honesty class as 012's restatement caveat — no outcome
leakage (sector labels don't encode future returns), but not tick-perfect
history; hunts read states, and terciles dampen level effects.
