# Research — Provider-Garbage Detection & Quarantine (008)

Decisions resolving the plan's open questions. No NEEDS CLARIFICATION items
remain.

## R1 — Findings storage: one plain-relational audit ledger

**Decision**: a single `data_quality_findings` table (proposed DDL in
contracts/sql.md): entity axis columns (`entity_table` TEXT + `entity_id` INT —
007's declared model, deliberately no hard FK), metric, date, rule, verdict
(`trash`/`suspect`), observed/expected as DOUBLE PRECISION, JSONB detail, context,
created_at, and nullable resolution fields. UNIQUE on (entity_table, entity_id,
metric, date, rule) gives idempotence by construction.

**Rationale**: findings are sparse (hundreds on today's prod), so no hypertable
(006 ledger precedent). DOUBLE PRECISION for observed/expected because garbage is
by definition unbounded — a NUMERIC-width quality ledger overflowing on the very
values it convicts would be ironic (the #79 lesson applied to ourselves). No FK on
entity_id: the ledger must survive `entity-delete` (FR-307, issue #76's exception),
and 007 already made declared identity the norm.

**Alternatives considered**: quality-flag columns on the data tables (rejected —
schema churn per table, no room for rule/expected/context, violates
one-system-for-all-entity-kinds); a findings hypertable (rejected — cardinality
nowhere near justifying chunks); separate resolution table (rejected — YAGNI;
nullable resolved_at/resolution on the finding keeps detection facts immutable
while allowing a later verdict amendment, Constitution VI).

## R2 — Catalog format: YAML at `data-quality/catalog.yaml`

**Decision**: one repo-versioned YAML file. Per metric: `entity_table`, the
`table`/`column` it validates, optional `bounds {min, max}`, optional `derivation
{expression inputs, tolerance_factor}`, optional per-metric overrides for
corroboration tiers, and free-text `why` (the definitional argument for the
bounds). A `defaults:` block carries global parameters (tolerance_factor 10,
temporal spike factor, robust-z threshold). The test-ticker list lives in the same
file under `universe:`.

**Rationale**: matches the principles-catalog precedent (YAML in repo, PyYAML
already a dependency); SC-306's family test is literally "add a YAML stanza". The
`why` field makes every bound reviewable — no magic numbers, each envelope carries
its definitional justification.

**Alternatives considered**: DB table for rules (rejected — rules are code-review
artifacts, want diffs and PR review; DB-first applies to *feature logic*, and the
constitution's own carve-out treats catalogs-as-configuration like the principles
catalog); per-metric Python validators (rejected — that's code, fails SC-306).

## R3 — Consumer exclusion mechanism: shared filter helper, applied per consumer

**Decision**: `gefion.quality` exposes one helper that, given a connection and the
target (table/metric set), returns the trash-convicted (entity_id, metric, date)
exclusion set (or an SQL fragment to anti-join). Consumers apply it where they
read: cross-sectional compute masks convicted inputs to NULL before ranking;
`ml dataset-build` masks before export and records `quality_filtering: true` (or
the opt-in) in the manifest; fundamentals-derived feature computation
(forward_fill_quarterly class) skips convicted source values. `--include-flagged`
is the uniform opt-in flag name.

**Rationale**: the metric→(table, column) mapping already lives in the catalog, so
one helper keeps the join logic in one place (no distributed vigilance — the exact
failure mode this spec kills). Masking to NULL reuses every consumer's existing
missing-data path; no consumer grows new semantics.

**Alternatives considered**: a filtered VIEW layer (rejected — the owner rejected
view indirection in 007 for good reason: fix the model, not the lens); deleting
convicted rows (out of scope by spec: flag, don't rewrite history).

**Amendment (US2 implementation, 2026-07-08 — owner-approved "chokepoint"
approach)**: implementation found that `stocks_fundamentals` — the table holding
every convicted value — is *consumer-less today* (007's own feeds graph flagged
it): no feature definition reads it, and cross-sectional compute / dataset build
read `computed_features`, which the fundamentals garbage never reaches. So the
per-downstream-consumer exclusion the spec described would, for fundamentals,
protect nothing real yet. Exclusion therefore moved UP to the single
feature-computation chokepoint: the dispatcher's generic-table source fetch
(`_fetch_from_generic_table`) drops convicted `(data_id, column, date)` values,
and macro materialization excludes convicted series values before they enter
`computed_features`. This keeps every current AND future consumer clean in one
place (the "no distributed vigilance" principle), and the one live convicted-data
path today — `macro_vix` reading `macro_series_values.value` — is protected
end-to-end. `--include-flagged` opts a macro series back in. Downstream consumers
inherit a clean feature store rather than each re-implementing the filter.

## R4 — Cross-field tolerance semantics

**Decision**: tier 2 convicts when observed and recomputed disagree by more than a
factor (default 10×): `max(|obs|,|rec|) / max(min(|obs|,|rec|), eps) >
tolerance_factor`, with sign disagreement on magnitudes both above a floor also
convicting. Recompute inputs come from already-stored trusted data (latest close ≤
date from `stock_ohlcv`; shares outstanding from the same payload). When an input
is missing, the rule **abstains** (edge case: no verdict from absence of evidence).

**Rationale**: reported vs recomputed values legitimately drift (reporting lags,
adjusted prices) — order-of-magnitude disagreement is where drift ends and
contradiction begins. Ratio-of-magnitudes is scale-free, so one default works
across metrics; the catalog can override per metric.

**Alternatives considered**: absolute difference thresholds (rejected — not
scale-free); percentage bands (rejected — too tight for legitimately noisy
metrics, would convict real data).

## R5 — Temporal-discontinuity rule (suspect only)

**Decision**: a value is a *spike* when its magnitude exceeds `spike_factor`
(default 100×) times the larger of its neighbors' magnitudes AND the series
reverts (the next observation returns to the prior regime). Produces a suspect
finding, or corroboration detail on an existing conviction. Runs where history is
already at hand: the backfill, and the write path when the prior row is fetched
anyway.

**Rationale**: episodic-vs-persistent is the whole signal (beta 1.2 → −503,341 →
1.2 vs a shell's steady −6); requiring reversion keeps splits/re-listings (level
shifts, not spikes) out. Suspect-only by spec (FR-304).

**Alternatives considered**: rolling z-score on the series (rejected — needs
longer history than weekly fundamentals reliably have; more parameters, no more
signal for this failure mode).

## R6 — Cross-sectional outlier rule (suspect only)

**Decision**: robust z = |v − median| / (1.4826 × MAD) over the same-date universe
for the metric; z > threshold (default 10) → suspect. Runs in the backfill and on
write batches large enough to form a cross-section (fundamentals-update processes
whole universes; single-symbol writes skip the tier).

**Rationale**: median/MAD is the 005 lesson (robust to the very outliers being
hunted). Threshold 10 is deliberately extreme — this tier exists to catch what the
convicting tiers miss, not to flag distressed companies weekly.

**Alternatives considered**: IQR fences (rejected — MAD is already the house
robust-scale idiom); letting high z convict (rejected by spec — cannot distinguish
trash from distress).

## R7 — Universe hardening: shared helper, 006 filter vocabulary

**Decision**: `gefion.quality.universe` provides the quality-universe SQL
(exclude test tickers unconditionally; `asset_type:common` / `exchange:<X>`
selectors; fail-closed on NULL asset_type with exclusion counts). The discovery
filter chain keeps its own declared-filter implementation (it already has
`test_tickers` and `asset_type:common` — 006) but both draw the ticker list and
selector definitions from the catalog's `universe:` block so there is exactly one
source of truth. Cross-sectional compute and `ml dataset-build` adopt the helper.

**Rationale**: discovery already solved declared universe filtering; the gap is
that cross-sectional peer groups and dataset builds don't use it. Sharing the
*definitions* (not necessarily the code path) keeps one truth without a risky
refactor of 006's audited chain.

**Alternatives considered**: DELETE test tickers from `stocks` (rejected —
rewriting history, and listing ingests would re-create them); a `research_grade`
boolean on stocks (rejected — hides the *why*; declared filters are auditable).

## R8 — Surfaces: `gefion quality` group + db-health section

**Decision**: CLI group `gefion quality` with `findings` (list/filter),
`catalog` (covered + uncovered metrics), `backfill` (on-demand history
validation); MCP `quality_findings`, `quality_catalog`, `quality_backfill`
(mutating — ledger only). `db-health` gains `data_quality`: per-metric flagged
counts by verdict, with warnings on nonzero trash counts. UI parity rides the
existing db-health surfaces (System/Data views) — no new page in v1.

**Rationale**: matches 007's shape (db-health section + a small command group);
db-health is already the operator's one-stop health view in all three interfaces.

**Alternatives considered**: folding findings under `gefion data` (rejected —
quality is cross-entity and will grow; its own group keeps routing clean).

## R9 — Write-path integration points

**Decision**: validation is a bounded post-batch pass inside the two covered
write paths: `_write_fundamentals_results` (per batch of fetched symbols) and
`macro.ingest.upsert_values`/`ingest_series` (per ingested series). It receives
the just-written values in memory (no re-read), evaluates tiers 1–2 (+cheap
corroboration when inputs are at hand), and upserts findings. Errors inside
validation are caught, counted, and reported in the command summary — never
raised into the write (FR-303).

**Rationale**: rides existing paths (no scheduler, no trigger — v1 scope);
in-memory batch evaluation keeps overhead within the <5% target; the backfill
covers everything written before this spec or outside these paths.

**Alternatives considered**: DB trigger validation (rejected — logic in the DB,
hard to test/version, and 007 already chose application-level integrity);
a periodic scan job (rejected for v1 — the backfill command covers the need
on demand; cron can call it later if wanted).
