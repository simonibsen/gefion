# Feature Specification: Regime Slicing — Conditional Evaluation Across Market/Sector/Asset States

**Feature Branch**: `005-regime-slicing`
**Created**: 2026-07-03
**Status**: Draft
**Input**: User description: "Being able to describe the state of the market, industry, etc. and add that as a dimension to test against — so we can discover and *statistically prove* when a signal or strategy has an edge, rather than only whether it has one on average."

## Motivation: Why Not Just a Computed Feature? *(mandatory context)*

The obvious objection to this feature is that a regime is "just another number," so
it could be delivered as a computed feature (e.g. `vol_regime ∈ {calm, normal, stressed}`)
fed to the model alongside signal features, letting a tree model learn the interactions
implicitly. That objection is worth answering head-on, because if there were no
functional difference, this feature would be redundant.

**A regime feature changes what the model *predicts*. Regime slicing changes what we can
*know and prove*.** They act on different layers:

| | Regime as a computed feature | Regime slicing |
|---|---|---|
| Layer | Prediction (model output) | Inference & evaluation (knowledge claims) |
| Statistical unit | **One** aggregate holdout p-value | **One p-value per regime**, entered into FDR |
| What you learn | "Feature set helped, net" | *A map* of where the edge lives |
| Interpretability | Latent in model weights (reverse-engineer via SHAP) | Explicit, legible, testable claim |
| Rare regimes | Silently under-weighted; sample hidden | Sample-per-bucket surfaced; low-power claims refused |
| Decision-layer use | Buried in the model's math | First-class label strategies/analysis can key on |

Three consequences make the difference concrete:

1. **The cancellation problem.** A signal that pays in a trending regime but bleeds in a
   choppy one can net to ~zero and *fail* an aggregate test — leading to "momentum
   doesn't work here." Slicing reveals a real, exploitable edge concentrated in one
   regime and masked by noise elsewhere. The aggregate approach *structurally cannot*
   surface an edge that nets flat.
2. **Conditional significance.** However good the model, its holdout p-value is
   aggregate. A regime *feature* makes the prediction conditional; it never makes the
   *statistical test* conditional. Slicing is the only mechanism that makes the gate
   itself emit a conditional verdict ("significant in high-dispersion regimes, p=0.01;
   noise elsewhere, p=0.42").
3. **Honest power.** If a regime is 5% of history, a model quietly makes do and you never
   see your effective sample. Slicing forces "do I have enough observations in this
   bucket to make a claim?" — and lets the system *refuse to claim* rather than report a
   lucky Sharpe on twelve days.

**They are complementary, not competing.** The clean model: *slicing is discovery and
inference* (find where an edge exists and prove it honestly); *a regime feature is
exploitation* (the model uses the conditioning to predict better). In practice you slice
first to discover that an edge is concentrated in a regime — and *that finding* is what
justifies building the interaction feature. The slice points; the feature exploits. This
spec delivers the slice.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Describe and Compute a Regime (Priority: P1)

A researcher describes the state of the market/sector/asset as a named, time-indexed
regime: a computation over available data plus a bucketing rule that maps each date (and,
for scoped regimes, each entity) to a discrete regime label. The system computes and
stores those labels so they can be attached to any downstream analysis.

**Why this priority**: Nothing else in the feature exists without a regime object to slice
by. This is the foundational MVP and is independently valuable — even just producing and
inspecting labels answers "what regime is the market in now, and how often are we in each?"

**Independent Test**: Define a volatility regime over the existing OHLCV data, compute its
labels, and verify the output is a queryable time series assigning exactly one label per
(date[, entity]) with sane bucket frequencies and no future information used.

**Acceptance Scenarios**:

1. **Given** OHLCV history, **When** the researcher defines a market-scoped volatility
   regime with three buckets (calm/normal/stressed) via rolling realized volatility
   terciles, **Then** the system computes a label for every date with sufficient lookback
   and reports the fraction of history in each bucket.
2. **Given** a regime definition scoped to `sector`, **When** it is computed, **Then**
   labels are assigned per (date, sector) so two stocks in different sectors on the same
   day can be in different regimes.
3. **Given** a regime whose computation needs N days of lookback, **When** labels are
   computed, **Then** the first N days (where the label is undefined) are marked
   `undefined`, not silently forward/back-filled.
4. **Given** any regime definition, **When** labels are computed, **Then** each label at
   time *t* depends only on data available at or before *t* (no lookahead), and this is
   verified by construction.

---

### User Story 2 — Regime-Sliced Backtest Reporting (Priority: P1)

A researcher runs an existing backtest and additionally requests results *sliced by* a
regime. The backtest executes exactly as it does today; the system then attributes each
dated equity point and each trade to its regime label and reports per-regime metrics
(return, Sharpe, drawdown, win rate, profit factor, trade count) alongside the aggregate.

**Why this priority**: This is the first place slicing pays off empirically — it directly
answers "does trend following pay only in high-volatility regimes?" It reuses the existing
equity-curve and trade output and the existing metric functions, so it is high-value and
low-risk.

**Independent Test**: Run one backtest with `--by-regime <definition>` and verify the
report contains per-regime metric blocks whose union reconciles to the aggregate, each
annotated with its sample size.

**Acceptance Scenarios**:

1. **Given** a completed backtest with a dated equity curve and trades, **When** the
   researcher slices by a market volatility regime, **Then** the system reports Sharpe,
   return, drawdown, win rate, and trade count *within each regime bucket*.
2. **Given** a regime bucket that contains fewer observations than the configured minimum
   for a reliable estimate, **When** results are reported, **Then** that bucket's metrics
   are flagged as low-power (or withheld) rather than presented as a finding.
3. **Given** a sliced backtest, **When** the aggregate and per-regime metrics are compared,
   **Then** trade counts and total return across buckets reconcile to the un-sliced totals
   (no trade double-counted or dropped).
4. **Given** a backtest run without `--by-regime`, **When** it executes, **Then** its
   behavior and output are byte-for-byte unchanged from today (slicing is strictly
   additive and opt-in).

---

### User Story 3 — Regime-Conditional Experiment Verdicts (Priority: P2)

The autonomous experiment framework can evaluate a hypothesis *conditionally*: instead of
one aggregate holdout p-value, it emits a per-regime holdout p-value, and those
per-regime tests are entered into the cycle's Benjamini-Hochberg FDR family so the added
multiple testing is corrected, not hidden. A conditional edge survives only if it clears
FDR as an honest, conditional claim.

**Why this priority**: This is the rigorous payoff and the deepest differentiator from a
regime feature — it makes the statistical gate itself conditional. It depends on Stories 1
and 2, so it follows them.

**Independent Test**: Run a hypothesis known to have signal only in one synthetic regime
and noise elsewhere; verify the system emits distinct per-regime p-values, that the
regime tests are counted in the FDR family, and that only the genuine regime survives.

**Acceptance Scenarios**:

1. **Given** an experiment with a signal that is real only in high-dispersion regimes,
   **When** it is evaluated conditionally, **Then** the system reports a significant
   p-value for that regime and a non-significant p-value for the others.
2. **Given** a cycle in which K experiments are each evaluated across R regimes, **When**
   FDR is applied, **Then** the correction accounts for the K×R tests (or a documented,
   more powerful hierarchical scheme), never for K alone.
3. **Given** a regime bucket below the minimum-sample threshold, **When** conditional
   evaluation runs, **Then** no p-value is emitted for that bucket and, per the
   fail-closed rule, it cannot survive.
4. **Given** conditional evaluation, **When** regime labels are used, **Then** the labels
   over the holdout window are computed with the same no-lookahead guarantee as training,
   so slicing cannot leak future information into the gate.

---

### User Story 4 — Surfaced Across CLI, MCP, and UI (Priority: P3)

Every regime-slicing operation — define, compute, list, inspect, slice-a-backtest, and
read conditional experiment verdicts — is reachable through all three surfaces (CLI, MCP,
UI), consistent with FR-042 ("every operation reachable via CLI, MCP, and UI").

**Why this priority**: Parity matters for adoption but is not required to prove the
concept; it follows the core capability.

**Independent Test**: For a defined regime, exercise define/compute/list/slice through the
CLI, confirm the mirrored MCP tools return equivalent structured output, and confirm the
UI renders per-regime metric blocks.

**Acceptance Scenarios**:

1. **Given** a regime defined via the CLI, **When** it is listed via the MCP tool, **Then**
   the same definition and label coverage are returned.
2. **Given** a sliced backtest, **When** viewed in the UI, **Then** per-regime metrics
   render with sample sizes and low-power flags visible.

---

### Edge Cases

- **Lookahead / leakage**: a regime label that uses information from after time *t*
  contaminates any holdout it touches. Labels MUST be causal; conditional evaluation MUST
  reuse the training-side no-lookahead guarantee.
- **Low power / rare regimes**: buckets below a minimum sample size must be flagged or
  withheld, never reported as findings; in the gate they fail closed.
- **Undefined periods**: dates without enough lookback (or with missing inputs) get an
  explicit `undefined` label and are excluded from per-regime metrics rather than lumped
  into a bucket.
- **Scope mismatch**: slicing an all-stocks backtest by a `sector`-scoped regime must join
  each position to its own sector's regime, not a single market label.
- **Overlapping / conflicting definitions**: two regime definitions can be computed and
  sliced independently; the system does not silently merge them.
- **Multiple-testing explosion**: adding regimes multiplies hypotheses; the FDR family
  MUST grow accordingly so slicing cannot smuggle a lucky bucket past the gate.
- **Reconciliation**: per-regime results must reconcile to aggregates (no double-counted
  or dropped trades at bucket boundaries).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST let a user define a named regime as (a) a computation over
  available data and (b) a bucketing rule mapping each date to a discrete label.
- **FR-002**: System MUST support regime *scope* of at least `market`, `sector`,
  `industry`, and `asset`, assigning labels per (date) or per (date, entity) accordingly.
- **FR-003**: System MUST compute and persist regime labels as a queryable time series,
  with exactly one label (including `undefined`) per (date[, entity]).
- **FR-004**: System MUST guarantee regime labels are causal — each label at time *t*
  depends only on data available at or before *t* — and MUST make this verifiable.
- **FR-005**: Regime definitions MUST be stored in the database and exportable to JSON in
  the repo for version control, mirroring the feature-definition pattern (Constitution
  Principle I, Database-First).
- **FR-006**: System MUST slice an existing backtest's dated equity curve and trades by a
  chosen regime and report per-regime metrics (return, Sharpe, drawdown, win rate, profit
  factor, trade count) computed with the existing metric functions.
- **FR-007**: Backtest slicing MUST be opt-in; a backtest run without slicing MUST be
  unchanged in behavior and output.
- **FR-008**: System MUST annotate every per-regime metric block with its sample size and
  MUST flag or withhold buckets below a configurable minimum-sample threshold.
- **FR-009**: Per-regime results MUST reconcile to the aggregate (trades and total return
  across buckets sum to the un-sliced totals).
- **FR-010**: The experiment framework MUST support conditional evaluation that emits a
  per-regime holdout p-value in addition to (not instead of) the aggregate.
- **FR-011**: Per-regime hypothesis tests MUST be entered into the cycle's FDR family so
  the added multiple testing is corrected; the correction MUST reflect the true number of
  tests (K×R or a documented hierarchical alternative).
- **FR-012**: Conditional evaluation MUST honor fail-closed: a bucket with no valid
  p-value (including low-power buckets) cannot survive.
- **FR-013**: All regime-slicing operations MUST be reachable via CLI, MCP, and UI
  (FR-042 parity), with MCP tools mirroring the CLI.
- **FR-014**: New modules MUST emit observability spans via `gefion.observability`
  (`create_span`/`set_attributes`), with child spans propagating parent context
  (Constitution: Observability).
- **FR-015**: Regime slicing MUST NOT change backtest *execution*; regime-gated strategies
  (turning a strategy on/off by regime) are explicitly out of scope for this feature and,
  if pursued, belong to a separate strategy-input capability.
- **FR-017**: Regime detection/computation MUST be a separate upstream stage that produces
  stored labels (Story 1). The backtest engine MUST remain regime-agnostic — it never
  detects or computes regimes; slicing consumes precomputed labels via a post-run join
  only. This is the single enforcement point for the causality guarantee (FR-004).
- **FR-018**: Bucket boundaries (e.g. volatility terciles, thresholds) MUST be derived
  causally from past-only data, never fit over the whole backtest or holdout window.
  Fitting boundaries over the evaluation window is lookahead and MUST be rejected.
- **FR-016** *(documentation, definition of done)*: The change is not done until user-facing
  docs reflect it in the same PR — see **Documentation Impact** below — and
  `tests/test_docs_drift.py` passes for any new commands and MCP tools.

### Key Entities *(include if feature involves data)*

- **RegimeDefinition**: a named, versioned description of a regime — its scope
  (market/sector/industry/asset), the computation over source data, and the bucketing rule
  (thresholds/method) producing discrete labels. Stored in DB, exported to JSON.
- **RegimeLabel**: the computed time series — one discrete label (or `undefined`) per
  (date[, entity]) for a given RegimeDefinition. The object everything slices against.
- **RegimeScope**: the granularity at which a regime applies (market → all entities on a
  date share one label; asset → per-entity labels).
- **RegimeSlicedResult**: the per-regime breakdown of a backtest or experiment evaluation —
  each bucket's metrics/p-value plus sample size and low-power/undefined flags, guaranteed
  to reconcile to the aggregate.

## Documentation Impact *(mandatory — definition of done)*

Per the project's documentation-as-definition-of-done rule, this PR MUST update:

- **README.md** — add the new CLI commands to the CLI Reference tables; mention regime
  slicing in the backtesting/experiments overview.
- **docs/USER_GUIDE.md** — full CLI reference for the new `regime …` command group and the
  `backtest run --by-regime` option.
- **docs/BACKTESTING.md** — how per-regime metrics are computed, the reconciliation
  guarantee, and the minimum-sample / low-power flagging.
- **New concept doc** (e.g. `docs/REGIMES.md`) — the "why not just a feature" rationale, the
  causal-label requirement, and the conditional-FDR accounting; linked from the docs index.
- **docs/DATA_DICTIONARY.md** — the new regime definition/label tables and columns.
- **docs/MCP_WORKFLOWS.md** — the mirrored MCP tools for regime operations.
- **.claude/commands/gefion-learn.md** — if the learning path changes, add a short regime
  module/aside (the curriculum already threads CLI/MCP/UI and links technical terms).
- **tests/test_docs_drift.py** — must pass: every new command and MCP tool referenced in
  docs must exist and be documented.

## Automation *(consider)*

- **Proposed skill**: `gefion-regime` — likely **not needed initially**. Regime definition
  and slicing are discrete operations that fit the existing CLI/MCP surfaces; a dedicated
  slash command is only worth it if a recurring multi-step workflow emerges (e.g.
  define → compute → slice a standard strategy panel → report). Revisit after Story 2.
- **Rationale**: Avoid premature automation; the value is the capability, not a wrapper.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A researcher can define, compute, and inspect a market/sector/asset regime and
  see per-bucket coverage of history — end to end — in under 5 minutes, without editing code.
- **SC-002**: Any existing backtest can be sliced by a regime with a single added flag, and
  per-regime metrics reconcile to the aggregate within rounding on 100% of runs.
- **SC-003**: On a synthetic dataset with signal in exactly one regime, conditional
  evaluation identifies that regime as significant and rejects the others, with the FDR
  family reflecting all K×R tests.
- **SC-004**: No regime bucket below the minimum-sample threshold is ever reported as a
  surviving finding (0 false low-power promotions).
- **SC-005**: 100% of regime-slicing operations are reachable and consistent across CLI,
  MCP, and UI, and `tests/test_docs_drift.py` passes.
- **SC-006**: Backtests run without slicing show zero behavioral or output change
  (regression-tested against pre-feature baselines).

## Future Direction: Agentic Regime Discovery (→ Spec 006)

This spec deliberately covers **Level 1 — specified regimes**: a human describes the
regime computation and buckets, and the system slices and conditionally evaluates against
them. Two further levels are intentionally **out of scope here** and are the subject of a
dependent follow-on spec (`006-agentic-regime-discovery`):

- **Level 2 — Discovered (unsupervised)**: the regime structure is inferred from data
  (HMM states, clustering on market-state features, changepoint detection) rather than
  hand-specified.
- **Level 3 — Agentic**: the autonomous experiment agent *proposes* regime hypotheses
  (seeded by catalog principles such as `regime-detection-hmm`, `hurst-exponent-regime`),
  generates the detection code, computes causal labels, and tests conditional edges — the
  same propose→codegen→gate loop the agent already runs for features, pointed at regimes.

**Why this is a separate spec, not a later story here.** Discovery is not "Level 1
automated." It multiplies researcher degrees of freedom and introduces failure modes that
do not exist when a human specifies the regime — double-dipping (fitting the regime to the
same data that judges the edge), unbounded search (thousands of candidate regimes →
data dredging), and the regime boundary itself being a fitted parameter. Letting an agent
discover regimes safely requires a **strictly stronger** gate than 005 provides. That
gate cannot be designed correctly until the causal-label machinery, conditional evaluation,
and reconciliation guarantees of 005 exist. Therefore 006 **depends on** 005.

**Design constraint this places on 005**: keep `RegimeDefinition` an open abstraction whose
computation may be either human-authored (Level 1) or machine-generated (Levels 2–3), and
keep label computation the single causality-enforcement point (FR-004/017/018) so a
discovered detector is subject to the same no-lookahead guarantee as a specified one.
