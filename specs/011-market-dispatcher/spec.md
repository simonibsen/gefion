# Feature Specification: Market-Level Feature Dispatcher Mode

**Feature Branch**: `011-market-dispatcher`
**Created**: 2026-07-12
**Status**: Draft
**Epic**: #114
**Input**: Owner decision 2026-07-12 — "do this correct from the start": market-level
function bodies live in the database and run through the sandboxed dispatcher,
replacing the abandoned SQL-in-registry stopgap and the current repo-resident SQL.

## Why (context)

Gefion has two kinds of feature computation. Per-stock functions (RSI, MACD,
AI-generated features) live IN the database (`feature_functions.function_body`)
and execute through a sandboxed dispatcher — they get the full lifecycle:
enable/disable, validate/fix, export/import, backup under the `irreplaceable`
data type, UI visibility. Market-level series (breadth, dispersion) currently
compute from SQL embedded in repo code, with registry rows as mere pointers —
second-class citizens of their own registry. This feature makes market-level
functions first-class: real Python bodies in the database, executed by a
market-scope mode of the SAME sandbox, with the SAME lifecycle. It is also the
prerequisite for machine-generated market-level features (explicitly out of
scope here; tracked on #114).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Market functions live and run from the registry (Priority: P1)

An operator inspects `feat-fx-list` and sees `breadth_sma200` and
`dispersion_20` as real functions — Python bodies visible in the database,
scope marked `market` — and `gefion macro derive` computes their daily values
by executing those bodies through the sandboxed dispatcher, per date, over the
stock cross-section.

**Why this priority**: this IS the feature — everything else decorates it.

**Independent Test**: edit the stored body of a market function in the DB
(e.g. change breadth's threshold), run `macro derive --full` on a synthetic
world, and observe the changed output — proving the DATABASE body, not repo
code, is what executes.

**Acceptance Scenarios**:

1. **Given** the two seeded market functions, **When** `macro derive` runs,
   **Then** values land in the feature store exactly as today (same series
   ids, same feature names, same storage) and match the legacy SQL's output
   on identical data (migration gate).
2. **Given** an operator-modified body in the database, **When** derive runs,
   **Then** the modified logic executes (DB is the source of truth) and
   redeploys do NOT clobber the edit (seeding is create-if-absent).
3. **Given** a market body that attempts a forbidden import or filesystem
   access, **When** it executes, **Then** the sandbox refuses exactly as it
   does for per-stock bodies, and no values are written.

### User Story 2 - Full lifecycle applies to market functions (Priority: P2)

The operator manages market functions with the same doors as every other
function: `feat-fx-disable breadth_sma200` stops it from computing (derive
reports it skipped, disabled); `feat-def-validate` shows no orphans;
`feat-fx-export`/`import` round-trips the bodies; whole-DB and
`irreplaceable` backups carry them.

**Independent Test**: disable a market function, run derive (skipped,
reported), re-enable, derive resumes; export then import on a clean DB
reproduces the function.

**Acceptance Scenarios**:

1. **Given** a disabled market function, **When** derive runs, **Then** it is
   skipped with an honest per-function report (never silently computed or
   silently dropped).
2. **Given** `feat-def-validate`, **When** run after migration, **Then** zero
   orphans — the migrated features reference real, enabled functions.

### User Story 3 - Honest failure and honest gaps (Priority: P2)

A market body that raises, returns a non-numeric value, or produces nothing
for a date fails LOUDLY per date-range run: the failure is recorded and
reported, no partial garbage lands in the feature store, and previously
computed values are untouched. Thin days (cross-section below the declared
minimum) remain gaps, never fabricated numbers.

**Acceptance Scenarios**:

1. **Given** a body that raises on execution, **When** derive runs, **Then**
   the run reports the failure with the function name and reason, writes zero
   values for that function, and other functions in the same run are
   unaffected.
2. **Given** a date whose cross-section is thinner than `min_stocks`,
   **When** derive runs, **Then** that date gets no value and the gap is
   visible in coverage reporting.

### Edge Cases

- Body returns a value for a date outside the requested range → refused
  (shape violation), nothing written.
- Body returns NaN/inf → treated as "no value" for that date, counted and
  reported (not stored, not fabricated).
- Two functions where one fails: the healthy one completes; exit status
  reflects partial failure honestly.
- Re-run after a failure: incremental logic resumes from the last stored
  date per function (a failed function wrote nothing, so it retries its
  full pending range).
- Legacy callers: the fifth hunt's atoms (`macro_breadth_sma200`,
  `macro_dispersion_20`) and all existing stored values keep working
  unchanged — same feature names, same series ids.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-1101**: The feature-function registry MUST support a scope
  discriminator distinguishing per-stock from market-level functions (DDL —
  propose to owner before applying; two-file rule + data dictionary).
- **FR-1102**: Market-level function bodies MUST be Python stored in the
  registry and executed through the EXISTING sandbox (same import whitelist,
  no filesystem/network) — no second executor.
- **FR-1103**: A market body receives, per date, a cross-sectional view of
  the Stock universe (close/high/low/volume plus any per-stock feature
  columns the function declares as inputs) and returns one numeric value or
  no value for that date.
- **FR-1104**: Execution MUST stream by date batches — the full 6k-stocks ×
  26-years history is never materialized at once.
- **FR-1105**: `macro derive` remains the operator door, now dispatcher-
  backed: idempotent, incremental from each function's last stored date,
  `--full` recompute, `--min-stocks` thin-day floor preserved.
- **FR-1106**: The two existing series MUST migrate to DB-resident bodies
  with output equal to the legacy SQL on identical data (numeric equality
  within float tolerance is the migration gate); repo code becomes seed-only
  (create-if-absent; operator edits persist across deploys).
- **FR-1107**: The full function lifecycle MUST apply to market functions:
  enable/disable honored by derive (skipped-and-reported when disabled),
  validate/fix sees no orphans, export/import round-trips, backups include
  them.
- **FR-1108**: Failures MUST be honest and isolated per function: recorded
  reason, zero partial writes for the failing function, healthy functions
  unaffected, non-zero exit on any failure.
- **FR-1109**: Values MUST continue landing exactly where they do today
  (feature store keyed by macro series id, `entity_table='macro_series'`) —
  zero new value-storage surfaces, existing atoms and stored history
  untouched.
- **FR-1110**: Surfaces per house rules: CLI (`macro derive`,
  `feat-fx-import` accepting market-scope bodies), MCP parity, docs, and
  curriculum Module 2 updated to say where market-level function code lives
  NOW; observability spans with per-function timing.

### Key Entities

- **Market function**: a registry row — name, Python body, scope=market,
  declared per-stock feature inputs, enabled flag, version.
- **Cross-sectional view**: the per-date frame a body receives (symbols ×
  declared columns for that date).
- **Derived series values**: unchanged — daily numbers in the feature store
  keyed by macro series id.

## Automation *(consider)*

- Nightly cron already runs `macro derive`; it inherits the dispatcher
  backend with no cron change.
- `/gefion` routing: "add/modify a market-level feature" → edit registry body
  + `macro derive --full` (documented, not new tooling).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-1101**: On identical data, migrated functions reproduce the legacy
  SQL outputs exactly (within declared float tolerance) for every date —
  verified by an automated migration-equality test.
- **SC-1102**: Full-history recompute of both series completes in minutes
  (target ≤ 10) on the production host, streaming (peak memory bounded and
  measured).
- **SC-1103**: A sandbox-violation body writes zero values and reports the
  refusal; a raising body writes zero values and reports the reason — both
  covered by tests.
- **SC-1104**: `feat-def-validate` reports zero orphans after migration;
  disable→derive→enable→derive round-trip behaves as specified.
- **SC-1105**: An operator can change a market function's logic by editing
  ONLY the database body (no deploy) and see the changed output after
  `macro derive --full` — demonstrated end-to-end in tests.
- **SC-1106**: All existing consumers (fifth-hunt atoms, stored values,
  regime labels referencing macro features) work unchanged post-migration —
  regression-tested.

## Out of Scope (v1 — remains on #114)

- Machine-GENERATION of market-level bodies by the experiment system, and
  the review/approval gate such generation requires.
- Market-level features over non-stock entities (macro-of-macro).
- Moving `macro_value` ingestion (VIX et al.) into this mode — provider
  ingestion stays as-is.

## Assumptions

- The existing sandbox's import whitelist (numpy etc.) suffices for market
  bodies; no whitelist expansion in v1.
- Per-date values are pure functions of that date's cross-section plus each
  stock's own trailing data already present in declared feature columns —
  bodies do not receive future data by construction (causality inherited
  from the inputs).
- The scope discriminator is one small DDL change on `feature_functions`;
  owner approval will be sought at plan time with the exact DDL.
