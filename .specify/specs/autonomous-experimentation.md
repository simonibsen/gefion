# Autonomous AI Experimentation Framework

## Design Document

**Status**: Future Work (Not Yet Implemented)
**Author**: Design discussion
**Date**: 2025-01-04

---

> **Note**: This document describes a comprehensive vision for autonomous AI
> experimentation. It is intentionally ambitious and should be implemented
> incrementally as pain points emerge, not all at once.
>
> **Current state**: Basic experiment framework exists (propose → approve → run).
> See [experiments-framework.md](experiments-framework.md) for current capabilities.

---

## 1. Overview

### 1.1 Purpose

Enable AI-driven experimentation across the Gefion platform with appropriate guardrails, isolation, and promotion paths. The AI can autonomously propose and run experiments, while humans maintain oversight through monitoring, review, and promotion decisions.

### 1.2 Scope

The experimentation framework covers:

| Component | AI Capabilities |
|-----------|-----------------|
| Feature Functions | Create new functions, modify parameters |
| Feature Definitions | Create new definitions using existing or new functions |
| Dataset Building | Build datasets with different feature combinations |
| Model Training | Train with hyperparameter tuning, warm-start from existing models |
| Predictions & Evaluation | Generate predictions, evaluate model performance |
| Strategy Configs | Create new parameterized strategy configurations |
| Strategy Creation | Propose new strategy implementations (requires human review) |
| Backtesting | Run backtests with various parameters and configurations |

### 1.3 Key Principles

1. **Isolation**: Experimental artifacts are clearly separated from production
2. **Composability**: Experiments can orchestrate multiple components as a pipeline
3. **Dependency Awareness**: System tracks what depends on what, prevents orphaning
4. **Async Oversight**: AI runs autonomously within guardrails; humans review outcomes
5. **Promotion Path**: Clear process to graduate successful experiments to production
6. **Rollback Capability**: Any promotion can be reversed

---

## 2. Experimental vs Production Artifacts

### 2.1 Definition

**Experimental artifacts** are those **created by** an experiment. They:
- Are marked with `is_experimental = true`
- Reference their source experiment
- Are not used by production systems
- Can be freely deleted (with dependency warnings)

**Production artifacts** are those that have been:
- Created manually, OR
- Promoted from an experiment after review

### 2.2 Important Clarification

**Using a production artifact in an experiment does NOT make it experimental.**

```
Example:
  - prod_dataset_v1 (production) exists
  - Experiment #42 uses prod_dataset_v1 to train a new model
  - prod_dataset_v1 remains PRODUCTION
  - The new model (exp42_model) is EXPERIMENTAL
```

Only newly created artifacts are marked experimental. Existing artifacts retain their status regardless of how they're used.

### 2.3 Artifact Status Lifecycle

```
                    ┌─────────────┐
                    │   CREATED   │
                    │ (by experiment)
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ EXPERIMENTAL│ ◄── Created by AI
                    │     🧪      │     Can be deleted
                    └──────┬──────┘     Not in production
                           │
                           │ (promotion + review)
                           ▼
                    ┌─────────────┐
                    │ PRODUCTION  │ ◄── Reviewed & approved
                    │     ✓       │     Protected from deletion
                    └──────┬──────┘     Used by live systems
                           │
                           │ (superseded by newer version)
                           ▼
                    ┌─────────────┐
                    │  ARCHIVED   │ ◄── Kept for rollback
                    │     📦      │     Not actively used
                    └─────────────┘
```

---

## 3. Composite Experiments

### 3.1 Motivation

Real optimization often requires coordinating multiple steps:

1. Create a new feature
2. Build a dataset including that feature
3. Train a model on the dataset
4. Create a strategy config using the model
5. Backtest the strategy

A **composite experiment** orchestrates these as a single tracked unit.

### 3.2 Component Types

| Component Type | Action | Creates |
|----------------|--------|---------|
| `feature_function` | create, modify | Feature function definition |
| `feature_definition` | create, modify | Feature definition |
| `dataset` | build | Dataset manifest + exported files |
| `model` | train | Model files + registry entry |
| `evaluation` | run | Evaluation metrics |
| `strategy_config` | create | Strategy configuration |
| `strategy` | propose | Strategy code (requires review) |
| `backtest` | run | Backtest results |

### 3.3 Component Dependencies

Components within an experiment can depend on each other:

```yaml
experiment:
  name: "Full Pipeline Optimization"
  components:
    - seq: 1
      type: feature_definition
      action: create
      name: "indicator_rsi_21"
      depends_on: []

    - seq: 2
      type: dataset
      action: build
      name: "exp_dataset"
      depends_on: [1]  # Needs the feature from step 1

    - seq: 3
      type: model
      action: train
      name: "exp_model"
      depends_on: [2]  # Needs the dataset from step 2
      config:
        warm_start_from: "prod_model_v1"  # Can reference production artifacts
```

### 3.4 Execution Flow

```
Experiment Submitted
        │
        ▼
┌───────────────────┐
│ Component 1       │ ── Creates artifact_1 (experimental)
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Component 2       │ ── Uses artifact_1
└────────┬──────────┘    Creates artifact_2 (experimental)
         │
         ▼
┌───────────────────┐
│ Component 3       │ ── Uses artifact_2 + prod_model_v1
└────────┬──────────┘    Creates artifact_3 (experimental)
         │
         ▼
  Experiment Complete
  Results: {best_score, artifacts_created, recommendation}
```

---

## 4. Dependency Tracking

### 4.1 Why Dependencies Matter

- **Prevent orphaning**: Don't delete things that other things need
- **Impact analysis**: Understand what's affected by changes
- **Lineage tracking**: Know where artifacts came from
- **Cascade operations**: Promote or delete related artifacts together

### 4.2 Dependency Types

```
feature_function
       │
       │ (defines)
       ▼
feature_definition ◄─────────────┐
       │                         │
       │ (included in)           │ (may reference)
       ▼                         │
    dataset ─────────────────────┘
       │
       │ (trained on)
       ▼
    model
       │
       │ (used by)
       ▼
strategy_config
       │
       │ (evaluated by)
       ▼
 backtest_result
```

### 4.3 Dependency Rules

| Artifact Type | Can Depend On |
|---------------|---------------|
| feature_definition | feature_function |
| dataset | feature_definition(s) |
| model | dataset, (optionally) another model (warm-start) |
| strategy_config | model (for ML strategies), feature_definition(s) |
| backtest | strategy_config |

### 4.4 Delete Protection

When attempting to delete an artifact:

```
$ gefion feature-def delete indicator_rsi_21

🔍 Checking dependencies...

Dependents found:

  EXPERIMENTAL 🧪 (can cascade):
    - dataset: exp42_dataset
    - model: exp42_model (via exp42_dataset)

  PRODUCTION ✓ (protected):
    - dataset: prod_dataset_v3

⚠️  Cannot delete: has production dependents.

Options:
  --cascade-experimental  Delete this + experimental dependents
  --force                 Delete anyway (will break prod_dataset_v3)
```

---

## 5. Warm-Start Training

### 5.1 Concept

Instead of training from scratch, start from an existing model's weights and continue training. This enables:

- Faster convergence
- Lower compute cost
- Incremental improvements
- Transfer learning between feature sets

### 5.2 Configuration

```yaml
model_training:
  algorithm: xgboost
  dataset: "exp42_dataset"

  # Warm start options (pick one):
  warm_start_from: "prod_model_v1"           # From production model
  warm_start_from_experiment: 41              # From another experiment

  # Continue training with:
  hyperparameter_search:
    method: bayesian
    n_trials: 20
    search_space:
      learning_rate: {type: float, low: 0.01, high: 0.2, log: true}
      n_estimators: {type: int, low: 100, high: 500}
```

### 5.3 Lineage Tracking

```sql
-- Model knows where it came from
ml_models:
  name: "exp42_model"
  warm_started_from: "prod_model_v1"
  source_experiment_id: 42
  is_experimental: true
```

---

## 6. Guardrails

### 6.1 Resource Guardrails

Prevent runaway compute and storage costs:

```yaml
resource_limits:
  max_trials_per_experiment: 100
  max_concurrent_experiments: 5
  max_experiments_per_day: 20
  max_compute_hours_per_week: 48
  max_storage_gb_experimental: 50
```

### 6.2 Scope Guardrails

Limit what parameters can be modified:

```yaml
parameter_bounds:
  strategy_params:
    lookback_days: [3, 120]
    top_n: [1, 50]
    rsi_threshold: [5, 95]

  model_hyperparams:
    learning_rate: [0.001, 1.0]
    max_depth: [1, 20]
    n_estimators: [10, 2000]

  constraints:
    - "fast_period < slow_period"
    - "rsi_oversold < rsi_overbought"
```

### 6.3 Quality Guardrails

Ensure experiments produce meaningful results:

```yaml
quality_requirements:
  minimum_backtest_days: 60
  minimum_trades: 10
  require_out_of_sample: true
  out_of_sample_ratio: 0.2
  max_allowable_drawdown: -0.50
```

### 6.4 Approval Tiers

| Tier | Auto-Approve If | Examples |
|------|-----------------|----------|
| **1 - Auto** | Within bounds, low resource use | Parameter tweaks, small backtests |
| **2 - Quick Review** | Medium complexity, bounded | New feature combinations, hyperparameter search |
| **3 - Full Review** | New code, high resource, production impact | New strategies, model architecture changes |

---

## 7. Monitoring

### 7.1 Approach

Asynchronous oversight rather than synchronous approval:

- Experiments run automatically within guardrails
- System monitors for anomalies and issues
- Humans receive digests and alerts
- Review happens on outcomes, not inputs

### 7.2 Metrics Tracked

**Execution Health**
- Experiments proposed/completed/failed
- Average duration
- Resource utilization

**Result Quality**
- Improvement rate over baseline
- Goal achievement rate
- Out-of-sample performance gap

**Anomaly Detection**
- Results too good (overfitting signal)
- Parameter clustering (not exploring)
- Sudden failure spikes

### 7.3 Alert Tiers

| Level | Condition | Action |
|-------|-----------|--------|
| 🔴 Critical | Failure rate > 50%, system errors | Immediate notification |
| 🟡 Warning | Declining improvement, budget high | Daily digest |
| 🟢 Info | Experiment completed, new best | Logged, queryable |

### 7.4 Daily Digest Example

```
═══════════════════════════════════════════════════════════════
📊 g2 Experiment Digest - 2025-01-04
═══════════════════════════════════════════════════════════════

SUMMARY
  Experiments: 8 proposed, 7 completed, 1 failed
  Best result: momentum_pipeline_v2 (Sharpe: 1.84, +12% vs baseline)
  Compute used: 3.2 hrs (16% of daily budget)
  Artifacts created: 4 🧪 experimental

TOP RESULTS
  1. momentum_pipeline_v2   Sharpe 1.84  (+12.1%)  Recommend: Promote
  2. rsi_period_search      Sharpe 1.71  (+4.3%)   Recommend: Review
  3. feature_selection_v3   Sharpe 1.65  (+1.2%)   Recommend: Continue

FAILURES
  1. ensemble_config_v1     OOM during trial 34 of 50

PENDING PROMOTION
  - indicator_rsi_21 (from exp:38, 3 days old)
  - ml_signal_config_v2 (from exp:41, 1 day old)

WARNINGS
  ⚠️ 80% of experiments focused on momentum strategy (low diversity)

───────────────────────────────────────────────────────────────
[View Details] [Promote All Recommended] [Pause Experiments]
```

---

## 8. Promotion Path

### 8.1 Workflow

```
Experiment Completes
        │
        ▼
┌───────────────────────────────────────┐
│ Artifacts in EXPERIMENTAL state       │
│ Results available for review          │
│ System generates promotion recommend. │
└────────────────┬──────────────────────┘
                 │
                 ▼
┌───────────────────────────────────────┐
│ REVIEW (human or auto if criteria met)│
│ - Check metrics                       │
│ - Validate on held-out data          │
│ - Consider production impact          │
└────────────────┬──────────────────────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
   [Approved]         [Rejected]
        │                 │
        ▼                 ▼
┌──────────────┐  ┌──────────────┐
│   PROMOTE    │  │   ARCHIVE    │
│ - Move to    │  │ - Mark as    │
│   production │  │   rejected   │
│ - Archive    │  │ - Keep for   │
│   previous   │  │   reference  │
└──────────────┘  └──────────────┘
```

### 8.2 Promotion Commands

```bash
# View promotion candidates
gefion experiment promotions --status pending

# Promote a single artifact
gefion experiment promote 42 --artifact "indicator_rsi_21" --as "indicator_rsi_21"

# Promote all artifacts from an experiment
gefion experiment promote 42 --all --suffix "_v2"

# Promote with notes
gefion experiment promote 42 --artifact "exp42_model" \
  --as "prod_model_v2" \
  --note "Validated on Q4 2024 data, 8% improvement"

# Rollback a promotion
gefion experiment rollback \
  --artifact-type model \
  --artifact-name "prod_model_v2" \
  --reason "Performance degraded in live trading"
```

### 8.3 Auto-Promotion Criteria

Certain artifacts can be auto-promoted if they meet strict criteria:

```yaml
auto_promotion:
  enabled: true

  rules:
    strategy_config:
      - improvement_pct >= 10
      - sharpe_ratio >= 1.5
      - max_drawdown >= -0.25
      - backtest_days >= 180
      - out_of_sample_validated: true

    feature_definition:
      - improvement_pct >= 5
      - must_be_new: true  # Never auto-replace existing

    model:
      - never  # Models always require human review
```

---

## 9. Database Schema

### 9.1 Enhanced Experiments Table

```sql
CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    hypothesis TEXT,
    experiment_type VARCHAR(50),  -- 'composite', 'single', 'parameter_search'

    -- Configuration
    config JSONB NOT NULL,
    search_space JSONB,

    -- Objective
    objective_metric VARCHAR(50) DEFAULT 'sharpe_ratio',
    objective_direction VARCHAR(10) DEFAULT 'maximize',
    goal_target NUMERIC(12,6),
    goal_type VARCHAR(20),

    -- Composite experiment tracking
    total_components INTEGER DEFAULT 1,
    completed_components INTEGER DEFAULT 0,

    -- Execution
    status VARCHAR(20) DEFAULT 'proposed',
    priority INTEGER DEFAULT 0,

    -- Results
    results JSONB,
    best_score NUMERIC(12,6),
    promotion_recommendation VARCHAR(20),  -- 'promote', 'review', 'reject'

    -- Audit
    proposed_by VARCHAR(100),
    approved_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

### 9.2 Experiment Components Table

```sql
CREATE TABLE experiment_components (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER REFERENCES experiments(id) ON DELETE CASCADE,

    -- Component definition
    sequence INTEGER NOT NULL,
    component_type VARCHAR(50) NOT NULL,
    action VARCHAR(50) NOT NULL,

    -- Artifact reference
    artifact_type VARCHAR(50),
    artifact_name VARCHAR(255),
    artifact_id INTEGER,  -- Populated after creation

    -- Dependencies
    depends_on_components INTEGER[],

    -- Configuration
    config JSONB,

    -- Execution
    status VARCHAR(20) DEFAULT 'pending',
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    UNIQUE(experiment_id, sequence)
);
```

### 9.3 Artifact Dependencies Table

```sql
CREATE TABLE artifact_dependencies (
    id SERIAL PRIMARY KEY,

    -- Source artifact (the one that has dependencies)
    artifact_type VARCHAR(50) NOT NULL,
    artifact_id INTEGER NOT NULL,

    -- Target artifact (what it depends on)
    depends_on_type VARCHAR(50) NOT NULL,
    depends_on_id INTEGER NOT NULL,

    -- Metadata
    dependency_kind VARCHAR(50) DEFAULT 'requires',
    created_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(artifact_type, artifact_id, depends_on_type, depends_on_id)
);

CREATE INDEX idx_deps_target ON artifact_dependencies(depends_on_type, depends_on_id);
```

### 9.4 Promotion History Table

```sql
CREATE TABLE artifact_promotions (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER REFERENCES experiments(id),

    -- What was promoted
    artifact_type VARCHAR(50) NOT NULL,
    artifact_id INTEGER NOT NULL,
    production_name VARCHAR(255) NOT NULL,

    -- What it replaced
    replaced_artifact_id INTEGER,
    replaced_artifact_archived_to VARCHAR(500),

    -- Audit
    promoted_at TIMESTAMP DEFAULT NOW(),
    promoted_by VARCHAR(100),
    promotion_notes TEXT,

    -- Status
    status VARCHAR(20) DEFAULT 'active',
    rolled_back_at TIMESTAMP,
    rollback_reason TEXT
);
```

### 9.5 Add Experimental Columns to Artifact Tables

```sql
-- Feature functions
ALTER TABLE feature_functions ADD COLUMN IF NOT EXISTS
    is_experimental BOOLEAN DEFAULT false;
ALTER TABLE feature_functions ADD COLUMN IF NOT EXISTS
    source_experiment_id INTEGER REFERENCES experiments(id);
ALTER TABLE feature_functions ADD COLUMN IF NOT EXISTS
    promoted_at TIMESTAMP;

-- Feature definitions
ALTER TABLE feature_definitions ADD COLUMN IF NOT EXISTS
    is_experimental BOOLEAN DEFAULT false;
ALTER TABLE feature_definitions ADD COLUMN IF NOT EXISTS
    source_experiment_id INTEGER REFERENCES experiments(id);
ALTER TABLE feature_definitions ADD COLUMN IF NOT EXISTS
    promoted_at TIMESTAMP;

-- ML models
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS
    is_experimental BOOLEAN DEFAULT false;
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS
    source_experiment_id INTEGER REFERENCES experiments(id);
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS
    promoted_at TIMESTAMP;
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS
    warm_started_from VARCHAR(255);

-- Strategy configs
ALTER TABLE strategy_configs ADD COLUMN IF NOT EXISTS
    is_experimental BOOLEAN DEFAULT false;
ALTER TABLE strategy_configs ADD COLUMN IF NOT EXISTS
    source_experiment_id INTEGER REFERENCES experiments(id);
ALTER TABLE strategy_configs ADD COLUMN IF NOT EXISTS
    promoted_at TIMESTAMP;

-- Dataset manifests
ALTER TABLE ml_dataset_manifests ADD COLUMN IF NOT EXISTS
    is_experimental BOOLEAN DEFAULT false;
ALTER TABLE ml_dataset_manifests ADD COLUMN IF NOT EXISTS
    source_experiment_id INTEGER REFERENCES experiments(id);
ALTER TABLE ml_dataset_manifests ADD COLUMN IF NOT EXISTS
    promoted_at TIMESTAMP;
```

---

## 10. CLI Commands

### 10.1 Experiment Management

```bash
# Propose a composite experiment
gefion experiment propose \
  --name "full_pipeline_opt" \
  --type composite \
  --config-file experiment_config.yaml

# List experiments
gefion experiment list --status completed --limit 20

# View experiment details
gefion experiment show 42 --include-components --include-artifacts

# Approve for execution
gefion experiment approve 42

# Run experiment
gefion experiment run 42
```

### 10.2 Artifact Management

```bash
# List artifacts with experimental status
gefion feature-def list --show-experimental
gefion ml model-list --show-experimental

# Check dependencies before delete
gefion artifact deps --type feature_definition --name indicator_rsi_21

# Delete with cascade
gefion feature-def delete indicator_rsi_21 --cascade-experimental
```

### 10.3 Promotion

```bash
# View pending promotions
gefion experiment promotions --status pending

# Promote artifact
gefion experiment promote 42 --artifact "exp42_model" --as "prod_model_v2"

# Rollback
gefion experiment rollback --artifact-type model --name prod_model_v2
```

### 10.4 Monitoring

```bash
# View experiment digest
gefion experiment digest --days 7

# Check experiment health
gefion experiment monitor --alert-level warning

# Resource usage
gefion experiment usage --period week
```

---

## 11. UI Integration

### 11.1 Experiments Page Enhancements

- List view shows composite experiment progress
- Component-level status for multi-step experiments
- Artifact tree showing what was created
- One-click promotion for approved results

### 11.2 Artifact Views

All artifact list views (Features, Models, Strategies) should show:

```
┌─────────────────────────────────────────────────────────────────┐
│ Name               │ Status      │ Dependencies │ Source       │
│────────────────────│─────────────│──────────────│──────────────│
│ indicator_rsi_14   │ ✓ production│ Used by: 12  │ manual       │
│ indicator_rsi_21   │ 🧪 exp      │ Used by: 3   │ exp:42       │
│ custom_momentum    │ 🧪 exp      │ Used by: 1   │ exp:38       │
└─────────────────────────────────────────────────────────────────┘

Filter: [All] [Production Only] [Experimental Only]
```

### 11.3 Dependency Visualization

Visual graph showing artifact relationships:

```
                    ┌─────────────────┐
                    │ indicator_rsi_21│
                    │ 🧪 experimental │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
      ┌──────────────┐ ┌──────────┐ ┌──────────────┐
      │exp42_dataset │ │prod_ds_v3│ │exp43_dataset │
      │ 🧪 exp       │ │ ✓ prod   │ │ 🧪 exp       │
      └──────┬───────┘ └────┬─────┘ └──────────────┘
             │              │
             ▼              ▼
      ┌──────────────┐ ┌──────────────┐
      │ exp42_model  │ │prod_model_v2 │
      │ 🧪 exp       │ │ ✓ prod       │
      └──────────────┘ └──────────────┘
```

---

## 12. Implementation Phases

### Phase 1: Foundation
- Add experimental columns to artifact tables
- Create artifact_dependencies table
- Implement dependency tracking on create/delete
- Add experimental status to UI views

### Phase 2: Composite Experiments
- Create experiment_components table
- Implement multi-step experiment execution
- Add component dependency resolution
- Build composite experiment UI

### Phase 3: Promotion System
- Create artifact_promotions table
- Implement promote/rollback commands
- Add auto-promotion criteria evaluation
- Build promotion UI

### Phase 4: Monitoring & Guardrails
- Implement resource tracking
- Add parameter bounds enforcement
- Build daily digest generation
- Create monitoring dashboard

### Phase 5: Advanced Features
- Warm-start training support
- Experiment chaining (use output of one as input to another)
- A/B testing of promoted artifacts
- Performance tracking post-promotion

---

## 13. Open Questions

1. **Strategy code generation**: Should AI be able to propose new strategy code, or only compose from existing strategies?

2. **Retention policy**: How long to keep experimental artifacts before cleanup?

3. **Concurrent experiments**: Can multiple experiments modify the same artifact type simultaneously?

4. **Cross-experiment dependencies**: Can one experiment depend on another experiment's output?

5. **Partial promotion**: Can we promote some artifacts from an experiment but not others?

---

## 14. Implementation Strategy

### 14.1 Guiding Principle

**Implement incrementally as pain points emerge, not all at once.**

This design is a north star. The full system is complex and may be premature for current needs. Add capabilities surgically when specific problems arise.

### 14.2 Recommended Progression

| Trigger | Add This | Complexity |
|---------|----------|------------|
| **Now** | Nothing - current experiment framework is sufficient | None |
| Accidentally delete something used elsewhere | Dependency tracking | Medium |
| Manual pipeline coordination becomes tedious | Composite experiments | High |
| Experimental artifacts pollute production views | `is_experimental` flag | Low |
| Promotion queue backs up | Auto-promotion criteria | Medium |
| Need to trace where artifacts came from | Full lineage tracking | Medium |

### 14.3 Simpler Alternatives to Consider First

Before implementing the full design, consider whether simpler approaches suffice:

**Naming Conventions**
```
exp_rsi_21          # Experimental (prefix)
indicator_rsi_14    # Production (no prefix)

# Cleanup: DELETE WHERE name LIKE 'exp_%'
```

**Git Branches for Experiments**
```bash
git checkout -b experiment/rsi-optimization
# ... make changes, run backtests ...
git merge experiment/rsi-optimization  # = promotion
```

**Simple Experiment Log**
```sql
-- Just log what happened, no formal tracking
INSERT INTO experiment_log (name, description, commands_run, outcome)
VALUES ('rsi_optimization', 'Test RSI(21) vs RSI(14)',
        ARRAY['gefion ml train ...', 'gefion backtest run ...'], 'success');
```

### 14.4 Minimum Viable Implementation

If implementing something now, start with only:

```sql
-- Add to existing artifact tables
ALTER TABLE feature_definitions
  ADD COLUMN is_experimental BOOLEAN DEFAULT false,
  ADD COLUMN source_experiment_id INTEGER REFERENCES experiments(id);

-- Repeat for: feature_functions, ml_models, strategy_configs, ml_dataset_manifests
```

This enables:
- Filtering experimental vs production in UI
- Knowing where artifacts came from
- No dependency tracking (defer until needed)
- No composite experiments (defer until needed)
- No auto-promotion (defer until needed)

### 14.5 Signals That More Is Needed

Implement more of this design when you observe:

| Signal | Indicates Need For |
|--------|-------------------|
| "I broke production by deleting X" | Dependency tracking |
| "I keep running the same 5 commands in sequence" | Composite experiments |
| "I can't tell what's experimental vs production" | Experimental flags |
| "I have 50 pending promotions to review" | Auto-promotion |
| "I don't know where this model came from" | Lineage tracking |
| "Experiments are piling up, wasting compute" | Resource guardrails |

---

## 15. References

- [EXPERIMENTS.md](../EXPERIMENTS.md) - Current experiments documentation
- [ML_QUICKSTART.md](../ML_QUICKSTART.md) - ML pipeline documentation
- [STRATEGIES.md](../STRATEGIES.md) - Strategy documentation
- [BACKTESTING.md](../BACKTESTING.md) - Backtesting documentation
