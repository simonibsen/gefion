# g2 Backlog

**Last Updated**: 2026-02-28

Open work items extracted from NEXT_STEPS.md, ML_ROADMAP.md, PROGRESS.md, and NOTES.md.

---

## Completed

### ~~Standalone Feature Computation UI~~ ✅
**Completed**: 2026-02-28 (branch: `standaloneFeatCompUI`)

Added "Compute" tab to Features view with symbol input, feature selection,
incremental/full mode, CLI preview, and background process execution.

---

## Active / In Progress

### Model Calibration Improvements
**Source**: NOTES.md
**Priority**: Medium

Quantile model `quantile` v`20260202` evaluation (7-day horizon, 42 samples):
- Q10 Calibration: 2.4% (target 10%) - poorly calibrated
- Q50 Calibration: 26.2% (target 50%) - poorly calibrated
- Q90 Calibration: 85.7% (target 90%) - reasonable
- 80% Interval Coverage: 83.3% (target 80%) - good

**Next steps**:
- Generate more historical predictions for robust evaluation
- Consider training with more data or tuning hyperparameters

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

## Technical Debt

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
