# Feature Specification: Sector-State Signals for Discovery

**Feature Branch**: `013-sector-signals`
**Created**: 2026-07-13
**Status**: Draft
**Follow-on to**: #86 / spec 005 (per-entity labels shipped in PR #101); enabler
for the sector-scoped hunt (session queue)
**Input**: Owner directive — sector-conditioned discovery. Grounding verified
2026-07-13 on prod: `stocks.sector` is 80.4% populated across 15 sectors
(FINANCIAL SERVICES 984, HEALTHCARE 935, TECHNOLOGY 605, INDUSTRIALS 370,
CONSUMER CYCLICAL 312, COMMUNICATION SERVICES 196 lead); per-entity
sector-scope regime LABELS already compute, but discovery atoms consume named
FEATURE SERIES — the missing bridge is sector-level series as first-class
features.

## Why (context)

Every discovery hunt so far has conditioned on whole-market states (trend
strength, breadth, dispersion) — the market described as one number per day.
The exploitation arc's lesson was that the binding constraint on discovery is
new *kinds* of information, not more machinery. Sector states are the nearest
untapped dimension: "technology is trending while energy churns" is a real,
persistent market condition that no market-wide median can express. This
feature turns sector-level aggregates into ordinary named series so hunts can
pre-register them as conditioning atoms with zero changes to any discovery
guarantee.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sector aggregate series exist as first-class features (Priority: P1)

An operator computes per-sector daily series from stored per-stock data: at
minimum, each sector's relative strength (sector median 20-day return minus
market median 20-day return) and each sector's internal breadth (% of members
above their own 200-day average). Each series is stored and named with its
sector, refreshes incrementally, and preserves gaps.

**Why this priority**: without the series there is nothing to hunt on;
everything downstream is naming and reuse.

**Independent Test**: on a synthetic world with two sectors given opposite
return drifts, the relative-strength series separate with the correct signs;
a date where a sector has fewer members than the declared floor yields NO
value for that sector (gap, not garbage); NULL-sector stocks influence
nothing except the market-wide baseline.

**Acceptance Scenarios**:

1. **Given** stored per-stock prices/features with sector metadata, **When**
   the operator computes sector series, **Then** one relative-strength and
   one breadth series exist per qualifying sector, named with the sector,
   and values match the definition on spot-checked dates.
2. **Given** a second run with no new data, **Then** zero new rows are
   written (idempotent); **Given** one new trading day, **Then** only that
   day is appended (incremental).
3. **Given** a sector whose membership on a date is below the declared
   floor, **Then** that (sector, date) has no value and the gap is visible,
   never interpolated.
4. **Given** stocks with NULL sector, **Then** they are excluded from every
   sector series (the 005 group rule) while remaining in market-wide
   baselines.

### User Story 2 - Sector series are ordinary discovery atoms (Priority: P1)

A hunt pre-registers tercile atoms on sector series exactly as it does for
any feature. Availability and entanglement screens, family counting, SPA,
and horizon-stated verdicts apply unchanged.

**Independent Test**: a synthetic end-to-end discovery run declaring sector
atoms completes with the atoms usable (or honestly diagnosed), the family
counting every test, and the in-run SPA verdict recorded.

**Acceptance Scenarios**:

1. **Given** a hunt whose atom library names sector series, **When** it
   runs, **Then** the pre-registration records them like any feature atom
   and the run completes with all existing guarantees intact.
2. **Given** an atom naming a sector series that was never computed,
   **Then** the run records an uncomputable-proposal diagnostic (existing
   behavior — no new failure modes).

### User Story 3 - The first sector-conditioned production hunt (Priority: P2)

The operator runs a production hunt conditioned on the leading sectors'
relative-strength and breadth states alongside the proven market vocabulary,
at a geometry consistent with the run-13 lessons, and the verdict — admitted
or honestly rejected — lands with full provenance.

**Acceptance Scenarios**:

1. **Given** the sector series computed on production history, **When** the
   hunt completes, **Then** its verdicts state the horizon and family size,
   and the run records the sector-atom vocabulary in its pre-registration.

### Edge Cases

- A sector present in metadata but with zero price history contributes no
  series (and the compute door reports it).
- Sector names with spaces/case variants ("FINANCIAL SERVICES") must map to
  stable, collision-free series names deterministically.
- A stock whose sector assignment CHANGES rewrites nothing retroactively:
  series values are computed from the membership known at compute time, and
  recomputation is the explicit, documented path (same convention as every
  derived series).
- The nightly refresh failing must not corrupt existing series
  (write-on-success, as everywhere).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-1301**: The system MUST compute per-sector daily aggregate series
  from stored per-stock data — at minimum sector relative strength (sector
  median 20-day return minus market median 20-day return) and sector breadth
  (% of members above their own 200-day average) — for every sector
  discovered from data (no hardcoded taxonomy).
- **FR-1302**: Sector series MUST be stored as ordinary named features
  (macro-entity convention), with the sector encoded in a stable,
  collision-free name; gaps preserved; NULL-sector stocks excluded from
  sector series.
- **FR-1303**: Computation MUST be idempotent and incremental, with a
  declared minimum-membership floor per (sector, date): thinner days yield
  gaps, never values.
- **FR-1304**: The series MUST be consumable by discovery as ordinary
  feature atoms with zero changes to discovery guarantees (availability,
  entanglement, family counting, SPA, horizon in verdicts).
- **FR-1305**: A CLI door MUST compute/refresh the series (derive-style),
  report per-sector rows written and skipped-thin days, and refuse unknown
  sector names listing the known ones.
- **FR-1306**: Surfaces per house rules: MCP parity, docs (USER_GUIDE +
  REGIMES), curriculum aside (sector states as a new conditioning
  dimension), /gefion routing, observability spans; nightly refresh joins
  the existing derived-series top-up.
- **FR-1307**: Zero DDL expected; if any schema change proves unavoidable it
  requires owner approval before implementation.

### Key Entities

- **Sector aggregate series**: a daily market-state series scoped to one
  sector (relative strength; breadth), named with the sector, gaps
  preserved.
- **Sector membership**: the set of stocks carrying a sector on compute
  date; NULL-sector stocks belong to no sector; membership floors are
  declared, not implied.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-1301**: On a synthetic two-sector world with planted opposite drifts,
  the relative-strength series separate with correct signs, and thin-day
  gaps appear exactly where planted — CI-tested.
- **SC-1302**: On production, series exist for every sector meeting the
  membership floor (expected: the 6 leading sectors at minimum), covering
  the same history span as their inputs, and a re-run writes zero new rows.
- **SC-1303**: A synthetic end-to-end discovery run with sector atoms
  completes with all guarantees intact — CI-tested.
- **SC-1304**: The first production sector-conditioned hunt completes and
  its verdict is recorded with full provenance; zero regressions in
  existing suites.

## Out of Scope (v1)

- Industry-level series (finer, thinner — later, same molds).
- Sector regime labels as direct discovery atoms (labels remain in the
  regime/conditional-evaluation world).
- Cross-sector pair/spread signals beyond relative-to-market.

## Assumptions

- Sector metadata quality (80.4% coverage, refreshed weekly by
  fundamentals-update) is adequate; coverage below the floor shows up as
  honest gaps rather than blocked work.
- Point-in-time sector membership is approximated by current metadata (same
  caveat class as adjusted prices, recorded in docs; a vintaged membership
  table is out of scope).
- The spec-011 derive molds fit; if the market-dispatcher cross-section
  needs the sector column, extending that stream is preferred over new
  machinery.
