# Implementation Plan: Autonomous AI Experimentation Framework

**Branch**: `004-autonomous-experiments` | **Date**: 2026-03-29 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-autonomous-experiments/spec.md`

## Summary

Build an autonomous experimentation framework where an AI agent discovers available data, consults a principles catalog extracted from quantitative finance literature, proposes experiments across the full ML pipeline, executes them within a statistical sandbox (mandatory holdout + FDR control), and auto-promotes survivors. Extends the existing experiment infrastructure with new experiment types (feature engineering, feature selection, hyperparameter, model comparison, pipeline), data discovery, serializable configs, runtime monitoring, D3 visualizations, and a feedback loop that updates principle empirical status based on results.

## Technical Context

**Language/Version**: Python 3.10+ (existing codebase)
**Primary Dependencies**: scikit-learn, XGBoost, LightGBM, optuna (Bayesian search), psycopg, typer (CLI), streamlit (UI), jinja2 (D3 templates)
**New Dependencies**: scipy.stats (Benjamini-Hochberg FDR, paired t-tests), psutil (resource monitoring)
**Storage**: PostgreSQL with TimescaleDB (existing); principles catalog as YAML files in repo
**Testing**: pytest with ENABLE_DB_TESTS=1 for database tests; OTEL_ENABLED=false for unit tests
**Target Platform**: Linux/macOS (development), Docker (production)
**Project Type**: CLI + web UI + MCP server (existing architecture)
**Performance Goals**: Experiment cycle completes within configured wall time budget; discovery step < 30s; D3 chart rendering < 2s
**Constraints**: Holdout data structurally excluded from training; FDR at configurable rate (default 10%); compute budgets enforced per cycle
**Scale/Scope**: ~5000 stocks, ~100 features, ~10 principles sources, ~50 principles per domain area, experiment cycles of 5-20 experiments

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Database-First | PASS | Experiments already in DB; principles in YAML (curated knowledge, not runtime data — justified); new schema changes require approval |
| II. TDD | PASS | All new code follows red-green-refactor; tests listed before implementation in all phases |
| III. CLI-First | PASS | All experiment operations get CLI commands first, then MCP + UI |
| IV. Observability | PASS | Experiment execution produces OTEL traces; inherits parent OTEL_ENABLED |
| V. Consistent CLI Presentation | PASS | New commands use existing output/emit helpers |
| VI. Simplicity | WATCH | Feature scope is large; will implement incrementally (P1 stories first). Principles catalog is lightweight YAML, not a separate framework |
| Schema Governance | PASS | Schema extensions (experiment_cycles, principle references) require owner approval before implementation |
| Secrets Management | PASS | No new secrets; uses existing DATABASE_URL pattern |

**Simplicity justification**: The feature is inherently complex (autonomous ML experimentation with statistical controls) but each component is independently useful. Implementation is phased: discovery + principles + basic experiments (P1) → new experiment types + visualization (P2) → pipeline + feedback (P3). No premature abstraction — each phase solves a concrete current problem.

## Project Structure

### Documentation (this feature)

```text
specs/004-autonomous-experiments/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/           # Phase 1 output (CLI + MCP contracts)
├── checklists/          # Quality checklists
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
# Principles catalog (YAML, version-controlled)
data/principles/
├── statistical.yaml          # Campbell/Lo/MacKinlay, Hamilton
├── ml_finance.yaml           # López de Prado, Jansen
├── factor.yaml               # Ang, Bali/Engle/Murray
├── risk_portfolio.yaml       # Meucci, Grinold & Kahn
└── microstructure.yaml       # Harris

# Experiment framework extensions
src/gefion/experiments/
├── __init__.py               # (existing)
├── core.py                   # (extend: cycles, holdout, FDR, configs)
├── search.py                 # (existing: grid, random, bayesian)
├── discovery.py              # NEW: data inventory + gap analysis
├── principles.py             # NEW: catalog loader + query + feedback
├── holdout.py                # NEW: holdout window management
├── statistical.py            # NEW: FDR control, p-value computation
├── safety.py                 # NEW: resource checks (disk, memory, DB)
└── types/
    ├── __init__.py            # (existing)
    ├── strategy_params.py     # (existing)
    ├── feature_engineering.py # NEW
    ├── feature_selection.py   # NEW
    ├── hyperparameter.py      # NEW: with purged CV
    ├── model_comparison.py    # NEW
    ├── label_engineering.py   # NEW: triple-barrier, meta-labeling
    └── pipeline.py            # NEW: chained stages

# D3 chart templates for experiments
src/gefion/charts/d3/templates/
├── experiment_trials.html     # NEW: trial scatter/bar
├── experiment_fdr.html        # NEW: FDR cycle summary
├── experiment_heatmap.html    # NEW: parameter sensitivity
└── experiment_features.html   # NEW: feature importance before/after

src/gefion/charts/d3/renderers.py  # (extend: experiment chart functions)

# UI
src/gefion/ui/views/experiments.py # (extend: discovery, visualization, cycle view)

# CLI
src/gefion/cli.py                  # (extend: discovery, cycle commands)

# MCP
mcp-server/server.py              # (extend: discovery, cycle MCP tools)

# Tests
tests/
├── test_experiments_discovery.py   # NEW
├── test_experiments_principles.py  # NEW
├── test_experiments_holdout.py     # NEW
├── test_experiments_fdr.py         # NEW
├── test_experiments_safety.py      # NEW
├── test_experiments_types.py       # NEW (feature_eng, feature_sel, hyperparameter, model_comp)
├── test_experiments_pipeline.py    # NEW
├── test_experiments_config.py      # NEW (serializable configs)
├── test_d3_experiments.py          # NEW (chart rendering)
└── test_experiments.py             # (existing: strategy_params)

# Schema
sql/schema.sql                     # (extend: experiment_cycles table, is_experimental columns)
sql/migrations/                    # NEW migration for schema extensions
```

**Structure Decision**: Extends the existing `src/gefion/experiments/` package with new modules. No new top-level packages — keeps the existing monorepo structure. Principles catalog lives in `data/principles/` as YAML files alongside existing data exports.

## Complexity Tracking

| Decision | Why | Simpler Alternative Rejected Because |
|----------|-----|-------------------------------------|
| Separate holdout.py module | Holdout logic (window management, structural exclusion, rolling) is reused across all experiment types | Inline holdout in each type would duplicate critical statistical logic |
| FDR in its own module | Statistical correction is independent of experiment type; testable in isolation | Inlining in core.py mixes lifecycle management with statistical rigor |
| 5 YAML files for principles | One file per domain area keeps files manageable and allows selective loading | Single file grows too large; DB table mixes curated knowledge with runtime data |
