# Gefion Backlog

**Last Updated**: 2026-03-27

Open work items extracted from NEXT_STEPS.md, ML_ROADMAP.md, PROGRESS.md, and NOTES.md.

---

## Completed

### ~~Model Calibration Improvements~~ ✅
**Completed**: 2026-02-28 (branch: `siModelCalibration`)

Implemented conformal calibration via `gefion ml calibrate`. Computes additive shift
corrections from a holdout period so predicted quantiles achieve nominal coverage
(10%, 50%, 90%). Saves `calibration.json` alongside model artifacts; future
predictions automatically apply the shifts.

### ~~Standalone Feature Computation UI~~ ✅
**Completed**: 2026-02-28 (branch: `standaloneFeatCompUI`)

Added "Compute" tab to Features view with symbol input, feature selection,
incremental/full mode, CLI preview, and background process execution.

### ~~UI Error Feedback Loop~~ ✅
**Completed**: 2026-03-01

Errors during UI sessions are logged to `~/.gefion/ui_errors.jsonl`. When `gefion ui`
exits, a summary is printed to stdout so Claude Code can see and diagnose
failures. Hooks in `data.py` (background process failures) and `cli.py`
(session start/end). Module: `g2.ui.errors`.

### ~~UI Reliability~~ ✅
**Completed**: 2026-03-22 (branch: `001-ui-reliability`)

Systematic hardening of the Streamlit UI assistant view:
- Renamed to "AI Actions", promoted to 2nd sidebar position
- Persistent conversation history (`~/.gefion/ai_history.jsonl`, 100 exchange cap)
- In-UI error surfacing (count badge + expandable list)
- Form submission, auto-refresh, Run button fixes
- Fixed 8 broken MCP_TOOL_MAP CLI mappings + regression tests
- CLAUDECODE env stripping for nested claude -p
- Chat input reordered above proactive actions
- Documentation added to USER_GUIDE.md
- Spec: `specs/001-ui-reliability/spec.md`

---

## Future Features

### VIX (macro series) ingestion as a regime input — ✅ CLOSED by spec 007 (2026-07-08)
**Resolution**: went the first-class route, not the pseudo-symbol route — spec 007
built the `macro_series` home, `gefion macro ingest --name vix --provider fred:VIXCLS`
(INDEX_DATA verified not-entitled on the prod key; FRED keyless CSV is the default),
and the `macro_vix` feature with `entity_table='macro_series'`. The "add a data
source" recipe now exists in docs/DEVELOPMENT.md. Related: issues #75/#76 —
`data entity-delete` is their first landed increment. Prod rollout = 007 T027.

**Original item** (kept for the record):
**Source**: T047 discovery diagnostics (2026-07-07); `regime-detection-hmm` principle
declares `macro.vix` in data_requirements and discovery records it as an
`uncomputable_proposal` structural diagnostic — the ledger is literally asking for it
**Priority**: Medium — first concrete "negative-space" signal from the diagnostics ledger

Regimes consume features, so the goal is a `macro_vix` series in `computed_features`;
everything downstream (discovery atoms, `regime define` expressions,
`regime interaction --by macro_vix`, principle seeding's `vix` stem match) then works
with zero further code. Plan:

1. **Provider**: AlphaVantage serves VIX via `INDEX_DATA&symbol=VIX`
   (daily/weekly/monthly OHLC, decades of history — a **premium** endpoint;
   the existing key's ~68 calls/min rate implies a premium plan, so it should
   already be unlocked — verify with one live call first). Reuse the existing
   `alphavantage/` client: one fetch method + a `catalog.py` parser, TDD.
   Fallback if the plan lacks index data: FRED `VIXCLS` (free, close-only)
   via a small new client.
2. **Storage**: start with the zero-schema-change path — pseudo-symbol row in `stocks`
   (`symbol='^VIX'`, `asset_type='Index'`) + closes in `stock_ohlcv`. Universe filters
   already exclude Index from tradable/discovery symbol universes, so VIX becomes
   conditioning data without becoming a candidate stock. If a macro family follows
   (rates; CPI is already parsed in `alphavantage/catalog.py`; dollar index), propose a
   first-class `macro_series` table instead (owner approval, two-file rule, data
   dictionary).
3. **Feature definition**: `macro_vix` passthrough row in `feature_definitions` so the
   series lands in `computed_features` (market-level median of a single entity is the
   value itself).
4. **Done means**: ingest command on CLI (+ MCP/UI if recurring), docs, data-layer
   learning-module line — and write the generic "add a data source" recipe into
   `docs/DEVELOPMENT.md` while doing it (no such recipe exists today).

### Universe quality filter (test tickers, asset types)
**Source**: first production ingest (sloth, 2026-07-06)
**Priority**: Medium — bites as soon as research runs against the prod universe

The NASDAQ "Active" listing ingested 6,193 symbols including ETFs, warrants, units,
and NASDAQ test tickers (ZVZZT, ZWZZT, ZXZZT, ZJZZT…). Research/backtest universes
need a quality filter: exclude test tickers outright; make asset-type (common stock
vs ETF vs warrant) a first-class selector using `stocks.asset_type`/`stocks.exchange`.
Follow-up originally noted after issues #29/#30 (exchange filters once
`stocks.exchange` populates) — production data now makes it concrete.

### chart regime: missing UI door + curriculum mention (audit finding 2026-07-06)
**Source**: three-axis audit (interfaces/docs/learning)
**Priority**: Small, quick

`gefion chart regime` (#56) shipped CLI + MCP + docs but no UI access (add a "Chart"
action per regime on the UI Regimes page) and no curriculum mention (one line in the
gefion-learn charts/regime material). The plan-template now makes these axes mandatory
so this class of gap is caught at planning time.

### Regime follow-ups (spec 005 shipped 2026-07; spec 006 shipped 2026-07-07)
**Source**: specs/005-regime-slicing, specs/006-agentic-regime-discovery
**Priority**: Medium

Spec 005 (regime slicing) shipped: definitions/labels, sliced backtests,
continuous-interaction, conditional experiment verdicts — across CLI/MCP/UI.
Remaining within 005's spec surface:
1. Per-entity (sector/industry/asset) label computation — US1 shipped market scope;
   `compute_labels` raises NotImplementedError for finer scopes
2. Reference-leaf resolution (compose stored regimes by name) and optional DSL string sugar
3. Per-observation holdout scores wired into more experiment types (only the shared
   helper exists; evaluators must emit `observations` for live conditional verdicts)

Spec 006 (agentic regime discovery) **shipped 2026-07-07** (T001–T046 on the dev
machine; first real-data validation run T047 pending on sloth): nested segregation
with an inner-evidence screen, one flat FDR family that counts the losers, all three
expressiveness tiers (incl. the 005 FR-019a detector-leaf runtime under the
fresh-holdout reserve), forward-only trust grading, diagnostics ledger, negative
control in CI, full CLI/MCP/UI parity + Module 10 curriculum.

### Reality-Check/SPA bootstrap for discovery (006 fast-follow — REQUIRED before raising budgets)
**Source**: specs/006-agentic-regime-discovery (FR-108, Clarification Q1)
**Priority**: High gate, not urgent — blocks raising discovery search budgets only

v1 error control is flat BH over the full realized family at 0.01 plus the inner
screen, honest at v1's capped volumes (measured false-admission ~1/100 noise runs).
A data-snooping-robust selection check (White Reality Check / Hansen SPA-style
bootstrap over the full candidate set) MUST land before per-cycle candidate budgets
are raised beyond v1 defaults (~50–200) or the grammar depth cap above K=2. The
candidate ledger already retains everything the bootstrap needs (the seam is there).
Also queued behind it: `signal_source` rungs `model_predictions` (needs a production
model) and `strategy_backtests` (needs the bootstrap; equity-curve inference is not a
clean paired test), and automated fold accrual riding data-update probation checks
(v1: `regime discover grade-fold` is manual/operator-driven).

### Live & Paper Trading (ML_ROADMAP Phase 6)
**Source**: ML_ROADMAP.md
**Priority**: Low (future)

Execute strategies in real-time with broker integration.

**Components needed**:
1. Order Router - routes signals to paper or live execution
2. Broker Adapters - Alpaca (priority), Interactive Brokers
3. Position Manager - track and reconcile positions
4. Real-time Data Feed - WebSocket connections

**CLI Commands (proposed)**:
```
g2 trade run --strategy momentum --mode paper --capital 100000
g2 trade run --strategy momentum --mode live --broker alpaca
g2 trade positions
g2 trade orders --limit 50
g2 trade flatten --confirm
```

**Database tables needed**: orders, positions, trading_sessions (requires schema approval per constitution)

**Safety features**: paper mode default, daily loss limits, position limits, confirmation prompts, emergency flatten, audit logging

**Implementation order**: Paper trading → Alpaca → Position reconciliation → Real-time data → IBKR (optional)

---

## Technical Debt (High Priority)

### ~~Migrate Postgres to Named Volume + Clean Up g2-Era Container~~ ✅ DONE
**Completed**: 2026-05-15

Migrated `gefion-postgres` from anonymous volume `6b6f435528fb…` (g2 era) to
named volume `gefion_postgres-data` declared in `docker-compose.yml`. 14.3 GB
of data copied via helper container. Row counts identical pre/post. Container's
`127.0.0.1` port binding now active — LAN access blocked. Initdb path
corrected from `/Users/simonibsen/src/g2/...` to `/Users/simonibsen/src/gefion/...`.

Old anonymous volume retained as a safety net — can be reclaimed (~14 GB) once
confirmed unused for a few days: `docker volume rm 6b6f435528fb79fa8c02ef4cbbc35d53b3566b7ef0e5bb11d93d511846ff480d`

### ~~Unified Predictions Table~~ ✅
**Completed**: 2026-03-27 (branch: `predictions`)
Merged `quantile_predictions` and `trend_class_predictions` into single `predictions` table with JSONB values. Migration, helper module, CLI/UI/MCP all updated.

---

## Bugs

### Backup Disk Space Check Falsely Reports Insufficient Space
**Discovered**: 2026-03-25
**Severity**: Medium

`gefion backup --data-types definitions` reports "Insufficient disk space. Need ~0.0 MB" with 42 GB available. The space estimation logic is broken — reports ~0.0 MB for definitions and ~919.1 MB for all data types.

---

## Technical Debt

### Unified CLI Output Component for UI
**Priority**: Medium
**Discovered**: 2026-03-29

**Problem**: Three different patterns exist for rendering CLI process output in the UI:
1. `render_process_status()` in `data.py` — data-update specific metrics (Progress, Inserted, Errors, Workers, Rate, ETA)
2. `render_freeform_output()` in `assistant.py` — AI/CLI stream-json parsing with work events
3. `_render_cull_status()` in `data.py` — cull-specific JSON parsing

This violates Constitution Section V (Consistent CLI Presentation). Each new CLI command integrated into the UI requires a new custom renderer.

**Desired outcome**: A single `render_cli_output(key, title)` component in `src/gefion/ui/components/cli_output.py` that:
- Shows running/complete/error state in an expander
- Parses JSON output from any CLI `--json` command into structured display (tables, counts, messages)
- Falls back to plain text for non-JSON output
- Auto-refreshes while running (polling process state)
- Supports command-specific display hints via JSON metadata (e.g., `"display": "table"` vs `"display": "metrics"`)
- Replaces all three existing patterns

**Files to modify**:
- Create: `src/gefion/ui/components/cli_output.py`
- Refactor: `src/gefion/ui/views/data.py` (data-update + cull)
- Refactor: `src/gefion/ui/views/assistant.py` (freeform output)
- Refactor: `src/gefion/ui/views/backtest.py` (backtest run)
- Refactor: `src/gefion/ui/views/ml.py` (train/predict/eval)

**Benefits**: New CLI commands automatically get proper UI rendering. No more duplicate rendering logic. Constitution Section V compliance.

### Cascading Cleanup on Data Cull ✅ DONE
**Discovered**: 2026-03-25
**Priority**: Medium
**Branch**: `predictions` (combined with unified predictions work)

When `stock_ohlcv` rows are deleted (e.g., culling to a 1-year window), downstream artifacts are orphaned with no warning. No foreign key constraints exist between `stock_ohlcv` and derived tables.

**Affected tables** (in dependency order, leaf to root):
1. `model_performance`, `prediction_outcomes`, `quantile_predictions`, `trend_class_predictions` (leaf)
2. `ml_models`, `ml_runs`
3. `ml_datasets`
4. `computed_features`, `cross_sectional_features`
5. `stock_ohlcv` (root)

**Proposed solution**: A `gefion data cull --before DATE` command that:
1. Identifies downstream artifacts overlapping the cull window
2. Reports impact via dry-run (default)
3. On `--confirm`, deletes in dependency order leaf → root
4. Logs removals for auditability

**Manual action taken** (2026-03-25): Deleted 1 stale `ml_datasets` row (training_20260325) built against full 26-year history, incompatible with culled 1-year window.

### Feature Management CLI Enhancements
**Source**: PROGRESS.md (Future Work)
**Priority**: Low

- `feat-fx-enable/disable`, `feat-def-enable/disable` commands (currently requires JSON edit + reimport)
- Inactive function handling: validation when definitions reference disabled/missing functions
- `feat-def-validate` / `feat-def-fix` commands for orphaned definitions
- Show function status in `feat-def-list` output

### Experiment Framework Extensions
**Source**: EXPERIMENTS.md
**Priority**: Low

Currently implemented: `strategy_params` optimization

Planned experiment types (not yet implemented):
- `feature_selection` - find optimal feature subsets
- `hyperparameter` - tune ML model hyperparameters
- `model_comparison` - compare multiple models

---

## Design Documents (Not Yet Implemented)

### Autonomous AI Experimentation Framework
**Source**: docs/design/AUTONOMOUS_EXPERIMENTATION.md
**Priority**: Future (implement incrementally as pain points emerge)

Vision for autonomous AI-driven experimentation with guardrails. Key concepts:
- Experimental vs production artifact tracking (`is_experimental` flag)
- Composite experiments (multi-step pipelines)
- Dependency tracking (prevent accidental deletion of used artifacts)
- Promotion path (experimental → production with review)
- Auto-promotion criteria for low-risk changes
- Resource guardrails (max trials, compute limits)
- Monitoring and daily digests

**Recommended progression** (from design doc):
| Trigger | Add This |
|---------|----------|
| Now | Nothing - current framework is sufficient |
| Accidentally delete something used elsewhere | Dependency tracking |
| Manual pipeline coordination becomes tedious | Composite experiments |
| Can't tell experimental vs production | `is_experimental` flag |
| Promotion queue backs up | Auto-promotion criteria |

Full design: `.specify/specs/autonomous-experimentation.md`
