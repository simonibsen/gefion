# Research: Generated Market-Level Features with an Owner Gate

No NEEDS CLARIFICATION markers remained in Technical Context; this document
records the design decisions and the alternatives weighed, grounded in the
shipped 011/013 machinery.

## R1 — Where candidates live: separate table, not a status value

**Decision**: New `market_function_candidates` table. Generated bodies never
touch `feature_functions` until approval; promotion inserts through the
existing `upsert_feature_function` + definition-pairing path
(`scope='market'`, active).

**Rationale**: The gate invariant is "a pending or rejected candidate cannot
produce a stored value through ANY path." `derive --series all`, explicit
derive, export/import, and UI listings all enumerate `feature_functions`;
if candidates lived there under a `pending_review` status, every one of
those paths (and every future one) would need a correct review-state filter
forever — one miss executes unreviewed generated code against production
data. A separate table makes the unsafe state unrepresentable and leaves
every existing consumer untouched (regression parity SC-1406 by
construction). It also matches the house ledger pattern (discovery
candidates live in their own ledger and only admitted artifacts become
regimes).

**Alternatives considered**: (a) `feature_functions.status='pending_review'`
— rejected per above (fail-open risk, forever). (b) Git-side review (PR per
candidate) — rejected: bodies are DB-resident by Constitution I; git exports
are backups, and a PR flow would invert that.

## R2 — Composite mode discriminator: input shape, not a new scope value

**Decision**: Composite market functions keep `scope='market'`; their
`inputs` JSON declares `{"series": ["vix", "breadth_sma200", ...]}` instead
of the cross-section contract's `{"features": [...]}`. The derive
orchestrator dispatches on input shape: series-inputs → new
`run_composite_function`; otherwise → existing `run_market_function`.

**Rationale**: A composite IS a market-level function (one value per date,
macro home); its only difference is what it reads. Extending the
`feature_functions.scope` CHECK would be a second schema change with no
consumer that needs it — every listing/lifecycle surface treats both kinds
identically, and the executor is the only place that cares. YAGNI
(Constitution VI) and one less DDL approval.

**Alternatives considered**: new `scope='composite'` CHECK value — rejected
(schema change without a consumer; lifecycle surfaces don't branch on it).
A separate composite table — rejected (composites ARE market functions;
duplicate lifecycle).

## R3 — Composite execution + ordering

**Decision**: `run_composite_function` queries the declared input series
pivoted per date (one query over `macro_series_values`, ~6.7k dates × few
series — trivially small), calls `compute(row)` per date in the same
sandbox, applies the same value/gap/failure semantics as 011
(non-numeric → error; NaN/None → gap; failing body writes nothing).
Registration validates: all declared series exist and are enabled; cycle
refusal by DFS over the composite dependency graph (output name → declared
inputs, recursing through composites). `macro derive` orders execution:
non-composite market functions first, then composites in topological order,
so same-night inputs are fresh before composites read them.

**Rationale**: mirrors the 011 executor contract exactly (same failure
isolation, same gap honesty) at a tiny data scale; topological ordering is
the minimum needed for correct same-night derives; DFS at registration makes
cycles unrepresentable rather than detected at run time.

**Alternatives considered**: computing composites from other composites'
same-run in-memory values (rejected — stored values are the source of truth
and the recovery door is full recompute, matching existing derive
semantics); run-time cycle detection (rejected — refuse at the door, not in
the night run).

## R4 — Generation path: cycle runner targets candidates

**Decision**: Extend the cycle runner's generation with a market-scope path:
Claude-subprocess prompt variant stating the market contract
(`compute(rows)` for cross-section; `compute(row)` for composites) plus
deterministic market templates (participation/concentration/breadth-class,
and composite templates over existing series). Generated output is written
ONLY to `market_function_candidates` with provenance (origin
claude|template, principle, generator, timestamp) and a stored dry-run
record. The per-stock generation path is untouched.

**Rationale**: reuses the two existing generation mechanisms and their
compile-time validation; writing candidates instead of functions is the
entire behavioral difference, which keeps the change surface small and the
gate absolute.

**Alternatives considered**: a standalone generator command only (no cycle
integration) — kept AS WELL (an explicit `--propose` door is cheap), but
cycle integration is the epic's point (autonomous widening of the
vocabulary, gated).

## R5 — Dry-run: seeded synthetic inputs, stored with the candidate

**Decision**: At generation (and re-runnable at review), execute the
candidate in the sandbox over deterministic seeded synthetic data — a
synthetic cross-section (fixed seed, ~50 symbols, plausible OHLCV +
declared feature columns) for cross-section candidates; seeded values for
declared series for composite candidates. Store `{ok, sample_values | error}`
on the candidate row. A sandbox violation or wrong-shape result marks the
dry-run failed, which blocks approval.

**Rationale**: gives the reviewer executable evidence without touching
stored data (evaluation against real history IS execution — forbidden
pre-approval by the spec); determinism makes review reproducible.

**Alternatives considered**: dry-run over real recent dates — rejected
(violates the gate's own definition); no dry-run (code review only) —
rejected (cheap signal, catches sandbox/shape errors before a human spends
attention).

## R6 — Approval surface semantics

**Decision**: `approve`/`reject` are CLI/MCP commands recording approver
identity, timestamp, and (for reject) a required reason. Rejection retains
the row (`review_state='rejected'`) — supersede/hide, never erase. Cycles
and schedulers have no code path to approve; the MCP tools are
human-directed acts identical in trust model to the existing
`experiment_approve` (which regime discovery already relies on for its
human gate). Approving a candidate whose dry-run failed is refused.
Promotion creates the paired `feature_definitions` row (zero orphans) and
records the promoted function id on the candidate for audit.

**Rationale**: matches the regime-discovery gate precedent exactly; the
audit trail (who/when/why) is what makes a rejected claim mill safe to keep.

**Alternatives considered**: UI-side approve buttons — deferred (read-only
UI satisfies parity; decisions stay in CLI/MCP where identity is explicit);
a required second reviewer — rejected (single-owner system).
