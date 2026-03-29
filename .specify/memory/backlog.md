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
