# Feature Specification: SPA Re-Verdict for Discovery (Data-Snooping-Robust Selection Check)

**Feature Branch**: `010-spa-reverdict`
**Created**: 2026-07-09
**Status**: Draft
**Input**: User description: "Data-snooping-robust selection check for regime discovery: a post-run SPA re-verdict over the candidate ledger (issue #87)…" (full text in git history; owner decisions of 2026-07-09 recorded on issue #87)

## The problem in one paragraph

Discovery's v1 error control — the inner-evidence screen plus one flat
Benjamini–Hochberg family at 0.01 over the full realized candidate set, losers
counted — is honest at v1's capped volumes (measured false-admission ~1/100
noise runs). But BH treats the candidates' p-values as given; it does not model
the *search* that produced them. As the searched family grows (more candidates
per cycle, deeper grammar), the best-of-many selection effect grows with it,
and a flat FDR family becomes a progressively weaker description of what the
search actually did. That is why raising per-cycle budgets beyond v1 defaults
(~50–200 candidates) or the grammar depth cap above K=2 is **gated** (006
FR-108, Clarification Q1) on a selection-aware test: one that asks, jointly
over everything the search tried, "is the *best* of these distinguishable from
the best you'd find searching pure noise?" This spec adds that test — a
**post-run SPA re-verdict** over an existing run's candidate ledger — and
encodes the budget gate so it is enforced, not just documented.

## Core concepts

- **The question SPA answers.** BH asks "which of these p-values survive a
  false-discovery budget?" The SPA (Superior Predictive Ability) test asks the
  selection-aware question: "given that we searched THIS whole family, is the
  best candidate's performance beyond what searching noise would produce?" The
  null is that *no* candidate beats the benchmark; the test statistic is the
  studentized best; the null distribution comes from resampling the entire
  family's performance series jointly.
- **Owner decisions (2026-07-09, on issue #87):**
  1. **Post-run re-verdict** — a command over an existing run's ledger, so it
     works retroactively (including the two admitted prod regimes). An in-run
     gate is a later increment.
  2. **Hansen SPA, not White's Reality Check** — studentized with a
     data-dependent recentered null. RC is conservative when the family
     deliberately contains many weak candidates — and discovery families do,
     by design (they count the losers). The consistent SPA p-value is the
     verdict; the lower/upper variants are reported as diagnostics.
  3. **Stationary bootstrap, jointly across candidates** — per-observation
     relative-performance series resampled with the SAME time blocks for every
     candidate (preserving cross-candidate dependence), automatic expected
     block length, seeded for reproducibility.
- **Reconstruct, then verify, then judge.** The ledger stores each candidate's
  expression and per-test summaries (p-value, n, statistic) but not
  per-observation series; the run row pre-registers seed, search space,
  segregation windows, and dataset version. The re-verdict therefore
  **reconstructs** each counted candidate's per-observation records over the
  pre-registered outer window — and before any bootstrap verdict, it must
  **reproduce the ledger's stored p-values** from the reconstruction. A
  mismatch means the world has drifted (price backfills, environment) and the
  command **refuses honestly**: no verdict from a different world than the one
  the run saw.
- **A stricter gate beside BH, never a rewrite.** The SPA p-value is recorded
  durably with the run, append-only. It never alters BH verdicts, the
  candidate ledger, or the family accounting. An admitted edge whose family
  fails SPA is **flagged loudly** — in show/verdicts and the trust-grades view
  — not auto-demoted (forward fold evidence remains the demotion mechanism).
- **The budget gate becomes code.** `discover start` refuses budgets or depth
  above v1 caps unless the SPA machinery exists and a passing SPA re-verdict
  is recorded for the relevant prior runs. Enforced in config validation, not
  just documented.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Re-verdict an existing run (Priority: P1)

As the operator, I run the SPA re-verdict over a completed discovery run's
ledger and get a selection-aware verdict: the consistent SPA p-value for the
family (with lower/upper diagnostics), recorded durably with the run.

**Why this priority**: this is the capability itself — everything else
(surfacing, gating) consumes its output. Retroactivity matters: the two
admitted prod regimes get their selection-aware check without re-running
discovery.

**Independent Test**: run the re-verdict on a seeded synthetic run with a
planted edge → SPA p-value is small; run it on a pure-noise run → SPA p-value
is large; both recorded with the run and reproducible under the same seed.

**Acceptance Scenarios**:

1. **Given** a completed run whose ledger has counted candidates, **When** the
   re-verdict runs, **Then** it reports the consistent SPA p-value plus
   lower/upper diagnostics, the family size used, iterations, and seed — and
   records them durably with the run.
2. **Given** the same run and the same seed, **When** the re-verdict runs
   again, **Then** the p-values are identical (reproducible) and the recorded
   result is versioned/appended, not overwritten.
3. **Given** a run with a genuinely planted edge (synthetic), **When**
   re-verdicted, **Then** SPA rejects (small p) — the test has power against
   real edges.
4. **Given** a run whose candidates were all noise, **When** re-verdicted,
   **Then** SPA does not reject (large p) — searching noise looks like noise.

---

### User Story 2 - Reconstruction is verified before any verdict (Priority: P1)

As the owner, I can trust that the SPA verdict was computed on the same world
the run saw: the re-verdict first reproduces the ledger's stored per-candidate
p-values from its reconstruction, and refuses with a clear explanation if it
cannot.

**Why this priority**: without this, a price backfill or environment change
silently yields a verdict about a *different* world — worse than no verdict.
This is the honesty core of the feature.

**Independent Test**: re-verdict a run, then perturb one price row in the
outer window and re-verdict again → the second run refuses with a
reconstruction-mismatch report naming the divergent candidates.

**Acceptance Scenarios**:

1. **Given** an unchanged world, **When** the re-verdict reconstructs, **Then**
   every counted candidate's recomputed p-value matches its stored one within
   tolerance, and the verification result is part of the recorded output.
2. **Given** price data that changed since the run, **When** reconstruction
   diverges beyond tolerance, **Then** the command refuses — naming the
   mismatched candidates and the size of the divergence — and records no
   verdict.
3. **Given** a run with `family_size` 0 or no counted candidates, **When**
   re-verdicted, **Then** the command refuses honestly ("nothing to test")
   rather than emitting a degenerate p-value.

---

### User Story 3 - The verdict is visible where verdicts live (Priority: P2)

As a researcher, `regime discover show` and `verdicts` display the run's SPA
result beside the BH family, and the trust-grades view flags an admitted edge
whose family failed SPA — loudly, but without auto-demotion.

**Why this priority**: a recorded verdict nobody sees changes no decisions.
Depends on US1.

**Independent Test**: after re-verdicting a run, `show`/`verdicts` display the
SPA p-value and pass/fail at the declared level; a failing family's admitted
edge carries a visible flag in the grades view.

**Acceptance Scenarios**:

1. **Given** a re-verdicted run, **When** `show` or `verdicts` renders,
   **Then** the SPA p-value, level, iterations, seed, and pass/fail appear
   beside the BH family size.
2. **Given** an admitted edge whose run's family fails SPA, **When** grades
   are displayed, **Then** the edge carries a loud "family failed
   selection-aware check" flag — and its BH verdict and trust grade are
   unchanged (no auto-demotion).
3. **Given** a run never re-verdicted, **When** `show` renders, **Then** the
   SPA field reads "not yet run" (absence is visible, not implied).

---

### User Story 4 - The budget gate is enforced in code (Priority: P2)

As the owner, discovery refuses to start a run whose budget or depth exceeds
v1 caps unless the relevant prior runs carry a passing SPA re-verdict — the
gate I declared in 006 is now enforced by configuration validation, not
memory.

**Why this priority**: the entire motivation of #87 — the gate must bind.
Depends on US1 (there must be something to check).

**Independent Test**: `discover start` with budget above the v1 cap and no
passing SPA on record → refused with an explanation naming the gate; the same
start after a passing re-verdict on the prior runs → accepted.

**Acceptance Scenarios**:

1. **Given** no SPA re-verdicts on record, **When** a run is configured with
   budget or depth above v1 caps, **Then** `discover start` refuses, naming
   the gate and the command that satisfies it.
2. **Given** passing SPA re-verdicts on the declared relevant prior runs,
   **When** the same configuration starts, **Then** it is accepted and the
   gate's satisfaction is recorded in the run's pre-registration.
3. **Given** budgets within v1 caps, **When** a run starts, **Then** the gate
   does not interfere (v1 behavior unchanged).

---

### User Story 5 - The negative control keeps the test honest (Priority: P3)

As the owner, CI carries a seeded negative control: SPA on pure-noise runs
rejects at no more than the nominal rate — the same discipline 006 applied to
the BH pipeline.

**Why this priority**: a selection-aware test that itself over-rejects would
quietly reintroduce the problem it exists to prevent. Depends on US1.

**Independent Test**: the CI control runs SPA over seeded noise families and
asserts the rejection rate at level α stays ≤ α (with the seeded margin).

**Acceptance Scenarios**:

1. **Given** seeded pure-noise discovery families, **When** the control runs
   SPA over them, **Then** the rejection rate at the declared level does not
   exceed the nominal rate beyond the seeded tolerance.
2. **Given** a seeded planted-edge family in the same control, **When** SPA
   runs, **Then** it rejects — power is demonstrated alongside size.

---

### Edge Cases

- **Family of one**: a single counted candidate is a legitimate family; SPA
  degenerates gracefully toward the single-candidate test (no special-casing
  into a refusal).
- **Refused/uncounted candidates**: candidates with `counted_in_family=false`
  or power-refused tests are excluded exactly as the BH family excluded them —
  the SPA family must equal the realized BH family, not the enumerated set.
- **Multiple signals per candidate**: a candidate can carry several outer
  tests (one per signal); the reconstruction must reproduce each, and the SPA
  family is over the same units the BH family counted.
- **Fresh-holdout reserve runs**: a run whose outer window was the consumed
  reserve reconstructs over that window; consuming nothing new (read-only).
- **Very short outer windows**: too few observations for a meaningful block
  bootstrap → honest refusal with the observation count and floor named.
- **Missing price data** (symbol delisted since the run, DB pruned):
  reconstruction fails → refusal names what is missing; no verdict.
- **Re-verdict re-runs**: new executions append (new seed/iterations allowed);
  history of re-verdicts is visible; the *latest* passing/failing state drives
  the gate and flags.
- **Runs from before this spec**: fully supported — that is the point of
  post-run (the two admitted prod regimes are the acceptance case).

## Requirements *(mandatory)*

### Functional Requirements

**The re-verdict**

- **FR-1001**: A re-verdict command MUST compute the Hansen SPA test over a
  completed run's realized candidate family: studentized statistics, a
  data-dependent recentered null, and the consistent p-value reported as the
  verdict with lower/upper variants as diagnostics.
- **FR-1002**: The bootstrap MUST be a stationary bootstrap over each counted
  candidate's per-observation relative-performance series, resampling the same
  time blocks jointly across all candidates, with an automatically chosen
  expected block length; iterations (default 1000) and seed MUST be
  parameters, and identical inputs + seed MUST reproduce identical p-values.
- **FR-1003**: The SPA family MUST equal the realized BH family: the same
  counted candidates and the same test units (per signal), with refused and
  uncounted entries excluded identically.

**Reconstruction & self-verification**

- **FR-1004**: The re-verdict MUST reconstruct per-observation records from
  the ledger's stored expressions plus the run's pre-registration (seed,
  search space, segregation windows, dataset version) over the pre-registered
  outer window — reading only; it MUST NOT modify any ledger or market row.
- **FR-1005**: Before any bootstrap verdict, the recomputed per-candidate
  p-values MUST reproduce the stored ones within a declared tolerance; on
  mismatch the command MUST refuse, naming the divergent candidates and
  magnitudes, and record no verdict.
- **FR-1006**: Refusals MUST be honest and specific: reconstruction mismatch,
  missing price data, family size 0 / no counted candidates, or an outer
  window too short for the block bootstrap (floor named).

**Recording & surfacing**

- **FR-1007**: Each re-verdict execution MUST be recorded durably with the run
  — p-values (consistent/lower/upper), level, iterations, seed, block-length,
  family size, verification result, timestamp — append-only: re-runs add
  records; nothing rewrites BH verdicts, the ledger, or prior re-verdicts.
  Any schema addition requires owner-approved DDL (propose, don't execute).
- **FR-1008**: `regime discover show` and `verdicts` MUST display the latest
  SPA result (or "not yet run") beside the BH family; the trust-grades view
  MUST flag an admitted edge whose family's latest SPA fails — flag only,
  no auto-demotion.

**The budget gate**

- **FR-1009**: `discover start` configuration validation MUST refuse budgets
  or grammar depth above the v1 caps unless the declared relevant prior runs
  carry a passing latest SPA re-verdict; the refusal MUST name the gate and
  the satisfying command. Within-cap configurations MUST be unaffected.
- **FR-1010**: When the gate is satisfied, that fact (which runs, which
  re-verdict records) MUST be recorded in the new run's pre-registration.

**Honesty controls**

- **FR-1011**: A seeded CI negative control MUST assert that SPA over
  pure-noise families rejects at no more than the nominal rate (within seeded
  tolerance), alongside a planted-edge power check.

**Interfaces & definition of done** (docs-drift enforced)

- **FR-1012**: CLI (`regime discover spa <run>` with iterations/seed options)
  and a mirrored MCP tool MUST land in the same increment, with docs (README,
  USER_GUIDE, MCP_WORKFLOWS, the discovery docs) and the gefion-learn Module
  10 aside + checkpoint, and `/gefion` operator routing.
- **FR-1013**: The re-verdict MUST be observable: spans carrying family size,
  iterations, block length, verification outcome, and the resulting p-values.

### Key Entities

- **SPA re-verdict record**: one execution's durable result — run, p-values
  (consistent/lower/upper), level, iterations, seed, expected block length,
  family size, verification outcome, created-at. Append-only history per run.
- **Reconstruction verification**: per-candidate comparison of recomputed vs
  stored p-values with tolerance and pass/fail — part of the recorded result.
- **Budget gate state**: the v1 caps, and the mapping from an above-cap
  configuration to the prior runs whose passing SPA satisfies the gate.

## Automation *(consider)*

- **Proposed skill**: None needed — `/gefion` routing gains "is this run's
  family trustworthy at scale" → the SPA tool; no new workflow shape.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-1001**: On seeded synthetic runs, the re-verdict rejects the planted-edge
  family (small p) and does not reject the pure-noise family (large p), and
  repeated executions with the same seed produce byte-identical p-values.
- **SC-1002**: The re-verdict on the two admitted production runs completes and
  records verdicts without modifying a single ledger row (row-count and
  content checksums identical before/after).
- **SC-1003**: Verification catches drift: perturbing one outer-window price
  row causes the re-verdict to refuse with the divergent candidate named —
  zero verdicts emitted from a drifted world in the test suite.
- **SC-1004**: `discover start` above v1 caps is refused without a passing SPA
  on record and accepted with one; within-cap starts are byte-identical to
  today's behavior.
- **SC-1005**: The CI negative control holds: pure-noise rejection rate at the
  declared level stays within the seeded tolerance of nominal, and the
  planted-edge check rejects.
- **SC-1006**: The SPA result is visible in `show`/`verdicts` (or explicitly
  "not yet run"), and an admitted edge with a failing family carries the flag
  in the grades view while its BH verdict and grade remain unchanged.

## Assumptions

- **Test units**: the SPA family is over the same units BH counted (candidate
  × signal outer tests); the per-observation relative-performance series is
  the same records the outer tests were computed from.
- **Level**: the SPA pass/fail level defaults to the run's declared FDR rate
  (0.01) for gate purposes; the p-value itself is always reported so stricter
  or looser readings remain possible.
- **Tolerance for verification**: small numeric tolerance for p-value
  reproduction (floating-point and library-version noise), declared in the
  recorded result; order-of-magnitude divergence is drift, not noise.
- **"Relevant prior runs" for the gate**: the most recent completed runs on
  the same dataset version whose configuration the raise extends; recorded
  explicitly at gate-satisfaction time so the mapping is auditable.
- **Iterations default 1000**, seed defaults to the run's own seed — both
  overridable per execution and recorded.
- **Runtime**: a re-verdict over a v1-sized family (~50–200 candidates,
  ~6–13 week outer window) completes in minutes on the dev machine; iterations
  scale linearly.

## Out of Scope

- The **in-run SPA gate** (blocking admission during a run) — a follow-up
  increment once the post-run tool has accumulated operational trust.
- The `signal_source` rungs `model_predictions` and `strategy_backtests`, and
  automated fold accrual — queued behind this in #87 but separate work
  (equity-curve inference is explicitly not a clean paired test).
- Changing v1 BH/FDR semantics, the inner screen, or the family-counting
  rules — SPA is an additional gate beside them, not a replacement.
- Auto-demotion of admitted edges on SPA failure — forward fold evidence
  remains the demotion mechanism; SPA failure flags.
