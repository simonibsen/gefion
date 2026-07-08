# Feature Specification: Provider-Garbage Detection & Quarantine (Data Quality)

**Feature Branch**: `008-data-quality`
**Created**: 2026-07-08
**Status**: Draft
**Input**: User description: "Detect and quarantine provider-garbage data values (true trash) without losing genuinely degenerate real extremes…" (full text in git history; design agreed in owner discussion 2026-07-08 following issue #79)

## The problem in one paragraph

Gefion deliberately stores what providers say, verbatim (the 2026-07-07 / issue #79
decision — no silent mutation at ingest). The price of that honesty is that provider
trash — values that are definitionally impossible (Beta −503,341.44, observed live
on MDXH) or self-contradictory (DividendYield 1,000,000.0 on CTAA) — now stores
successfully and flows *silently* into cross-sectional features, ML datasets, and
fundamentals-based research. Meanwhile a second population looks superficially
similar but is genuinely real: a shell company reporting ROE −615% from near-zero
revenue is internally consistent and must stay usable. The system needs to tell
these apart with stated confidence, record its verdicts visibly, and keep trash out
of research **by default** — without ever rejecting, correcting, or deleting a
stored value.

## Core concepts

**Two populations, two treatments.**
- *Degenerate but real* — extreme values that are internally consistent (ratios
  exploding on near-zero denominators). Kept, unflagged (or at most *suspect*);
  robust statistics downstream handle them.
- *Provider trash* — values that violate the metric's definition or contradict
  independently trusted data. Stored verbatim, but verdicted **trash** and treated
  as missing by every research consumer unless explicitly opted in.

**The detection hierarchy** (decreasing confidence; only the top two can convict):
1. **Definitional impossibility** — each metric has a validity envelope derived
   from its *definition*, not from data (a beta is a bounded regression slope; a
   dividend yield is dividend/price). Envelopes are per-metric and deliberately
   loose where reality is loose (margins/ROE explode legitimately). Bounds live in
   a declarative validation catalog — configuration, never magic numbers in code.
2. **Cross-field contradiction** — recompute derivable metrics from independently
   trusted inputs (the price history is ground truth: dividend yield ≈ dividend per
   share / close; PE ≈ close / EPS; market cap ≈ shares × close). Wild disagreement
   is trash *by construction* — no statistics required.
3. **Temporal discontinuity** (corroboration only) — trash is episodic (beta 1.2 →
   −503,341 → 1.2 across weekly updates); degenerate reality is persistent.
4. **Cross-sectional outlierness** (suspect flag only, never a verdict) — robust
   distance from the universe median. Cannot distinguish trash from distress on
   its own and is never allowed to convict alone.

**Acting policy** (house style: verdicts are first-class and visible; nothing is
silently mutated):
- Ingest never rejects or NULLs a value.
- Every detection is recorded in a **data-quality findings ledger** — an audit
  record naming the entity, metric, date, rule violated, observed value,
  expected/recomputed value, and verdict tier.
- Research consumers treat trash-verdict values as **missing by default**, with an
  explicit opt-in to include them.
- `db-health` gains a `data_quality` section in the existing dimension-coverage /
  entity-integrity style, so a provider-side regression shows up as a loud trend.

**One system for all entity kinds.** The same catalog + ledger + db-health
machinery validates fundamentals (stocks) and macro series (a VIX ≤ 0 is trash) —
riding spec 007's declared entity model rather than growing a per-table one-off.

**The sibling concern, absorbed.** Much observed garbage comes from instruments
research should never see. This spec closes the standing universe-quality backlog
item: NASDAQ test tickers (ZVZZT, ZWZZT, ZXZZT, ZJZZT, …) are excluded from
research universes outright, and asset type / exchange (populated since
listing-meta) become first-class research-universe selectors so shells, warrants,
and units stop polluting cross-sectional peer groups.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Trash is convicted at write time and recorded (Priority: P1)

As the operator, when `fundamentals-update` (or any covered write path) stores a
provider value that is definitionally impossible or contradicts trusted data, the
value still lands verbatim — and a finding lands beside it in the data-quality
ledger, naming the rule, the observed value, the expected value, and the verdict.

**Why this priority**: this is the detection core; everything else consumes its
verdicts. The issue #79 quartet (ELOX, CTAA, KIDZ, MDXH) is a live, reproducible
proving case sitting in prod right now.

**Independent Test**: re-ingest the four issue-79 symbols → each garbage value
(Beta −503,341.44, DividendYield 1,000,000.0, …) stores verbatim AND produces a
trash finding; a seeded shell-company row (ROE −615%, internally consistent)
produces **no** trash finding.

**Acceptance Scenarios**:

1. **Given** the validation catalog declares beta's envelope, **When** a beta of
   −503,341.44 is written, **Then** the row stores verbatim and the ledger gains a
   finding (rule = definitional bound, observed = −503,341.44, verdict = trash).
2. **Given** price history for the symbol, **When** a dividend yield of 1,000,000
   is written while dividend-per-share / close ≈ 0.02, **Then** the ledger gains a
   cross-field-contradiction finding recording both numbers.
3. **Given** a shell company whose ROE of −615% is consistent with its own reported
   income and equity, **When** it is written, **Then** no trash finding is created.
4. **Given** the same value is re-ingested unchanged, **When** validation runs
   again, **Then** the ledger does not accumulate duplicate findings for the same
   (entity, metric, date, rule).
5. **Given** a metric absent from the catalog, **When** it is written, **Then** no
   definitional check applies (no false conviction from a missing rule) and the
   gap is visible via the catalog listing.

---

### User Story 2 - Research consumers exclude trash by default (Priority: P1)

As a researcher, when I build cross-sectional features, ML datasets, or
fundamentals-derived features, trash-verdict values are treated as missing — I do
not need to remember to filter them (no distributed vigilance). When I explicitly
opt in, I get the verbatim data and the output says so.

**Why this priority**: detection without default exclusion changes nothing —
the garbage still reaches research. This is the quarantine half of the feature.

**Independent Test**: build the same cross-sectional feature / dataset twice over a
universe containing a convicted value — once default (value absent, treated like
NULL), once with the explicit include flag (value present, output labeled).

**Acceptance Scenarios**:

1. **Given** a convicted beta for symbol X on date D, **When** cross-sectional
   rankings are computed for D, **Then** X's beta is treated as missing (X simply
   has no rank for that metric that day) and the universe median is unpolluted.
2. **Given** the same state, **When** an ML dataset is built covering D, **Then**
   the convicted value is absent from the exported features and the dataset
   manifest records that quality filtering was active.
3. **Given** the explicit opt-in flag, **When** the same dataset is built, **Then**
   the verbatim value is included and the manifest records the opt-in.
4. **Given** suspect-tier (not trash) findings, **When** consumers run with
   defaults, **Then** suspect values are NOT excluded (only trash is quarantined).

---

### User Story 3 - Research universes stop seeing junk instruments (Priority: P2)

As a researcher, my cross-sectional peer groups and discovery universes never
contain NASDAQ test tickers, and I can select universes by asset type and exchange
as first-class filters — shells, warrants, and units are excludable by
declaration, not by per-query vigilance.

**Why this priority**: prevention beats detection — a large share of observed
garbage rides in on instruments research should never see. Independent of US1/US2
and closes the standing universe-quality backlog item.

**Independent Test**: with test tickers present in `stocks`, build a
cross-sectional computation and a discovery universe → test tickers appear in
neither; an asset-type selector produces exactly the declared subset.

**Acceptance Scenarios**:

1. **Given** ZVZZT exists in the stocks table, **When** any research universe is
   assembled (cross-sectional compute, discovery filter chain, dataset build),
   **Then** ZVZZT is absent — always, without the caller asking.
2. **Given** asset types populated by listing-meta, **When** a universe declares
   `asset_type:common`, **Then** ETFs, warrants, and units are excluded, and the
   filter's name is recorded with the artifact (declared, auditable — 006 style).
3. **Given** an instrument with NULL asset type, **When** a quality-filtered
   universe is assembled, **Then** the instrument is excluded and the exclusion is
   countable (fail-closed, consistent with the discovery filter chain).

---

### User Story 4 - Quality is visible and operable (Priority: P2)

As the operator, I can see data-quality state at a glance (`db-health` gains a
`data_quality` section: flagged counts per metric, trend against prior runs), list
and inspect findings, and run an on-demand backfill that validates *already-stored*
history without modifying a single stored value.

**Why this priority**: verdicts nobody can see are as silent as no verdicts;
and prod already holds garbage written before this spec existed — it needs
flagging retroactively.

**Independent Test**: run the backfill against a database seeded with known
garbage → findings appear, stored values are byte-identical before/after;
`db-health` reports non-zero flagged counts for exactly the seeded metrics.

**Acceptance Scenarios**:

1. **Given** convicted findings exist, **When** `db-health` runs, **Then** its
   `data_quality` section reports flagged counts per metric with actionable
   warnings, in the same style as `dimension_coverage`/`entity_integrity`.
2. **Given** prod history containing pre-spec garbage, **When** the backfill runs,
   **Then** findings are created for it, re-running is idempotent, and no stored
   value changes.
3. **Given** the findings ledger, **When** the operator lists findings filtered by
   metric/symbol/verdict, **Then** each finding shows rule, observed, expected,
   and detection context.

---

### User Story 5 - Corroboration tiers and the macro family (Priority: P3)

As the system owner, temporal-discontinuity and cross-sectional-outlier checks
add *suspect* flags (never solo convictions), and the same catalog + ledger +
db-health machinery validates macro series (VIX ≤ 0 → trash) — proving the
quality system is one system, not a fundamentals one-off.

**Why this priority**: valuable but additive — the P1 tiers convict everything
observed in the wild so far; these tiers widen the net and prove generality.

**Independent Test**: a synthetic series with an episodic spike earns a suspect
finding (temporal rule) while a persistently extreme series earns none; a macro
value violating its catalog bounds earns a trash finding via the identical
machinery.

**Acceptance Scenarios**:

1. **Given** a metric whose history is (1.2, −503,341, 1.2), **When** temporal
   corroboration runs, **Then** the spike is flagged suspect (or corroborates an
   existing conviction) while a persistent (−6.1, −6.2, −6.0) history is not.
2. **Given** a robust cross-sectional outlier that passes all definitional and
   cross-field checks, **When** validation runs, **Then** at most a suspect
   finding is recorded — never a trash verdict from outlierness alone.
3. **Given** the catalog declares VIX > 0, **When** a macro value of −3 is
   ingested, **Then** a trash finding is recorded through the same ledger and
   surfaces in the same db-health section (SC-307's family proof).

---

### Edge Cases

- **No trusted comparator**: cross-field recompute needs price/shares; when the
  symbol has no price history for the date, that rule abstains (no verdict from
  absence of evidence) — definitional bounds still apply.
- **Sentinel values**: provider sentinels ('None', '-', '0'-as-missing) are already
  parsed to NULL upstream; NULL is missing, never trash.
- **Tier disagreement**: definitional bound passes but cross-field contradiction
  fails → trash (any convicting tier suffices); conviction records *which* rule.
- **Legitimate discontinuities**: splits/re-listings cause real temporal jumps —
  which is exactly why the temporal tier can only corroborate or mark suspect.
- **Catalog gaps**: an uncataloged metric gets no definitional check and that gap
  is enumerable (the operator can list unvalidated metrics) — no silent coverage
  illusion.
- **Ledger growth**: findings are per (entity, metric, date, rule) and idempotent
  on re-validation; the backfill cannot double the ledger by being run twice.
- **Deleting a flagged entity**: entity deletion (spec 007) never deletes its
  findings — the ledger is accounting, and issue #76's exception applies.
- **Universe fail-closed starvation**: if asset-type coverage regresses (e.g., a
  fresh exchange listing wave), quality-filtered universes shrink loudly (counts
  reported) rather than silently admitting unknowns.

## Requirements *(mandatory)*

### Functional Requirements

**Detection & catalog**

- **FR-301**: A declarative per-metric validation catalog MUST define, for each
  covered metric: its definitional envelope (bounds), an optional cross-field
  derivation (what recomputes it, from which trusted inputs, with what tolerance),
  and which entity kind it applies to. Adding or tuning a metric's rules MUST be a
  catalog edit, not a code change.
- **FR-302**: The write paths for fundamentals and macro series MUST validate each
  covered value at store time against the catalog: definitional bounds (tier 1)
  and cross-field contradiction against trusted stored data (tier 2). Violations
  of either tier produce a **trash** verdict.
- **FR-303**: Validation MUST never reject, mutate, or NULL a stored value, and
  MUST never block the write path on validation failure (a validation error is
  itself recorded, not raised into the ingest).
- **FR-304**: Temporal-discontinuity and cross-sectional-outlier checks MUST only
  produce **suspect** findings or corroborate an existing conviction — never a
  trash verdict alone.
- **FR-305**: An on-demand backfill command MUST validate already-stored history
  through the same catalog and ledger, idempotently, changing no stored values.

**Findings ledger**

- **FR-306**: Every detection MUST be recorded as a finding: entity kind + entity +
  metric + date + rule violated + observed value + expected/recomputed value (where
  applicable) + verdict tier + detection context (which run/command). Findings are
  unique per (entity, metric, date, rule) and idempotent under re-validation.
- **FR-307**: The findings ledger is an audit ledger: it MUST survive deletion of
  the flagged artifact (spec 007 `entity-delete` never touches it) and MUST never
  be rewritten — a value later deemed acceptable gets a superseding resolution
  record, not an erased finding.

**Consumers**

- **FR-308**: Cross-sectional feature computation, ML dataset builds, and
  fundamentals-derived feature computation MUST treat trash-verdict values as
  missing by default, and MUST record in their artifacts (manifest/metadata) that
  quality filtering was active.
- **FR-309**: Each consumer MUST offer an explicit opt-in to include convicted
  values; opting in MUST be recorded in the artifact.
- **FR-310**: Suspect-tier findings MUST NOT cause default exclusion.

**Research universes**

- **FR-311**: NASDAQ test tickers MUST be excluded from every research universe
  (cross-sectional peer groups, discovery universe chains, dataset builds)
  unconditionally.
- **FR-312**: Asset type and exchange MUST be usable as declared universe
  selectors; quality-filtered universes MUST fail closed on unknown asset type,
  with exclusion counts reported (006 filter-chain style).

**Visibility & operations**

- **FR-313**: `db-health` MUST gain a `data_quality` section: flagged counts per
  metric and verdict tier, in the dimension-coverage style, with actionable
  warnings when counts rise.
- **FR-314**: The operator MUST be able to list/inspect findings (filter by
  metric, entity, verdict, date range) and list the catalog including uncataloged
  (unvalidated) metrics.

**Interfaces & definition of done** (owner directive; enforced by docs-drift tests)

- **FR-315**: All new operations land with CLI + MCP surfaces in the same
  increment; docs (README, USER_GUIDE, MCP_WORKFLOWS) and learning materials
  (curriculum module covering the two-populations concept and the hierarchy) in
  the same increment; `/gefion` operator-skill routing updated.
- **FR-316**: New tables require owner-approved DDL, declare their layer and feeds
  edges (007 governance), and carry a deliberate deletion story: the findings
  ledger is explicitly *never* cascade-deleted.
- **FR-317**: The validation pass MUST be observable (spans with counts), and MUST
  NOT materially slow the write paths it rides in (validation is a bounded
  per-batch pass, not a full-table scan).

### Key Entities

- **Validation catalog**: per-metric rules — entity kind, metric name, definitional
  envelope, optional derivation (inputs + tolerance), notes. Repo-versioned
  configuration; the authoritative list of what is and is not validated.
- **Data-quality finding**: one detection — (entity kind, entity, metric, date),
  rule, observed, expected, verdict tier (trash | suspect), context, created-at.
  Append-only audit record with optional superseding resolution.
- **Verdict tier**: trash (convicted: definitional or cross-field) vs suspect
  (corroboration tiers); only trash affects consumers by default.
- **Quality-filtered universe**: a declared, named instrument filter (test-ticker
  exclusion + asset-type/exchange selectors) recorded with any artifact built
  from it.

## Automation *(consider)*

- **Proposed skill**: None needed — `/gefion` operator-skill routing gains lines
  for the findings/backfill/catalog operations (FR-315); no new workflow shape
  justifies a dedicated skill.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-301**: The four issue-79 observed garbage values (e.g., Beta −503,341.44,
  DividendYield 1,000,000.0) each receive a trash finding naming rule, observed,
  and expected values — while a seeded internally-consistent shell-company row
  (ROE −615%) receives none. Zero false convictions across the seeded
  degenerate-but-real set.
- **SC-302**: A cross-sectional computation and an ML dataset built over a
  universe containing convicted values exclude them by default (outputs match a
  world where those values were never written), and include them under explicit
  opt-in with the choice recorded in the artifact.
- **SC-303**: Injecting a synthetic garbage batch raises `db-health`'s
  `data_quality` flagged counts for exactly the affected metrics; a clean database
  reports zeros.
- **SC-304**: After universe hardening, NASDAQ test tickers appear in zero
  research universes (verified across cross-sectional compute, discovery chain,
  dataset build), and an `asset_type:common` universe contains only common stock.
- **SC-305**: The backfill over full prod history completes, produces findings for
  pre-spec garbage, is idempotent on re-run, and leaves every stored value
  byte-identical.
- **SC-306** (family test): covering a brand-new metric — bounds and/or a
  derivation — is a catalog edit plus re-validation, with zero code or schema
  changes.
- **SC-307** (one-system proof): a macro-series value violating its catalog bounds
  (VIX ≤ 0) produces a finding through the identical ledger and db-health section
  as fundamentals.

## Assumptions

- **Tolerance for cross-field contradiction**: recomputed values legitimately
  drift from reported ones (reporting lags, adjusted vs unadjusted prices), so
  tier-2 convicts only on order-of-magnitude disagreement (default: observed vs
  recomputed differing by more than 10×), per-metric overridable in the catalog.
- **Initial catalog scope**: the twelve fundamentals ratio columns plus market cap
  and shares outstanding (via cross-field), and VIX for macro — the metrics with
  observed or definitionally obvious failure modes. Coverage grows by catalog
  edits (SC-306).
- **Suspect flags are informational in v1**: surfaced in ledger and db-health,
  never excluded by default, no consumer flag to exclude them yet (add later if
  suspects prove predictive of provider faults).
- **Findings live with system state**: the ledger is queryable alongside the data
  it describes; retention is indefinite (audit record, like discovery ledgers).
- **Test-ticker identification**: NASDAQ's published test symbols plus the
  documented Z-prefix test family; the list is configuration, not code.
- **Existing sentinel parsing stands**: 'None'/'-' → NULL handling in providers
  is unchanged; this spec only judges values that were parsed as numbers.

## Out of Scope

- Fixing or working around the provider (garbage in is expected; silent garbage
  through is the defect).
- Automatic correction or imputation of flagged values.
- Deleting or rewriting historical garbage — flag it, don't rewrite history.
- Real-time/streaming validation outside the covered write paths.
- Quarantining *suspect*-tier values from consumers (v1 surfaces them only).
- OHLCV price-series validation (its own problem class — splits, halts — and its
  own future spec if needed).
