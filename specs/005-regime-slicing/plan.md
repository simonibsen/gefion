# Implementation Plan: Regime Slicing — Conditional Evaluation Across Market/Sector/Asset States

**Branch**: `005-regime-slicing` | **Date**: 2026-07-04 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-regime-slicing/spec.md`

## Summary

Add a first-class **regime** abstraction: describe the state of the market/sector/asset as a
named, causal, persistent, time-indexed dimension, and evaluate signals and strategies
*conditionally* against it. Three capabilities: (1) define + compute regime labels (declarative
expression-tree definitions with a gated detector-function leaf, causal by construction,
persistent episodes); (2) slice an existing backtest's dated equity curve/trades by a regime
and report per-regime metrics that reconcile to the aggregate; (3) conditional experiment
verdicts — per-regime holdout p-values entered into a flat Benjamini-Hochberg family — plus a
continuous-interaction test for graded conditioning.

**Technical approach**: a new cohesive module `src/gefion/regimes/` that *consumes* existing
infrastructure rather than duplicating it — `backtest/metrics.py` for per-regime metric
computation, `experiments/statistical.py::apply_fdr` for BH, `experiments/holdout.py` for the
causal holdout window, and the existing feature-function sandbox for detector-function leaves.
Regime definitions are stored in the database and exported to `regime-definitions/` (Database-First),
mirroring the feature-definition pattern.

## Technical Context

**Language/Version**: Python 3.10+
**Primary Dependencies**: psycopg (DB), numpy / pandas / scipy / statsmodels (effective-N,
interaction regression), existing gefion modules (`backtest.metrics`, `experiments.statistical`,
`experiments.holdout`, `features.dispatcher` + feature-function sandbox), typer (CLI), streamlit (UI)
**Storage**: PostgreSQL + TimescaleDB. New `regime_definitions` (relational) and `regime_labels`
(hypertable) tables — **schema change, PROPOSED for owner approval, not executed** (Schema
Governance). Definitions also exported to `regime-definitions/*.json` for version control.
**Testing**: pytest; DB tests guarded by `ENABLE_DB_TESTS=1` using `schema.test_db_url()`
**Target Platform**: Linux/macOS server (CLI-first; MCP + Streamlit UI parity)
**Project Type**: Single project (CLI-first research system)
**Performance Goals**: slicing reuses existing metric functions on regime-filtered equity
segments (no new heavy path); label computation is causal/rolling and bounded; avoid `COUNT(*)`
on hypertables (use `pg_stat_user_tables.n_live_tup` per constitution perf patterns)
**Constraints**: causal labels (no lookahead, FR-004/017/018); per-regime results reconcile to
aggregate (FR-009); parameterized SQL only; `Json()` adapter for JSONB; observability spans on
all new modules; type hints + docstrings on public functions
**Scale/Scope**: dev dataset ~100 instruments / ~11k OHLCV rows; production target = decades of
history across all instruments/exchanges (dataset provenance + per-dataset diagnostics, FR-023/024)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How the plan satisfies it |
|---|---|---|
| I. Database-First | **PASS (gated)** | Regime definitions live in DB + exported to `regime-definitions/` JSON. New tables (`regime_definitions`, `regime_labels`) are **proposed DDL only** — owner approval required before writing `schema.sql`/migration (Schema Governance). No autonomous schema change. |
| II. TDD (non-negotiable) | **PASS** | Every step lists the test file before the src file; DB tests use `schema.test_db_url()` with `ENABLE_DB_TESTS` guards. |
| III. CLI-First | **PASS** | New `gefion regime …` group + `backtest run --by-regime`; MCP tools mirror the CLI; `/gefion` operator skill reviewed for new tools; skill (if any) prefixed `gefion-`. |
| IV. Observability | **PASS** | All `regimes/` modules import `gefion.observability`; significant ops use `create_span`/`@traced` with parent-context propagation. |
| V. Consistent CLI Presentation | **PASS** | Output via `output.py` / `cli_helpers`; `--json` bypasses formatting. |
| VI. Simplicity | **PASS** | Reuses `backtest.metrics`, `experiments.statistical`, `experiments.holdout`, feature-function sandbox. One new module; no premature abstraction (detector-function leaf reuses existing sandbox, not a new one). |
| Tech Constraints | **PASS** | Python 3.10+, parameterized SQL, `Json()` for JSONB, no deep learning, type hints + docstrings. |

No unjustified violations. The only gated item is the schema change, handled by the propose-don't-execute rule.

## Project Structure

### Documentation (this feature)

```text
specs/005-regime-slicing/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (cli.md, mcp.md, sql.md)
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
src/gefion/
├── regimes/                     # NEW cohesive module
│   ├── __init__.py
│   ├── definitions.py           # RegimeDefinition + RegimeExpression AST, validation, JSON export/import
│   ├── labels.py                # causal label computation, persistence/hysteresis, effective-N
│   ├── slicing.py               # attach labels to backtest output; per-regime metrics (reuse backtest.metrics); reconciliation
│   ├── interaction.py           # continuous-interaction (linear signal×conditioning term)
│   └── conditional.py           # per-regime holdout p-values → experiments.statistical.apply_fdr
├── backtest/                    # REUSED: engine emits dated equity/trades; metrics.py reused per-regime
├── experiments/                 # REUSED: statistical.apply_fdr, holdout.HoldoutWindow; conditional eval hook
├── features/                    # REUSED: dispatcher + feature-function sandbox for detector-function leaves
├── cli.py                       # NEW: `regime` command group; `backtest run --by-regime` option
└── observability.py             # REUSED

regime-definitions/              # NEW: JSON exports of regime definitions (Database-First backup)

sql/
├── schema.sql                   # PROPOSED additions (owner approval) — regime_definitions, regime_labels
└── migrations/NNNNNN_regimes.sql# PROPOSED migration (two-file rule)

tests/
├── test_regime_definitions.py   # AST validation, causality-by-construction, JSON round-trip
├── test_regime_labels.py        # causal labels, persistence/hysteresis, effective-N, undefined periods
├── test_regime_slicing.py       # per-regime metrics, reconciliation, opt-in no-change
├── test_regime_interaction.py   # continuous-interaction recovery + no-false-gradient
├── test_regime_conditional.py   # per-regime p-values, flat BH family size, fail-closed
└── test_regime_cli_mcp.py       # CLI/MCP/UI parity, docs-drift
```

**Structure Decision**: Single-project CLI-first layout. The new capability is isolated in
`src/gefion/regimes/` (five focused files mirroring the five spec capabilities), consuming
existing `backtest`, `experiments`, and `features` infrastructure. This keeps the surface small
and honors Simplicity — no new sandbox, no new metric engine, no new FDR implementation.

## Complexity Tracking

> No constitution violations require justification. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
