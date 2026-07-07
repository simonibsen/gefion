# Feature Specification: Agentic Regime Discovery — Autonomously Proposing and Validating Regimes Without Fooling Ourselves

**Feature Branch**: `006-agentic-regime-discovery`
**Created**: 2026-07-03
**Status**: Draft
**Depends on**: `005-regime-slicing` (causal labels, conditional evaluation, reconciliation)
**Input**: User description: "Allow the system to discover regimes — perhaps agentic discovery — where the autonomous agent proposes regime hypotheses, generates detection code, and tests conditional edges. Account for the overfitting/leakage traps strictly."

## Motivation & Threat Model *(mandatory context)*

Spec 005 lets a **human** specify a regime and then slice/evaluate against it. This spec
lets the **autonomous agent** *discover* regimes — inferring them from data (Level 2) or
proposing them as principle-seeded hypotheses and generating their detection code
(Level 3) — reusing the existing propose→codegen→gate loop that already drives feature
experiments.

This is powerful and, done naively, **actively dangerous**. Discovery does not merely
automate Level 1; it introduces failure modes that a human-specified regime does not have.
The entire value of this spec is that it delivers discovery **while making these failure
modes structurally impossible**, not merely discouraged. If we cannot gate them strictly,
we should not ship discovery at all — a false conditional edge is worse than no edge,
because it looks rigorous.

### The traps (each MUST be structurally prevented, not just avoided by convention)

- **T1 — Double-dipping / outcome leakage.** Fitting a regime on the same data that then
  judges the conditional edge fits the regime *to the outcome*. This is the single most
  dangerous trap: it manufactures significance from noise and is invisible in the result.
- **T2 — Unbounded search / data dredging.** An agent can generate thousands of candidate
  regimes. "Search until something conditions well" guarantees false positives unless the
  full search is counted in the multiple-testing correction — including the losers.
- **T3 — Fitted-boundary degrees of freedom.** A regime's thresholds / HMM parameters /
  cluster boundaries are themselves fitted. A conditional test that ignores this
  under-charges for complexity and over-reports significance.
- **T4 — Selection after peeking.** Choosing *which* regime or *which* bucket to report
  after seeing results (post-hoc cherry-picking) invalidates naive p-values; selection
  must be accounted for (selective inference / data-snooping-robust methods).
- **T5 — Silent survivorship.** Dropping the regimes that failed, so the reported
  denominator of the correction is smaller than the true search, understates false
  discovery. Every candidate tried MUST be recorded and counted.
- **T6 — Non-reproducible search.** If a discovery run cannot be reproduced, its
  multiple-testing accounting cannot be audited, so its verdicts cannot be trusted.

### The core defense: nested, pre-registered, search-aware, fail-closed

1. **Nested data segregation (defeats T1/T3).** Regime discovery and detector fitting occur
   **only** on inner training folds; the outer holdout that judges the conditional edge is
   never seen during discovery or fitting. A discovered detector is then applied *causally*
   out-of-sample to produce labels for the edge test (inherits 005 FR-004/017/018).
2. **Pre-registered, bounded search space (defeats T2/T5).** The regime search space and a
   per-cycle discovery budget are declared before the run; every candidate evaluated is
   persisted with provenance so the multiple-testing denominator is the *true* count.
3. **Search-aware error control (defeats T2/T4).** The FDR family includes every
   (signal × candidate-regime × bucket) test actually run — never just the survivors — and
   the design SHOULD support a data-snooping-robust check (e.g. a White Reality Check /
   Hansen SPA-style bootstrap over the full candidate set) as a stronger guard where
   feasible.
4. **Fail-closed everywhere (defeats T1–T5 residue).** No valid disjoint-data p-value → no
   survival. Low-power bucket → no survival. Unrecorded candidate → the run is invalid.
5. **Reproducible, audited runs (defeats T6).** Discovery is seeded and deterministic; the
   full candidate ledger is inspectable.
6. **Expressiveness that scales with data, not a free-for-all (defeats T2/T4 at the source).**
   The agent's hypothesis class is deliberately tiered so honest inference is always
   possible:
   - **Default — continuous-interaction tests.** The gradient question ("does the edge scale
     with this variable?") is answered by a single interaction coefficient/p-value — cheap,
     honest, no bucket search.
   - **Structural — bounded compositional grammar.** Compositions are drawn from a
     pre-registered primitive library (bounded *M*) up to a max composition depth (*K*),
     making the search space finite and enumerable so the FDR denominator is exact.
     Compositionality is where *2ᴹ* explosions live, so depth *K* is a first-class,
     recorded cap.
   - **Expressive — free-form, unlocked only with fresh-holdout validation.** Arbitrary
     free-form expressions are permitted for the agent *only* when validated on a genuinely
     independent, purged holdout (which large datasets make affordable). Free-form without
     fresh-holdout validation is prohibited, because no correction can rescue an unbounded,
     data-reusing search.

## Clarifications

### Session 2026-07-06

- Q: Is the Reality-Check/SPA bootstrap required for v1, or is flat FDR over the full
  realized family sufficient with the bootstrap as a fast-follow? → A: **FDR-over-full-family
  for v1; bootstrap as fast-follow.** Pre-registration + counting every candidate (including
  losers) is already honest and conservative at v1's capped search volumes; the bootstrap
  must land before search budgets are raised. Mirrors 005's flat-BH-first decision.
- Q: Which signals does discovery test conditional edges against in v1? → A: **Active
  feature signals now, with `signal_source` a declared, pluggable field of the
  pre-registered search space** (option D). The three sources form a maturity ladder —
  features (v1, cheap, enumerable, works with flat FDR) → model prediction edges (once a
  production model exists; native to the conditional core) → strategy backtests (only
  after the bootstrap fast-follow lands, since equity-curve inference needs it). Each
  rung is configuration, not redesign, and the declared source cannot become a hidden
  researcher degree of freedom.
- Q: How are "eras" defined for cross-era trust grading? → A: **Walk-forward folds,
  pluggable** — the grading scheme is a declared `grading_scheme` field of the discovery
  configuration (same pattern as `signal_source`), with walk-forward temporal folds as the
  v1 default and declared market-structure eras / hybrids as swappable alternatives. One
  rule is enforced by the grading INTERFACE, not left to implementations: **only data
  genuinely after the discovery window counts as confirmation** (the probation window is
  the first walk-forward fold; the grade accrues as scheduled re-tests pass). Backward
  era-slices are permitted but labeled *descriptive* — the regime's boundaries saw that
  data, so they can never inflate the trust grade. Honest per-fold re-discovery (full
  nested walk-forward) is an optional deep-validation mode, not the default.
- Q: Is the universe-quality filter (test tickers, asset types) in 006's scope? → A:
  **Pluggable filter interface; not a blocker** — 006 defines a `universe_filter`
  interface that accepts new filter types over time, and ships the minimal built-ins
  (exchange test-ticker exclusion + `stocks.asset_type` selection) so real-data discovery
  is never blocked waiting on the full universe-quality feature. Richer filter types
  (liquidity tiers, market-cap floors, listing age) land later as plug-ins through the
  same interface — including an explicit `passthrough` (identity) filter for
  deliberately unfiltered runs. The chosen filter chain is always recorded in the
  search-space pre-registration: the universe can never be a hidden researcher degree
  of freedom. The default chain is the minimal quality filters; passthrough is a
  declared, recorded choice, never a silent fallback.
- Q: Which expressiveness tiers ship in v1? → A: **All three tiers** — continuous-
  interaction, bounded grammar, AND the expressive tier (free-form expressions and
  sandboxed detector-function candidates such as HMM/clustering) gated by fresh-holdout
  validation. Consequence accepted: v1 builds the detector-leaf sandbox runtime, the
  fresh-holdout data-reserve machinery, degeneracy/stability checks, and fitted-parameter
  (T3) accounting. The plan MUST sequence the tiers as independently shippable increments
  (interaction → grammar → expressive) so the guardrail core is validated before the
  most dangerous tier activates.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Nested Discovery That Cannot See the Holdout (Priority: P1)

The agent discovers/fits regimes using only inner training data, then the conditional edge
is judged on an outer holdout the discovery process never touched. The nesting is enforced
by the framework, not by the agent's good behavior.

**Why this priority**: This is the load-bearing guardrail (T1/T3). Without it, every other
part of discovery produces confident nonsense. It is the MVP: even discovering one regime
under honest nesting is the whole point.

**Independent Test**: On a dataset where a regime's boundary is fit on a leaked (holdout-
touching) split vs. a properly nested split, verify the nested path yields no spurious
significance while the leaked path (used only as a negative demonstration in tests) would.

**Acceptance Scenarios**:

1. **Given** a discovery run, **When** regimes are discovered and detectors fitted, **Then**
   the framework guarantees — and records — that no outer-holdout observation was used in
   discovery or fitting.
2. **Given** a fitted detector, **When** it labels the outer holdout, **Then** labels are
   produced causally (each label at *t* uses only data ≤ *t*), reusing 005's causality
   enforcement.
3. **Given** an attempt to discover a regime using outer-holdout data, **When** the run
   executes, **Then** the framework refuses/aborts the run rather than producing a verdict.

---

### User Story 2 — Search-Aware Multiple-Testing That Counts the Losers (Priority: P1)

When the agent evaluates many candidate regimes, the error control accounts for the *entire*
search — every candidate tried, not just the ones reported — so a lucky bucket cannot be
promoted by being cherry-picked from a large search.

**Why this priority**: This is the second load-bearing guardrail (T2/T4/T5). Discovery's
whole risk is search volume; if the correction ignores search volume, discovery is a
false-positive generator.

**Independent Test**: Run discovery with N candidate regimes over pure-noise labels; verify
zero survive after correction, and that the recorded test count equals N×(signals×buckets),
not the survivor count.

**Acceptance Scenarios**:

1. **Given** a discovery cycle evaluating C candidate regimes across S signals and B
   buckets, **When** FDR is applied, **Then** the family size reflects the full C×S×B tests
   actually performed.
2. **Given** candidates that failed, **When** results are recorded, **Then** every failed
   candidate is persisted in the ledger and counted in the denominator (no silent drops).
3. **Given** a post-hoc attempt to report only the best bucket, **When** verdicts are
   computed, **Then** the selection is accounted for (the reported significance reflects
   selection over the search, not a single naive test).

---

### User Story 3 — Principle-Seeded Agentic Proposal of Regimes (Priority: P2)

The agent proposes regime hypotheses as a new experiment type, seeded by catalog principles
(`regime-detection-hmm`, `hurst-exponent-regime`, `low-volatility-anomaly`, …), generates
the detection code via the existing sandboxed codegen, and registers each as a
`RegimeDefinition` (Level 3), within a declared, bounded search space and budget.

**Why this priority**: This is the "agentic" payoff, but it is only *safe* once Stories 1
and 2 exist, so it follows them.

**Independent Test**: Trigger a discovery proposal for a given principle and verify it emits
a bounded set of candidate `RegimeDefinition`s with generated, sandboxed detection code and
recorded provenance to the seeding principle.

**Acceptance Scenarios**:

1. **Given** a principle implying a regime (e.g. Hurst trending/mean-reverting), **When**
   the agent proposes, **Then** it registers a candidate regime whose definition references
   the seeding principle and whose detector is generated within the security sandbox
   (whitelisted imports only).
2. **Given** a declared discovery budget of B candidates per cycle, **When** the agent
   proposes, **Then** it never exceeds B, and the budget is recorded with the run.
3. **Given** a generated detector, **When** it is registered, **Then** it is stored in the
   DB and exported to JSON like any `RegimeDefinition` (Database-First), tagged as
   machine-generated with full provenance.

---

### User Story 4 — Negative Control: Discovery Finds Nothing in Noise (Priority: P1)

The system ships with an enforced negative-control test: run the full discovery+evaluation
loop against synthetic pure-noise data and confirm that **nothing** survives. This is a
standing guarantee, not a one-time check.

**Why this priority**: It is the empirical proof that the guardrails work end-to-end. A
discovery system that cannot demonstrably find nothing in noise must not be trusted to find
something in signal.

**Independent Test**: The negative-control suite runs the loop on seeded noise and asserts
zero survivors across repeated seeds.

**Acceptance Scenarios**:

1. **Given** synthetic data with no conditional structure, **When** the full discovery loop
   runs, **Then** zero regimes/edges survive correction, across multiple seeds.
2. **Given** synthetic data with signal in exactly one planted regime, **When** the loop
   runs, **Then** it recovers that regime and rejects the decoys.

---

### User Story 5 — Reproducible, Auditable Discovery Runs (Priority: P2)

Every discovery run is seeded and reproducible, and exposes a full ledger of candidates
tried, data splits used, tests counted, and verdicts — inspectable via CLI, MCP, and UI.

**Why this priority**: Auditability is what makes the multiple-testing accounting
trustworthy (T6). It is required for trust but follows the core guardrails.

**Independent Test**: Re-run a discovery run with the same seed and inputs; verify identical
candidate ledger and verdicts.

**Acceptance Scenarios**:

1. **Given** a completed discovery run, **When** a reviewer inspects it, **Then** they see
   every candidate regime, its data segregation, its test count, and its verdict.
2. **Given** the same seed and inputs, **When** the run is repeated, **Then** the ledger and
   verdicts are identical.

---

### User Story 6 — Cross-Era Grading and a Diagnostics Ledger for What We Couldn't Test (Priority: P2)

A discovered edge is promoted on **one** hard, fail-closed, out-of-sample holdout gate — but
its *trust* is graded by how widely it survives across time eras and dataset versions.
Edges that pass the gate but are cross-era-weak are promoted *flagged as regime-limited*
with tighter probation, so transient alpha is captured without being trusted like a durable
edge. Separately, every limit the search hits — budget exhausted, depth capped, and
especially min-sample refusals — is recorded in a diagnostics ledger, split into
sample-dependent learnings (re-test on a new dataset) and structural learnings (accumulate).

**Why this priority**: This is how the system stays honest *and* useful across the move from
a tiny dev dataset to decades of production data — it neither over-trusts a one-era edge nor
throws away legitimate transient alpha, and it turns the search's negative space into a
learning signal (e.g. "we keep wanting VIX regimes but VIX isn't ingested").

**Independent Test**: Run discovery on data with an edge present in only one era; verify it
passes the hard gate, is flagged regime-limited, and that a min-sample refusal on a rare
regime is recorded as a sample-dependent (re-testable) diagnostic, not a structural one.

**Acceptance Scenarios**:

1. **Given** an edge that passes the honest holdout gate but survives in only one era,
   **When** it is promoted, **Then** it is flagged regime-limited/decaying and placed on
   tighter probation, not trusted as durable.
2. **Given** an edge that survives across multiple eras and dataset versions, **When** it is
   graded, **Then** it receives a higher trust grade than a single-era survivor.
3. **Given** a candidate refused for insufficient sample, **When** it is recorded, **Then**
   the diagnostic is tagged sample-dependent (with the quantitative reason, e.g. "18 of 100
   required") so it is re-evaluated — not inherited — on a larger dataset.
4. **Given** a structural limit (e.g. a required input is not ingested, or depth-3
   conjunctions are always under-powered on this dataset), **When** it is recorded, **Then**
   it is tagged structural and accumulates as a data-priority / search-design signal.

### Edge Cases

- **Degenerate regimes**: a discovered regime that assigns nearly all observations to one
  bucket (or splits pathologically) MUST be flagged and excluded, not evaluated as if
  informative.
- **Unstable detectors**: a detector whose labels flip frequently or are unstable across
  seeds MUST be treated as low-confidence and cannot survive on that basis alone.
- **Discovery-signal entanglement**: if a proposed regime is derived using the target
  signal/outcome (not just market-state inputs), the run MUST treat it as maximally
  suspect and subject it to the strictest segregation, or reject it.
- **Budget exhaustion mid-cycle**: partial searches MUST still record the true number of
  tests attempted so accounting stays honest.
- **Interaction with 005**: discovered regimes flow through 005's slicing/reconciliation and
  inherit its low-power/undefined handling, including persistence (episodes, not flicker).
- **Composition-depth explosion**: without a hard cap on depth *K*, compositional search
  grows ~2ᴹ; the cap and the true realized search size MUST be recorded every run.
- **Uncomputable proposals**: a proposed regime whose inputs are not in the dataset (e.g.
  VIX when it isn't ingested) MUST be rejected at proposal time via the data-availability
  inventory (spec 004 discovery) and recorded as a structural diagnostic, not attempted.
- **Non-stationarity across the data expansion**: adding decades spans different macro eras;
  more data tests stationarity rather than merely adding power, so cross-era survival is a
  grading dimension, not an afterthought.
- **Dev-scale is plumbing only**: on a tiny dataset the loop's real job is to prove the
  machinery (segregation, ledgers, fail-closed) via the negative control; meaningful
  discovery emerges only at production data scale, and initial "not enough data" verdicts
  are correct behavior, not a defect.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-101**: Regime discovery and detector fitting MUST occur exclusively on inner
  training folds; the outer holdout used to judge the conditional edge MUST never be
  accessed during discovery or fitting (nested segregation).
- **FR-102**: The framework MUST enforce and record data segregation; a run that cannot
  prove segregation MUST be marked invalid and produce no surviving verdicts.
- **FR-103**: Discovered detectors MUST label out-of-sample data causally, inheriting the
  no-lookahead guarantee (005 FR-004/017/018).
- **FR-104**: The multiple-testing family MUST include every (signal × candidate-regime ×
  bucket) test actually performed, counting failed candidates; silent survivorship is
  prohibited.
- **FR-105**: The discovery search space and per-cycle candidate budget MUST be declared
  before the run and persisted with it.
- **FR-106**: Every candidate regime evaluated MUST be persisted in an auditable ledger with
  provenance (seeding principle or discovery method, parameters, data splits, test count,
  verdict).
- **FR-107**: The system MUST honor fail-closed: no valid disjoint-data p-value, or a
  low-power/degenerate bucket, MUST yield no survival.
- **FR-108**: v1 error control is **FDR over the full realized family** (every candidate
  counted, including losers), which pre-registration and bounded budgets make honest. A
  data-snooping-robust selection check (Reality Check / SPA-style bootstrap over the full
  candidate set) is a REQUIRED fast-follow that MUST land before per-cycle search budgets
  are raised beyond v1 defaults; the design MUST leave a seam for it (the candidate ledger
  already retains everything the bootstrap needs).
- **FR-108a**: The conditional-edge **signal universe** MUST be a declared, pre-registered
  field of the search space (`signal_source`). v1 ships `features` (active feature
  signals — cheap, enumerable, flat-FDR-compatible); `model_predictions` (requires a
  trained production model; consumes the 005 conditional core natively) and
  `strategy_backtests` (requires the FR-108 bootstrap; equity-curve inference is not a
  clean paired test) are later rungs enabled by configuration, in that order.
- **FR-109**: Agentic proposal MUST be a first-class experiment type (`regime_discovery`)
  reusing the existing sandboxed codegen (whitelisted imports only) and the discovery step.
- **FR-110**: Discovered/generated `RegimeDefinition`s MUST be stored in the DB and exported
  to JSON, tagged machine-generated with provenance (Database-First).
- **FR-111**: Discovery runs MUST be seeded, deterministic, and reproducible from recorded
  inputs.
- **FR-112**: The system MUST ship a standing negative-control test asserting zero survivors
  on pure-noise data across seeds, run in CI.
- **FR-113**: Degenerate or unstable detectors MUST be detected and excluded from surviving
  verdicts.
- **FR-114**: A regime proposed using the target signal/outcome (not solely market-state
  inputs) MUST be flagged as entangled and either subjected to strictest segregation or
  rejected.
- **FR-115**: All discovery operations and the candidate ledger MUST be reachable via CLI,
  MCP, and UI (FR-042 parity).
- **FR-116**: New modules MUST emit observability spans via `gefion.observability` with
  parent-context propagation (Constitution: Observability).
- **FR-117** *(documentation, definition of done)*: User-facing docs MUST be updated in the
  same PR (see **Documentation Impact**) and `tests/test_docs_drift.py` MUST pass.
- **FR-118**: The agent's hypothesis class MUST be tiered: **continuous-interaction tests as
  the default gradient mechanism**; **bounded compositional grammar** (pre-registered
  primitive library of size *M*, max composition depth *K*) for structural search; and
  **free-form expressions only when validated on a fresh, purged, independent holdout**.
- **FR-118a**: All three tiers ship in v1, sequenced as independently shippable increments
  (interaction → grammar → expressive). The expressive tier comprises free-form
  expressions AND sandboxed detector-function candidates (HMM, clustering — the 005
  FR-019a leaf, whose execution runtime this feature builds), both admissible only under
  fresh-holdout validation. The fresh-holdout reserve MUST be a declared, budgeted data
  block recorded in the run's pre-registration and never reused across runs without
  re-declaration.
- **FR-119**: Free-form agentic expressions without fresh-holdout validation MUST be
  prohibited; the framework MUST NOT emit a p-value for an unbounded, data-reusing search.
- **FR-120**: Composition depth *K* and the realized search-space size MUST be capped,
  declared, and recorded each run; the FDR family MUST reflect the true realized size.
- **FR-121**: Regime proposals whose inputs are unavailable in the target dataset MUST be
  rejected at proposal time using the spec-004 data-availability inventory, and recorded as
  structural diagnostics rather than attempted.
- **FR-121a**: The discovery symbol universe MUST be selected through a **pluggable
  `universe_filter` interface** that accepts new filter types over time. v1 ships minimal
  built-in filters — exchange test-ticker exclusion (e.g. the NASDAQ ZVZZT family) and
  `stocks.asset_type` selection (common stock by default) — so real-data discovery is not
  blocked on the full universe-quality feature; liquidity, market-cap, and listing-age
  filters are later plug-ins through the same interface, and an explicit `passthrough`
  filter type permits deliberately unfiltered runs. The active filter chain MUST be
  recorded in the search-space pre-registration; the minimal quality chain is the default,
  and passthrough MUST be an explicit declaration, never a silent fallback.
- **FR-122**: Promotion MUST be two-tier — a single hard, fail-closed, out-of-sample holdout
  gate decides admit/reject; **cross-era and cross-dataset survival grades trust/rank among
  admitted edges only** and MUST NOT relax the hard gate.
- **FR-122a**: The trust-grading scheme MUST be a declared, pluggable field of the discovery
  configuration (`grading_scheme`), defaulting to **walk-forward temporal folds** in v1.
  The grading interface MUST enforce, for every scheme: (a) only data genuinely after the
  discovery window counts as confirmation — the probation window is the first fold and the
  grade accrues via scheduled re-tests; (b) backward applications of a discovered regime
  are recorded as descriptive only, never as confirmations (the regime's fitted boundaries
  saw that data); (c) fold length / era boundaries are declared, versioned configuration.
  Full nested per-fold re-discovery is an optional deep-validation mode.
- **FR-123**: An admitted edge that is cross-era-weak MUST be flagged regime-limited/decaying
  and placed on tighter probation (reusing the existing probation mechanism), so transient
  alpha is captured but not trusted as durable.
- **FR-124**: The system MUST maintain a **diagnostics ledger** of every limit hit (budget,
  depth, min-sample refusal, uncomputable proposal), each tagged **sample-dependent**
  (re-evaluated on a new dataset) or **structural** (accumulated), with quantitative reasons.
- **FR-125**: Every discovery artifact (search space, candidate, verdict, diagnostic) MUST
  carry dataset provenance and descriptive metadata (005 FR-023/024); sample-dependent
  diagnostics MUST NOT be inherited across dataset versions without re-evaluation.
- **FR-126**: Discovered regimes MUST inherit 005's persistence handling — favoring
  contiguous episodes — and record realized persistence as a gradable property.

### Key Entities *(include if feature involves data)*

- **RegimeSearchSpace**: the declared, bounded universe of candidate regimes a discovery run
  may explore — the primitive library (size *M*), the max composition depth (*K*), the
  expressiveness tier in use, the **signal_source** (which signal universe conditional
  edges are tested against: `features` in v1; `model_predictions` and `strategy_backtests`
  as later rungs), the **universe_filter** (declared symbol-universe selection: v1 excludes
  test tickers and filters by asset type; richer filters plug in later), and the per-cycle
  budget. Pins down the denominator of error control.
- **DiscoveryDiagnostics**: the ledger of limits the search hit — budget/depth exhaustion,
  min-sample refusals, uncomputable proposals — each tagged sample-dependent vs structural
  with a quantitative reason and dataset provenance. The negative-space learning signal.
- **TrustGrade**: the cross-era / cross-dataset survival grade attached to an admitted edge
  (distinct from the hard admit/reject gate), including the regime-limited/decaying flag and
  probation tightness. Computed by a pluggable **GradingScheme** (declared per run;
  v1 default: walk-forward temporal folds). The grade ACCRUES from forward-in-time
  confirmations (probation window = first fold; scheduled re-tests = later folds);
  backward era-slices are stored as descriptive context only and never counted.
- **DiscoveredRegime**: a `RegimeDefinition` (from 005) whose computation is machine-
  generated (Level 2/3), carrying provenance (seeding principle or method), fitted
  parameters, and the training folds it was fit on.
- **RegimeDiscoveryRun**: one discovery execution — its seed, search space, budget, data
  segregation, and link to the candidate ledger.
- **CandidateLedgerEntry**: one evaluated candidate — its detector, data splits, per-test
  results, whether counted in the family, and verdict. The audit trail that makes error
  control honest.

## Documentation Impact *(mandatory — definition of done)*

- **docs/REGIMES.md** (created in 005) — add the discovery section: the threat model (T1–T6),
  the nested/pre-registered/search-aware/fail-closed defense, the expressiveness tiers
  (continuous-interaction → bounded grammar → free-form-with-fresh-holdout), two-tier
  promotion (hard gate vs cross-era grading), and how to read both the candidate and
  diagnostics ledgers.
- **README.md** — add the `regime discover` command(s) to the CLI Reference; note discovery
  under Autonomous Experiments.
- **docs/USER_GUIDE.md** — full CLI reference for the `regime discover` group and options.
- **docs/DATA_DICTIONARY.md** — the search-space, discovery-run, and candidate-ledger tables.
- **docs/MCP_WORKFLOWS.md** — the mirrored MCP tools for discovery and ledger inspection.
- **specs/004-autonomous-experiments** cross-reference — record `regime_discovery` as a new
  experiment type and how its stricter gate differs from standard experiments.
- **.claude/commands/gefion-learn.md** — extend the experiments/regime curriculum with a
  discovery aside emphasizing the traps (this is a teachable, high-value rigor lesson).
- **tests/test_docs_drift.py** — must pass for all new commands and MCP tools.

## Automation *(consider)*

- **Proposed skill**: `gefion-regime-discover` — **defer**. Discovery is invoked within the
  existing experiment-cycle flow; a dedicated skill is only warranted if a distinct recurring
  operator workflow emerges (e.g. scheduled discovery sweeps with ledger review). Revisit
  after Story 4 proves the loop is trustworthy.
- **Rationale**: Do not automate a loop until its negative controls demonstrably pass.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-101**: On synthetic pure-noise data, the full discovery loop promotes **zero** regimes
  across ≥20 seeds (empirical false-discovery control), enforced in CI.
- **SC-102**: On synthetic data with signal planted in exactly one regime among D decoys, the
  loop recovers the true regime and rejects all decoys in ≥95% of seeded runs.
- **SC-103**: 100% of discovery runs record a candidate ledger whose counted test family
  equals the true number of (signal × candidate × bucket) evaluations — audited, no silent
  drops.
- **SC-104**: 100% of discovery runs provably use no outer-holdout data during discovery or
  fitting (segregation assertion passes or the run is marked invalid).
- **SC-105**: Discovery runs are byte-reproducible from seed + inputs (identical ledger and
  verdicts on re-run).
- **SC-106**: No degenerate, unstable, or entangled regime is ever among surviving verdicts.
- **SC-107**: All discovery operations are reachable and consistent across CLI, MCP, and UI,
  and `tests/test_docs_drift.py` passes.
- **SC-108**: No free-form agentic expression is ever assigned a p-value without fresh-holdout
  validation; every run records its expressiveness tier, primitive-library size, and depth
  cap, and the FDR family matches the realized search size.
- **SC-109**: Every admitted edge carries a trust grade; on data with a single-era edge, the
  edge is admitted, flagged regime-limited, and placed on tighter probation in 100% of runs.
- **SC-110**: 100% of limit-hits are recorded in the diagnostics ledger and correctly tagged
  sample-dependent vs structural; sample-dependent diagnostics from a small dataset are
  re-evaluated (not inherited) when the same search runs on a larger dataset.
