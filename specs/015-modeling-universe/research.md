# Research: Modeling Universe Membership (015)

## R1. Current population-selection landscape (codebase survey)

A full sweep found **three parallel idioms and ~15 inline sites**, none sharing
a chokepoint:

- **A. Manifest-driven**: `ml/dataset.py:52 resolve_universe_symbols` — explicit
  symbols win; else `SELECT symbol FROM stocks` + optional exchange/limit, then
  test-ticker filter. The dataset export fallback (`dataset.py:191`) exports
  **every** stock when no symbols resolve. No status/asset_type filter.
- **B. Declared filter chain**: `regimes/discovery/universe.py` —
  `DEFAULT_CHAIN = "test_tickers,asset_type:common"`, audited in run records.
  The most principled existing pattern; philosophically the ancestor of this
  feature, but private to discovery.
- **C. Ad-hoc inline SQL**: at least 12 sites with mutually inconsistent
  filters: `status='Active'` (backtest loader), `status IS DISTINCT FROM
  'Inactive'` (cross-sectional rankings), `asset_type='Stock'`
  (`dispatcher.py:1572 run_market_function` — the breadth/dispersion cross
  section), and **no filter at all** (feat-compute enumeration, ml-predict,
  fundamentals backfill).

**Decision**: introduce `gefion.universe` as the single gate; route modeling
consumers through it; leave observing-plane consumers (ingestion, quality,
feat-compute sweep, UI browsing helpers) unfiltered by design (FR-006).

**Sites routed in v1** (from the survey):
`ml/dataset.py:69+191`, `cli.py` ml-predict population (`1754-1774`),
`backtest/data_loader.py` (both selectors), `compute/cross_sectional.py:346`,
`features/dispatcher.py:1572` (market functions — SQL-composed filter),
`experiments/cycle_runner.py:1079` (experimental-feature backfill population),
regime discovery base list (`cli.py:13805`, `regimes/discovery/spa.py:262`) via
a new chain step, `regimes/discovery/signals.py:271` market mean,
`ml/e2e.py:323`, volatility compute (`cli.py:10101`).

**Sites deliberately NOT routed**: `cli.py:7038` feat-compute (compute
everything), `cli.py:7452` fundamentals backfill, `cli.py:8114` universe-ingest,
`quality/*` scans, `ui/components/database.py:get_symbols` (browsing, not
modeling).

## R2. Membership representation: exclusion intervals (complement form)

- Decision: materialize **exclusion intervals** — one row per (universe,
  symbol, rule, from-date, to-date-nullable). A symbol is a member as-of D iff
  no exclusion interval covers D. Membership itself needs no rows.
- Rationale: exclusions are the minority (~29% of symbols under the initial
  rules, most with a single open-ended interval), every row self-documents the
  *why* (rule name — FR-003), static rules produce exactly one open-ended row
  per excluded symbol (spec US3/AS2), and streaming-SQL consumers compose it as
  one `NOT EXISTS` subquery without materializing symbol lists.
- Alternatives considered: (a) member intervals — ~2.5× more rows, loses the
  "which rule" annotation on the common path, and "new symbol appears" would
  require a row before the symbol can be modeled (fail-closed in the wrong
  direction: absence of bookkeeping should not silently exclude — spec edge
  case 1); (b) per-symbol-per-day membership table — 40M+ rows/universe,
  rejected on size with no query benefit over intervals.

## R3. Rule evaluation

- Static attributes (`asset_type`, `industry`, `sector`, `exchange`, `status`):
  one SQL predicate over `stocks`, producing open-ended intervals starting at
  the symbol's first bar date.
- Time-varying attribute v1 = **`close` only** (true daily history exists in
  `stock_ohlcv`). Intervals via gaps-and-islands (LAG over predicate-true
  dates). `market_cap` et al. are **deferred** until fundamentals vintages
  accrue (separate backlog item): evaluating today's snapshot against history
  would be exactly the look-ahead bias US3 exists to prevent. The attribute
  surface is a declared registry (attribute → source + static/time-varying), so
  adding `market_cap` later is registry data + the vintage source, not a
  redesign.
- Operators: `eq, ne, in, gte, lte, between, is_missing` — validated against
  the registry at definition time; unknown attribute/op → refusal naming valid
  options (US2/AS3).
- Semantics: any exclude-rule match excludes (OR); pins beat rules;
  `is_missing` never fires implicitly (spec edge case 1).

## R4. Definition storage & fingerprint

- Decision: `universe_definitions` relational table mirroring
  `regime_definitions` (name, description, rules JSONB, pins JSONB,
  fingerprint, is_default, enabled, timestamps). Fingerprint = sha256 of
  canonical (sorted-keys) JSON of rules+pins. Export/import as YAML mirroring
  `regime_definitions_export/import`.
- Default universe `modeling_default` (two rules: industry = SHELL COMPANIES;
  asset_type = ETF) seeded idempotently by `db-init` (reference data, like
  sector function seeding). Reserved name **`all`** = no filtering, for control
  runs and SC-001's control test; it is not a table row.
- Constraint: at most one `is_default` (partial unique index).

## R5. Chokepoint API shape

- `universe_members(conn, name=None, as_of=None) -> list[str]` /
  `universe_member_ids(...)` — symbol-list consumers.
- `universe_exclusion_clause(universe_id, date_expr, data_id_expr) -> (sql,
  params)` — for the two streaming-SQL sites (`run_market_function`,
  discovery market mean) that cannot take symbol lists without changing their
  per-date aggregation shape.
- `explain_symbol(conn, name, symbol, as_of=None)` — SC-003.
- Resolution: explicit name wins → `all` bypasses → else default universe.
  Disabled/unknown universe → refusal listing valid names (edge case 5).
- Guard (FR-010): refresh compares new excluded fraction vs current; empty
  membership or shrink beyond guard threshold (default 25 percentage points in
  one refresh) → refuse, report, leave old intervals in place.

## R6. Provenance (no new columns on result tables)

- `ml_datasets.universe` JSONB already exists — gains
  `{"universe_name", "universe_fingerprint", "resolved_count"}` alongside the
  existing manifest keys.
- Models: `save_model_artifact` metadata passthrough + result dict carry the
  stamp from the dataset (same mechanism as `device` in #146);
  `ml_models.hyperparams` JSONB needs no change (stamp travels in artifact
  metadata + linked dataset).
- Experiments: `experiments.config` JSONB gains the stamp; discovery already
  stamps `search_space["universe_filter"]` (existing precedent) — extended to
  include name+fingerprint.

## R7. FR-013 recompute & re-verdict path

- After the gate lands in `run_market_function`, all derived market series
  recompute full history (existing `macro derive` machinery, forced
  recompute). Regime labels conditioned on recomputed series re-derive via
  existing `regime compute`. The admitted 013 signal
  (MACD-on-high-fin-breadth) re-checks via the existing SPA re-verdict
  machinery (010) — shells largely classify under Financial Services, so
  `sector_breadth_financial_services` is the most-shifted series.
- Recorded as a vintage change: prod runbook step in quickstart; observation
  entry on the operating ledger when executed.

## R8. Nightly integration & performance

- `gefion universe refresh` appended to the nightly chain after data-update /
  feat-compute, before `macro derive` (derived series must see fresh
  membership). SC-006 budget: static rules are one UPDATE-shaped statement;
  close-rule islands over 26y × 6.2k symbols is a single indexed scan —
  well under 5 min.
- Index: `(universe_id, data_id)` on exclusions; the `NOT EXISTS` adds a
  nested-loop probe per (symbol,date) group in market functions — bounded by
  the existing join cost. Verified via Tempo before/after (constitution IV).

## R9. Deletion door & observability

- `universe delete`: standard pattern — dry-run default, dependency
  enumeration (datasets/experiments/models whose provenance references the
  fingerprint), refusal when referenced, `--confirm` to execute; audit ledgers
  untouched. Mirrors `src/gefion/{ml,experiments,features}/deletion.py`.
- Spans: `universe.refresh`, `universe.evaluate_rule`, `universe.members`,
  with counts as attributes; child spans propagate parent context.
