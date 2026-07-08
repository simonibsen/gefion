# Feature Specification: First-Class Entities for the Feature Store — Declared Identity, Feeds, and Deletion

**Feature Branch**: `007-entity-model`
**Created**: 2026-07-08
**Status**: Draft
**Depends on**: spec 006 (discovery consumes the feature store; its diagnostics ledger motivated this)
**Input**: User description: "Generalize the feature store's entity model so non-equity series (VIX, macro data) are first-class instead of shoehorned into the stocks table… the pair (feature_definitions.entity_table, computed_features.data_id) resolves entity identity per feature… the registry becomes load-bearing for identity, the feeds graph, AND deletion… VIX ingestion is the proving case."

## Motivation & Problem *(mandatory context)*

Every value in the feature store belongs to an entity, and today that entity is
hard-wired to be a stock: the feature store's rows point at the stocks table and
nothing else. The moment a non-equity series is needed — and the system is already
asking for one: the `regime-detection-hmm` principle declares a VIX requirement, and
discovery's diagnostics ledger records it as an uncomputable proposal on every run —
there are only bad choices:

- **Masquerade** the series as a stock (a `^VIX` pseudo-symbol). Cost: every bulk
  pipeline that iterates stocks must *remember* to exclude it — price updates would
  burn API errors on it, indicator computation would pollute cross-sectional medians
  with it, backtest universes could trade it. That is distributed vigilance: N
  scattered exclusion rules, each a future silent bug. Two such silent gaps were
  found and fixed on production this week (NULL dimension metadata; numeric overflow
  in unconsumed columns) — this failure shape is not hypothetical here.
- **Fork** the feature store (a separate macro-features table). Cost: the funnel
  splits, and everything downstream (discovery availability checks, dataset builds,
  market-series loading) must learn two paths.

The clean observation, from design review: the relationship between a feature's
values and its entity is *already declared per-feature in the registry*
(`feature_definitions`) — the generic name `data_id` reflects that original intent.
This spec makes that declaration load-bearing: identity resolution, the feeds graph,
and deletion all flow from the registry, and the hard one-table foreign key is
retired in favor of a **declared** logical key. Integrity moves from
impossible-by-constraint to detectable-by-health-check — a trade accepted because
feature-store writes flow through a single reviewed dispatcher path, and because the
detection-layer pattern (db-health coverage warnings) proved itself on production
twice this week.

A second principle rides along (owner directive, 2026-07-08): **deletions are
first-class** — anything created must be cleanly deletable together with its
associated data. The registry-driven entity model is what makes a uniform,
dependency-aware delete possible across entity kinds, replacing a cascade that only
ever covered the stocks case.

> **Visual design record**: [design-options.md](./design-options.md) — the problem
> and all three options as Mermaid diagrams (preserved from the interactive review
> artifact), plus the row-vs-table decision rule for future entity kinds.

## Clarifications

### Session 2026-07-08 (design review, pre-spec)

- Q: Views as the containment mechanism (a `tradable_stocks` chokepoint)? → A:
  **Rejected by owner.** Views treat the symptom (non-equities in the stocks table)
  rather than the cause (the feature store's entity model). The declared
  entity-table model removes the need: non-equities never enter equity pipelines
  because they never enter the stocks table.
- Q: Is `source_table` the entity link? → A: **No — two axes.** `source_table`
  declares what a computation *reads*; the new `entity_table` declares who the value
  *belongs to*. Today they coincidentally collapse (entity is always a stock even
  when the source is `quarterly_financials`); the spec keeps them separate.
- Q: Are the integrity/cascade costs worth it? → A: **Yes, conditional on the macro
  family being real** — and the principles catalog (`macro.vix`,
  `macro.spy_returns`), the unused CPI parser, and the diagnostics ledger's standing
  request all say it is. The reversal condition (macro data out of scope for a
  year-plus) was considered and rejected.
- Q: Is this VIX-specific? → A: **No — VIX-heavy narrative, VIX-free architecture.**
  The mechanism is "any declared entity table" (US1/FR-201/202 never mention VIX);
  VIX is only the proving case because a real consumer beats a synthetic one. SC-207
  (the family test) guards against VIX-isms leaking into the implementation, and the
  model extends past macro entirely — a future *sectors* entity table (005's
  deferred sector-scope regimes) or benchmark/portfolio entities ride the same rail.
  One shape decision is deliberately left to planning: the raw values table must
  serve both daily-OHLC (VIX) and monthly-single-value (CPI) cadences.
- Q: Why the name `macro_series`? → A: It matches vocabulary the system already
  declared — the principles catalog namespaces these requirements `macro.vix` /
  `macro.spy_returns`, and diagnostics report the gap under that name. "Macro" is
  quant shorthand for market/economy-wide series as opposed to per-security data;
  alternatives (`market_series`, `external_series`) were considered and rejected as
  more ambiguous. Final say rests with DDL approval. It houses market-level time
  series specifically — future non-series entity kinds (sectors, benchmarks) get
  their own entity tables, not residence here.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Declared Entity Identity (Priority: P1)

The feature registry declares, per feature, which entity table its values belong to.
Existing features declare the stocks table (no behavior change); a feature may
declare a different entity table, and the feature store accepts and serves its
values without those entities existing in — or affecting — the stocks table.

**Why this priority**: This is the load-bearing change; everything else composes on
it. It is independently valuable even before any macro series exists: it makes the
implicit entity model explicit and auditable.

**Independent Test**: Register a feature declaring a non-stock entity table, write
values for an entity id from that table, and verify (a) the values are served to
consumers, (b) nothing about equity pipelines (price updates, indicator computation,
cross-sectional ranks, backtest universes) sees or is affected by the new entity.

**Acceptance Scenarios**:

1. **Given** the migrated schema, **When** existing features are inspected, **Then**
   every one declares the stocks entity table and all existing behavior is unchanged
   (full regression suite passes untouched).
2. **Given** a feature declaring entity table X, **When** its values are written for
   an id that exists in X, **Then** they are stored and served like any feature.
3. **Given** a non-stock entity, **When** any equity bulk pipeline runs, **Then** the
   entity is structurally invisible to it — no exclusion clause anywhere is needed.

---

### User Story 2 - Macro Series Home + VIX Proving Case (Priority: P1)

A macro-series catalog (name, provider, kind, cadence) and its raw daily values
become the first non-stock entity tables. VIX is ingested end-to-end: fetched from
the existing market-data provider's index endpoint (premium — verified with one live
call before building; a free fallback provider is named if the plan lacks it),
cataloged, materialized into the feature store as `macro_vix`, and immediately
usable by discovery atoms, regime expressions, and interaction tests.

**Why this priority**: The proving case. The diagnostics ledger has been asking for
VIX since the first production discovery run; landing it proves the entity model
with a real consumer, not a synthetic one.

**Independent Test**: After ingestion, `regime-detection-hmm`-seeded discovery stops
recording VIX as an uncomputable proposal and instead proposes real VIX candidates;
`regime interaction --by macro_vix` answers; the stocks table contains no index row.

**Acceptance Scenarios**:

1. **Given** the provider's index endpoint (verified live), **When** VIX ingestion
   runs, **Then** decades of daily values land in the macro values table and the
   `macro_vix` feature materializes into the feature store, keyed to the catalog
   entity.
2. **Given** the ingested series, **When** a discovery atom or regime expression
   references `macro_vix`, **Then** it resolves like any market-level feature (a
   single-entity series: the market-level aggregate is the value itself).
3. **Given** the ingestion, **When** stocks-table consumers are audited, **Then**
   zero changes were required anywhere in equity pipelines.

---

### User Story 3 - The Registry as the Feeds Graph (Priority: P2)

The generated data dictionary renders the data-flow graph from the registry: every
raw table listed with the features that consume it (source edges) and the entities
it identifies (entity edges), with raw tables that feed nothing flagged loudly. The
add-a-table checklist requires new tables to declare their layer, naming-prefix
taxonomy compliance, feeder edges, and a deletion story with deliberate delete
behavior — at DDL-approval time.

**Why this priority**: This is the sprawl control the whole discussion was about:
"what feeds what" becomes a generated artifact instead of an archaeology exercise
(the `stocks_fundamentals` question this week took a code grep plus a database query
to answer; it should be a glance).

**Independent Test**: Regenerate the dictionary and verify every raw table shows its
consumers; verify a table with no declared consumers is flagged; verify the
checklist additions are in the development guide and referenced by the DDL-approval
flow.

**Acceptance Scenarios**:

1. **Given** the current schema, **When** the dictionary is generated, **Then** the
   feeds section shows (at minimum) prices→features, quarterly-financials→features,
   and flags `stocks_fundamentals` as having no feature consumers.
2. **Given** a proposed new table, **When** it reaches DDL approval, **Then** the
   checklist demands layer, prefix, feeder edges, and deletion story.

---

### User Story 4 - Integrity Is Detectable (Priority: P2)

With the hard constraint retired, orphaned feature values (an id with no home in its
declared entity table) must be *loudly detectable*: the database health check gains
an orphan scan per declared entity table, in the same actionable-warning style as
the dimension-coverage check.

**Why this priority**: This is the honest price of the model — pay it visibly.
Without it, the integrity trade-off would be silent erosion.

**Independent Test**: Manufacture an orphan row in a test database; the health check
reports it with the entity table, count, and an actionable message; a clean database
reports zero orphans.

**Acceptance Scenarios**:

1. **Given** a feature value whose id has no row in its declared entity table,
   **When** the health check runs, **Then** it warns with entity table, orphan
   count, and remediation guidance.
2. **Given** a healthy database, **When** the health check runs, **Then** the orphan
   section reports clean (and the check adds negligible runtime).

---

### User Story 5 - Registry-Driven Deletion (Priority: P2)

Deleting an entity (a stock, a macro series) cleanly removes its feature-store
values through the registry — uniformly across entity kinds — with a dry-run-first,
confirm-to-execute command shape. This replaces the retired cascade (which only ever
covered stocks) and implements the first-class-deletion principle for the feature
store.

**Why this priority**: Owner principle; also the direct replacement for capability
lost by retiring the hard constraint, so it should land in the same feature.

**Independent Test**: Delete a test entity via the command; dry-run reports exactly
what would be removed (values per feature); confirm removes values then the entity
row; nothing else is touched; an entity with no values deletes trivially.

**Acceptance Scenarios**:

1. **Given** an entity with feature values, **When** delete runs without
   confirmation, **Then** it reports the full impact and changes nothing.
2. **Given** confirmation, **When** delete runs, **Then** values are removed for
   every feature declaring that entity table, then the entity row, in that order.
3. **Given** the stocks entity table, **When** a stock is deleted via the command,
   **Then** behavior is equivalent to the old cascade (no regression in cleanup).

---

### Edge Cases

- **A feature declaring an entity table that does not exist**: refused at
  registration time (the registry validates the declaration), recorded honestly —
  never a runtime surprise.
- **Single-entity series and market-level aggregation**: market-level loaders
  currently take a cross-sectional median across entities; for a one-entity series
  the aggregate must degenerate to the value itself, and mixed-entity aggregation
  across different entity tables must be impossible by construction.
- **Cross-sectional peer groups**: ranks/percentiles are peer-relative among stocks;
  non-stock entities must never enter peer groups (structurally, not by exclusion
  list).
- **Orphan creation window**: between retiring the constraint and the health check
  landing, orphans are undetectable — the two must ship in the same increment.
- **Entity id collision across tables**: ids are only meaningful *with* their
  declared entity table; any consumer joining by bare id without consulting the
  registry is a defect this spec must flush out (audit of existing joins is in
  scope).
- **Premium endpoint not actually available**: the VIX fetch is verified with one
  live call before any building; if the plan lacks index data, the named free
  fallback provider is used — the entity model is identical either way.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-201**: The feature registry MUST declare, per feature, the entity table its
  values belong to; existing features MUST default to the stocks table with zero
  behavior change (additive migration only — no data moves, no renames).
- **FR-202**: The feature store MUST accept and serve values for any declared entity
  table; the hard single-table foreign key MUST be retired in the same change.
- **FR-203**: `source_table` (what a computation reads) and `entity_table` (who the
  value belongs to) MUST remain independent declarations.
- **FR-204**: Equity bulk pipelines (price updates, indicator computation,
  cross-sectional ranks, backtest/universe selection) MUST be structurally unable to
  see non-stock entities — no per-pipeline exclusion rules anywhere.
- **FR-205**: A macro-series catalog and raw-values table MUST exist as the first
  non-stock entity (name, provider, kind, cadence; values keyed by series and date),
  following the naming-prefix taxonomy. All DDL requires owner approval
  (propose-don't-execute).
- **FR-206**: VIX MUST be ingested end-to-end as the proving case: provider's index
  endpoint verified with one live call first (named free fallback otherwise),
  catalog entry, raw daily values, `macro_vix` feature materialized into the store.
- **FR-207**: After VIX lands, discovery MUST stop recording VIX as an uncomputable
  proposal; `macro_vix` MUST be usable in discovery atoms, regime expressions, and
  interaction tests with no changes to equity pipelines.
- **FR-208**: Market-level series loading MUST resolve per the declared entity
  table; a single-entity series' market-level value is the value itself; aggregation
  across different entity tables is prohibited by construction.
- **FR-209**: The generated data dictionary MUST render the feeds graph from the
  registry (source and entity edges per raw table) and flag raw tables with no
  declared consumers.
- **FR-209a**: The generated data dictionary MUST include an entity-relationship
  diagram (Mermaid, rendered natively on the code host), generated — never
  hand-maintained — from the schema plus the registry: database-enforced
  relationships drawn solid, registry-declared logical relationships (entity/source
  edges) drawn dashed, grouped by the layer taxonomy so each sub-diagram stays
  legible. The solid-vs-dashed distinction is the entity model made visible.
- **FR-210**: The add-a-table checklist MUST require, at DDL-approval time: declared
  layer, naming-prefix compliance, feeder edges, and a deletion story with
  deliberate delete behavior — plus, for entity tables, the **row-vs-table decision
  rule** (see design-options.md): a new *instance* of an existing kind is a catalog
  row; a new *kind* (own attributes/relationships, or a meaningful peer group for
  within-kind aggregation) is a new entity table. When in doubt, start as a row and
  promote later.
- **FR-211**: The database health check MUST scan for orphaned feature values per
  declared entity table and warn actionably (entity table, count, remediation);
  this MUST land in the same increment as FR-202.
- **FR-212**: Entity deletion MUST be a first-class command: dry-run by default
  reporting full impact, confirm-to-execute, removing feature values (per registry)
  before the entity row, uniform across entity kinds; deleting a stock via this
  path MUST be at least as complete as the retired cascade.
- **FR-213**: All new/changed operations MUST be reachable per the house
  three-surface rule where user-facing (CLI first; MCP mirror; UI where a page
  exists), with docs updated in the same increment and drift checks green.
- **FR-214**: New modules MUST emit observability spans with parent-context
  propagation.

### Key Entities

- **Feature registry entry**: gains the declared entity table — the axis that
  resolves identity. Existing axes (name, source table/column, params, lifecycle)
  unchanged.
- **Macro series (catalog)**: a named non-equity series — name, provider, kind
  (index/rate/breadth/…), cadence. The entity rows that macro feature values point
  at.
- **Macro series values (raw)**: the L1 time series — one row per (series, date),
  the source a macro feature definition reads.
- **Feature value**: unchanged shape; its identity is now resolved via (feature →
  declared entity table, id) instead of a hard-wired single table.
- **Feeds graph**: the generated rendering of registry declarations — which raw
  tables feed which features, which entity tables anchor them, and which raw tables
  feed nothing.

## Documentation Impact *(mandatory — definition of done)*

- **docs/ARCHITECTURE.md** — the data-layering model (dimension / raw / feature
  store / consumers), the declared-entity resolution rule, and the funnel rule
  ("nothing skips a layer").
- **docs/DEVELOPMENT.md** — add-a-table checklist additions (layer, prefix, feeder
  edges, deletion story); the "add a data source" recipe (VIX as the worked
  example — no such recipe exists today).
- **docs/DATA_DICTIONARY.md** — regenerated with the feeds-graph section, layer
  grouping, and the generated Mermaid ERD (FR-209a).
- **README.md / docs/USER_GUIDE.md** — new commands (macro ingestion, entity
  delete) in the CLI reference.
- **docs/MCP_WORKFLOWS.md** — mirrored tools for any new user-facing operations.
- **.claude/commands/gefion-learn.md** — Module 1 (data layer) gains the
  entity-model and feeds-graph concepts, **including the row-vs-table decision rule
  and the peer-group litmus** (owner directive: this must reach the curriculum);
  the "add a data source" path referenced. Checkpoint candidate: learner explains
  why CPI is a `macro_series` row but sectors would be their own entity table.
- **tests/test_docs_drift.py** — passes for all new commands/tools.

## Automation *(consider)*

- **Proposed skill**: None needed. Macro ingestion joins the existing scheduled
  metadata-maintenance crontab pattern (documented in docs/DEPLOYMENT.md) rather
  than a new skill.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-201**: The full regression suite passes with zero equity-pipeline changes
  after the entity-model migration (behavioral no-op for stocks).
- **SC-202**: VIX is usable by discovery/regimes/interaction tests with **zero**
  edits to any equity bulk pipeline, and the stocks table contains no index row.
- **SC-203**: Discovery runs seeded from VIX-requiring principles record **zero**
  uncomputable-VIX diagnostics after ingestion (previously: every run).
- **SC-204**: The generated dictionary answers "what consumes table X" for 100% of
  raw tables, and correctly flags `stocks_fundamentals` as consumer-less today.
- **SC-204a**: The generated ERD renders on the code host without manual steps,
  covers 100% of tables grouped by layer, and visibly distinguishes
  database-enforced from registry-declared relationships; regenerating after any
  schema change requires zero hand-editing.
- **SC-205**: A manufactured orphan is reported by the health check within one run,
  with entity table and count; clean databases report zero.
- **SC-206**: Entity deletion via the command removes 100% of the entity's feature
  values (verified by count before/after) and is refused-without-impact in dry-run;
  stock deletion is at least as complete as the retired cascade.
- **SC-207**: Adding the *second* macro series (post-VIX) requires: one catalog row,
  one ingest configuration, one feature definition — and zero schema or pipeline
  changes (the "family test" that motivated the design).
