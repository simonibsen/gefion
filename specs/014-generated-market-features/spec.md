# Feature Specification: Generated Market-Level Features with an Owner Gate

**Feature Branch**: `014-generated-market-features`
**Created**: 2026-07-18
**Status**: Draft
**Input**: User description: "Machine-generated market-level features with an owner approval gate, plus market-level features over non-stock entities (closes epic #114)."

## Overview

Spec 011 made market-level feature series first-class: database-resident
function bodies, executed per trading date over the streamed stock
cross-section, with full lifecycle and failure isolation. Spec 013 added
deterministic, template-seeded sector series. What the system still cannot do
— the remainder of epic #114 — is two things:

1. **Invent new market-level series itself.** The experiment system already
   generates per-stock feature functions (synthesized code, evaluated in
   experiments, promoted on evidence). Market-level series — breadth,
   dispersion, concentration, participation classes — are exactly the
   vocabulary the discovery pipeline consumes as conditioning atoms, and today
   every new one is hand-written or hand-templated.
2. **Compute market-level series from other market-level series.** Composite
   indicators over existing macro series (for example a risk-state composite
   over volatility, breadth, and dispersion) have no home: the market
   execution mode assumes the stock universe is the input.

Machine-generated code that writes to the production feature store is a
different risk class from machine-generated code evaluated inside a sandboxed
experiment. The centerpiece of this feature is therefore the **owner gate**:
a generated market-level body never computes a single stored value until a
human has reviewed the code, its declared inputs, and a sandbox dry-run, and
has explicitly approved it. This follows the house precedent set by regime
discovery — the machine proposes, a human owns the gate.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Machine-proposed market series behind an owner gate (Priority: P1)

The autonomous experiment system (or the owner, explicitly) generates a
candidate market-level function body from a principle — for example
"participation confirms trend" yields a candidate breadth-class series. The
candidate lands in a **pending-review** state: it is stored with full
provenance (what generated it, from which principle, when) but is not
executable against stored data. The owner opens the review surface, sees the
generated code, its declared inputs, and the result of a sandbox dry-run over
a synthetic cross-section, and approves or rejects with a reason. Only an
approved candidate joins the production roster: the nightly derive picks it
up automatically, and from that point it behaves exactly like a hand-seeded
market function (disable, export/import, failure isolation).

**Why this priority**: This is the epic's headline capability and the reason
it was deferred — generation without the gate is unacceptable, and the gate
without generation has nothing to review. Delivering this story alone is a
complete, safe MVP: the machine can widen the market-series vocabulary and a
human owns what reaches production.

**Independent Test**: Can be fully tested by generating a candidate from a
principle, verifying it cannot produce stored values pre-approval, walking
the review surface, approving it, and observing the nightly derive compute it
— with rejection and refusal paths exercised alongside.

**Acceptance Scenarios**:

1. **Given** a candidate market body generated from a principle, **When** it
   is stored, **Then** its state is pending review, its provenance (origin,
   principle, timestamp) is recorded, and any attempt to compute stored
   values from it is refused with a message naming the gate.
2. **Given** a pending candidate, **When** the owner opens its review,
   **Then** the full function body, its declared inputs, its provenance, and
   a sandbox dry-run result over a synthetic cross-section are all visible in
   one place.
3. **Given** a pending candidate, **When** the owner approves it, **Then** it
   becomes an active market function, the next scheduled derive computes it
   with no configuration change, and its values are indistinguishable in
   lifecycle from a hand-seeded series.
4. **Given** a pending candidate, **When** the owner rejects it with a
   reason, **Then** it never executes against stored data, and the candidate
   plus reason are retained for audit — rejection hides it from the pending
   queue, it does not erase the record.
5. **Given** a candidate whose body violates the sandbox (forbidden import,
   wrong return shape), **When** the dry-run runs, **Then** the violation is
   reported on the review surface and the candidate cannot be approved until
   a corrected version replaces it.

---

### User Story 2 - Market series computed from other market series (Priority: P2)

The owner (or, later, the generator) defines a market-level function whose
inputs are **named macro series** rather than the stock cross-section — for
example a composite risk-state indicator over a volatility index, breadth,
and dispersion. Each trading date, the function receives that date's values
of its declared input series and returns one value (or an honest gap). The
output series lives under the macro home like every other market series and
is immediately usable downstream (discovery atoms, regime expressions,
charts).

**Why this priority**: Composites over existing series are the cheapest new
information in the system — the inputs already exist and are maintained
nightly. It is independently valuable without Story 1 (hand-written
composites) and it completes the epic's second remaining item.

**Independent Test**: Can be fully tested by registering a composite over
existing macro series, deriving its full history, and verifying values, gap
behavior, and downstream usability without touching Story 1 machinery.

**Acceptance Scenarios**:

1. **Given** a function declaring three existing macro series as inputs,
   **When** derive runs, **Then** each date's output is computed from exactly
   that date's stored input values, and the series appears under the macro
   home with full history.
2. **Given** a date on which any declared input series has no stored value,
   **When** derive reaches that date, **Then** the output has no value for
   that date — an honest gap, never an imputed number.
3. **Given** a function declaring an input series that does not exist (or is
   disabled), **When** registration or derive is attempted, **Then** it is
   refused with a message naming the missing input.
4. **Given** a composite whose declared inputs would (directly or through
   another composite) include its own output, **When** registration is
   attempted, **Then** it is refused — dependency cycles cannot be created.
5. **Given** derive running the composite incrementally, **When** it runs
   nightly after inputs update, **Then** only missing dates are computed and
   a rerun writes nothing new (idempotent), matching existing derive
   semantics.

---

### User Story 3 - Generation targets composites too (Priority: P3)

Generation from Story 1 can also emit candidates in the Story 2 shape:
machine-proposed composite indicators over existing macro series. The same
gate applies — pending review, dry-run (over synthetic series values),
explicit approval — and the same lifecycle follows.

**Why this priority**: It multiplies the value of the first two stories but
requires both; nothing else depends on it.

**Independent Test**: Generate a composite candidate, verify the identical
gate semantics, approve, and observe nightly computation.

**Acceptance Scenarios**:

1. **Given** a principle suggesting a relationship among existing macro
   series, **When** generation runs in composite mode, **Then** the candidate
   declares only existing macro series as inputs and enters the same
   pending-review state as Story 1 candidates.
2. **Given** a composite candidate under review, **When** the dry-run runs,
   **Then** it executes over synthetic values for the declared input series
   and its result is shown to the reviewer.

---

### Edge Cases

- Generation backend unavailable (the code-synthesis path cannot run): the
  template fallback still produces candidates; if neither path can, the cycle
  reports the gap honestly and proposes nothing — no empty candidates.
- A candidate generated twice from the same principle: the second candidate
  is a new version, never a silent overwrite; the pending queue shows both.
- Approval attempted by the machine (any non-interactive path): refused — the
  gate is human-only by construction, mirroring the discovery precedent.
- A body that passes dry-run but fails on real history (data shape it never
  saw): failure isolation from 011 applies — the failing run writes nothing,
  reports the error, and the series simply has no values yet; the owner can
  reject or replace the candidate.
- An approved generated series later proves worthless: the standard exits
  apply (disable = skip-and-report; the 011 lifecycle is unchanged). Approval
  is not un-doable into "pending" — the audit trail keeps the decision.
- Export/import: candidates in pending or rejected states are excluded from
  export — only approved (active/disabled) functions travel; review history
  stays with the source system.
- A composite over an input series that is itself disabled later: derive
  skips-and-reports the composite (disabled input = reported skip, so silence
  never reads as health; missing values on a date = gap).
- Thin synthetic dry-run passing but real cross-section thinner than the
  body's floor: the existing thin-day policy applies (no value, honest gap).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-1401**: The experiment system MUST be able to generate candidate
  market-level function bodies in the market contract (one value or gap per
  trading date from that date's cross-section), from a principle, via both a
  code-synthesis path and a deterministic template fallback.
- **FR-1402**: A generated market-level candidate MUST be stored in a
  pending-review state with provenance (origin mechanism, principle,
  timestamp, generator identity) and MUST NOT be executable against stored
  production data while pending or rejected.
- **FR-1403**: Generated market-level candidates MUST NEVER be auto-approved.
  Approval MUST be an explicit human act; every autonomous path (cycles,
  schedulers, automation acting through any surface) MUST be refused at the
  gate.
- **FR-1404**: The review surface MUST present, in one place: the full
  function body, its declared inputs, its provenance, and the result of a
  sandbox dry-run over synthetic data matching the function's input shape
  (cross-section for Story 1, named series values for Story 3). A dry-run
  sandbox violation MUST block approval.
- **FR-1405**: Rejection MUST be first-class: a reason is required, the
  rejected candidate and reason are retained for audit, and rejected
  candidates never execute. Records are superseded or hidden, never erased.
- **FR-1406**: Upon approval, a generated function MUST join the standard
  market-function lifecycle with no further special-casing: picked up by the
  scheduled derive automatically, disable = skip-and-report, failure
  isolation (a failing body writes nothing), zero orphans between function
  and its series definition.
- **FR-1407**: The system MUST support market-level functions whose declared
  inputs are named macro series (composite mode): per trading date the
  function receives that date's stored values of its declared inputs and
  returns one value or a gap; outputs are stored under the macro home per the
  entity model.
- **FR-1408**: Composite-mode inputs MUST be validated at registration and at
  derive: unknown or disabled input series refuse loudly; a date missing any
  declared input value yields a gap, never an imputed value.
- **FR-1409**: Dependency cycles among composite functions (a function
  consuming, directly or transitively, its own output) MUST be refused at
  registration.
- **FR-1410**: Composite derive MUST match existing derive semantics:
  incremental (only missing dates), idempotent (rerun writes nothing new),
  full-recompute supported, causal by construction (only stored values as of
  each date are read).
- **FR-1411**: All new operations (propose/generate, pending queue, review
  show, approve, reject, composite registration) MUST be reachable via CLI
  and MCP, with the UI presenting at minimum the pending queue and review
  surface (FR-042 parity). Approval through MCP MUST carry the same
  human-gate requirement as the CLI.
- **FR-1412**: New modules MUST emit observability spans with propagated
  parent context (Constitution: Observability).
- **FR-1413**: The sandbox import whitelist is unchanged by this feature; a
  generated body requiring anything outside it fails the dry-run and cannot
  be approved.
- **FR-1414** *(documentation, definition of done)*: User-facing docs and the
  learning path MUST reflect the new capabilities in the same increment, and
  the docs-drift test MUST cover the new commands and MCP tools.

### Key Entities

- **Generated candidate**: a market-level function body plus provenance
  (origin, principle, generator, created-at), declared inputs, review state
  (pending / approved / rejected), and its dry-run record.
- **Review decision**: who decided, when, verdict, reason (required on
  rejection); immutable audit record attached to the candidate.
- **Composite market function**: a market-level function whose declared
  inputs are named macro series rather than the stock cross-section; its
  output series lives under the macro home.
- **Dry-run record**: the synthetic-input execution result shown at review —
  success with sample values, or the exact sandbox/shape violation.

## Automation *(consider)*

- **Proposed skill**: None needed.
- **Rationale**: Generation rides the existing experiment-cycle skill; review
  and approval are deliberately interactive CLI/UI acts (the gate must not be
  automated), and the nightly derive already adopts approved series without
  cron edits.

## Out of Scope

- Moving provider ingestion (VIX et al.) into any of these modes — provider
  ingestion stays as-is (carried from 011).
- Changes to per-stock function generation or its auto-approval policy.
- Sandbox whitelist expansion.
- Automatic quality evaluation of candidate series (e.g., auto-running
  discovery on candidates pre-approval) — the review packet is code, inputs,
  provenance, and dry-run; evidence-gathering beyond that is future work.
- Sector-scoped execution mode (a per-sector universe variant) — sector
  series remain template-seeded per 013.

## Assumptions

- Generation is triggered from within experiment cycles (as per-stock
  generation is today) and by an explicit owner-invoked command; in both
  cases every market-level candidate queues for review — cycle automation
  never shortens the gate.
- The reviewer decides on code, declared inputs, provenance, and dry-run
  alone; no candidate evaluation against real history happens pre-approval
  (evaluation against stored data IS execution, which the gate forbids).
- Synthetic dry-run data is generated deterministically (seeded) so review
  results are reproducible.
- Composite mode reads input series values as stored at derive time; if an
  input series is later revised and fully re-derived, the composite's full
  recompute path is the recovery door (matching existing derive semantics).
- The candidate/review state lives alongside the existing function lifecycle
  states without breaking existing consumers (existing functions are all, by
  definition, approved).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-1401**: A generated market-level candidate produces **zero** stored
  values before explicit human approval, demonstrated by tests that attempt
  every execution path (scheduled derive, explicit derive, full recompute)
  against a pending and a rejected candidate.
- **SC-1402**: An owner can go from "candidate exists" to an approve/reject
  decision using only the review surface — one command (or one screen) shows
  code, inputs, provenance, and dry-run — verified in the acceptance walk.
- **SC-1403**: An approved generated series appears in the next scheduled
  derive run with zero configuration changes (no cron edit, no manual
  registration), and its lifecycle operations (disable, export/import,
  failure isolation) are indistinguishable from hand-seeded series in tests.
- **SC-1404**: A composite over at least three existing macro series derives
  its full stored history in the same order of runtime as existing derived
  series (minutes, not hours) and its nightly incremental run completes in
  seconds, measured on the production-scale dataset.
- **SC-1405**: Every rejected or dry-run-failed candidate leaves zero stored
  values and a complete audit record (candidate, reason, decision, dry-run
  result), verified by test.
- **SC-1406**: All existing market functions, derived series, and their
  consumers behave identically after this feature ships (regression parity),
  verified by the existing derive equality and lifecycle test suites passing
  unchanged.
