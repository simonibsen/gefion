# AI Experimentation Framework

## Overview

The experiments module enables **autonomous experimentation** with trading strategy parameters, ML model hyperparameters, and feature selection. It follows a **hybrid autonomy** model where AI proposes experiments and users approve them.

**Key Features:**
- Data-driven experiments stored in PostgreSQL (queryable, reproducible)
- Pluggable search strategies (Grid, Random, Bayesian)
- Goal-based optimization with optional early stopping
- Experiment chaining (output of one feeds into next)
- Full integration with backtest engine and ML pipeline

## Core Concepts

### Experiment Types

| Type | Description | Status |
|------|-------------|--------|
| `strategy_params` | Optimize trading strategy parameters | Implemented |
| `feature_selection` | Find optimal feature subsets | Planned |
| `hyperparameter` | Tune ML model hyperparameters | Planned |
| `model_comparison` | Compare multiple models | Planned |

### Experiment Lifecycle

```
proposed → approved → running → completed
    ↓         ↓                     ↓
 rejected   failed              (results stored)
```

- **proposed**: Created by AI or user, awaiting approval
- **approved**: Ready to run
- **running**: Currently executing trials
- **completed**: All trials finished, results available
- **failed**: Error during execution
- **rejected**: User declined the experiment

### Search Strategies

| Strategy | Description | Best For |
|----------|-------------|----------|
| `GridSearch` | Exhaustive parameter combinations | Small search spaces, completeness required |
| `RandomSearch` | Random sampling | Large spaces, quick exploration |
| `BayesianSearch` | Adaptive optimization (Optuna TPE) | Efficient optimization, finding optima |

### Goals and Early Stopping

**Exploratory (no goal):**
```bash
g2 experiment propose --name "explore_momentum" --objective sharpe_ratio ...
# Runs all trials, reports best result
```

**Targeted (achieve goal):**
```bash
g2 experiment propose --name "achieve_sharpe_2" \
  --goal-type achieve --goal-target 2.0 --early-stop ...
# Stops when Sharpe >= 2.0
```

**Improvement (beat baseline):**
```bash
g2 experiment propose --name "improve_by_20pct" \
  --goal-type improve --baseline 1.5 --goal-target 1.8 --early-stop ...
# Stops when Sharpe improves from 1.5 to 1.8
```

## CLI Commands

### Propose Experiment

```bash
g2 experiment propose \
  --name "momentum_optimization" \
  --strategy momentum \
  --search-space '{"lookback_days": {"type": "int", "low": 5, "high": 30}}' \
  --symbols AAPL,MSFT,GOOGL \
  --start-date 2023-01-01 \
  --end-date 2024-01-01 \
  --objective sharpe_ratio \
  --search-method bayesian \
  --max-trials 50
```

**Parameters:**

| Flag | Description | Default |
|------|-------------|---------|
| `--name` | Experiment name (required) | - |
| `--strategy` | Strategy to optimize | - |
| `--search-space` | JSON search space definition | (required) |
| `--symbols` | Comma-separated symbols | - |
| `--start-date` | Backtest start date | - |
| `--end-date` | Backtest end date | - |
| `--objective` | Metric to optimize | `sharpe_ratio` |
| `--direction` | `maximize` or `minimize` | `maximize` |
| `--max-trials` | Maximum number of trials | 50 |
| `--search-method` | `grid`, `random`, `bayesian` | `grid` |
| `--goal-type` | `achieve` or `improve` | - |
| `--goal-target` | Target value for goal | - |
| `--baseline` | Baseline for improvement | - |
| `--early-stop` | Stop when goal achieved | false |

### Search Space Format

```json
{
  "lookback_days": {
    "type": "int",
    "low": 5,
    "high": 30,
    "step": 5
  },
  "entry_threshold": {
    "type": "float",
    "low": 0.01,
    "high": 0.10,
    "steps": 10
  },
  "exit_type": {
    "type": "categorical",
    "choices": ["trailing", "fixed", "time_based"]
  },
  "learning_rate": {
    "type": "float",
    "low": 0.0001,
    "high": 0.1,
    "log": true
  }
}
```

**Parameter Types:**
- `int`: Integer range with optional `step`
- `float`: Float range with optional `steps` or `log` (log-scale)
- `categorical`: Discrete choices

### List Experiments

```bash
# All experiments
g2 experiment list

# Filter by status
g2 experiment list --status proposed
g2 experiment list --status completed

# Filter by type
g2 experiment list --type strategy_params

# Limit results
g2 experiment list --limit 10

# JSON output
g2 experiment list --json
```

### Approve/Reject

```bash
# Approve for execution
g2 experiment approve --id 1

# Reject with reason
g2 experiment reject --id 1 --reason "Too many trials"
```

### Run Experiment

```bash
# Run an approved experiment
g2 experiment run --id 1
```

Executes all trials (or until goal achieved with `--early-stop`), tracks progress, stores results.

### View Results

```bash
# Summary
g2 experiment results --id 1

# Include all trial details
g2 experiment results --id 1 --show-trials

# JSON output
g2 experiment results --id 1 --json
```

**Output includes:**
- Best parameters found
- Best score achieved
- Total/completed trials
- Goal achievement status
- Trial-by-trial metrics (with `--show-trials`)

### Get Status

```bash
g2 experiment status --id 1
```

Shows full experiment details including config, progress, and results.

## Experiment Chaining

Chain experiments where child uses parent's output:

```bash
# Run initial optimization
g2 experiment propose --name "coarse_search" \
  --search-space '{"lookback_days": {"type": "int", "low": 5, "high": 50, "step": 5}}' \
  ...
g2 experiment approve --id 1
g2 experiment run --id 1

# Chain a fine-tuning experiment
g2 experiment chain \
  --parent-id 1 \
  --name "fine_tune" \
  --search-space '{"lookback_days": {"type": "int", "low": 13, "high": 17}}' \
  --depends-on best_params

# List children
g2 experiment children --parent-id 1

# Get parent info
g2 experiment parent --id 2
```

**depends_on options:**
- `best_params`: Use parent's best parameter values
- `best_score`: Use parent's best score as baseline

## MCP Tools

The experiment tools are exposed via MCP for AI interaction:

| Tool | Description |
|------|-------------|
| `experiment_propose` | Propose a new experiment |
| `experiment_list` | List experiments with filters |
| `experiment_approve` | Approve for execution |
| `experiment_run` | Run approved experiment |
| `experiment_results` | Get experiment results |
| `experiment_chain` | Create chained experiment |
| `experiment_children` | List child experiments |
| `experiment_status` | Get detailed status |

## Database Schema

### experiments

```sql
CREATE TABLE IF NOT EXISTS experiments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    experiment_type VARCHAR(50) NOT NULL,

    -- Configuration
    config JSONB NOT NULL,
    search_space JSONB,
    search_method VARCHAR(20) DEFAULT 'grid',

    -- Objective
    objective_metric VARCHAR(50) DEFAULT 'sharpe_ratio',
    objective_direction VARCHAR(10) DEFAULT 'maximize',
    goal_target NUMERIC(12,6),
    goal_type VARCHAR(20),
    baseline_value NUMERIC(12,6),
    early_stop_on_goal BOOLEAN DEFAULT FALSE,

    -- Execution
    status VARCHAR(20) DEFAULT 'proposed',
    priority INTEGER DEFAULT 0,

    -- Chaining
    parent_experiment_id INTEGER REFERENCES experiments(id),
    depends_on_output VARCHAR(100),

    -- Results
    results JSONB,
    goal_achieved BOOLEAN,
    total_trials INTEGER,
    completed_trials INTEGER DEFAULT 0,
    best_score NUMERIC(12,6),

    -- Metadata
    proposed_by VARCHAR(50) DEFAULT 'ai',
    approved_by VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

### experiment_trials

```sql
CREATE TABLE IF NOT EXISTS experiment_trials (
    id SERIAL PRIMARY KEY,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id),
    trial_number INTEGER NOT NULL,

    params JSONB NOT NULL,
    metrics JSONB NOT NULL,
    score NUMERIC(12,6),

    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    duration_seconds NUMERIC(10,2),

    UNIQUE(experiment_id, trial_number)
);
```

## Python API

```python
from g2.experiments import (
    ExperimentConfig,
    ExperimentRunner,
    GridSearch,
    RandomSearch,
    BayesianSearch
)

# Create config
config = ExperimentConfig(
    name="momentum_opt",
    experiment_type="strategy_params",
    search_space={
        "lookback_days": {"type": "int", "low": 5, "high": 30}
    },
    objective_metric="sharpe_ratio",
    max_trials=50,
    search_method="bayesian"
)

# Create runner
runner = ExperimentRunner(db_url)

# Propose experiment
exp_id = runner.propose(config)

# Approve and run
runner.approve(exp_id)
results = runner.run(exp_id)

print(f"Best params: {results['best_params']}")
print(f"Best score: {results['best_score']}")
```

## Example Workflow

```
1. AI analyzes momentum strategy performance
   → Notices suboptimal parameters

2. AI proposes experiment via MCP:
   experiment_propose(
     name="momentum_optimization",
     strategy="momentum",
     search_space={"lookback_days": [5,10,15,20]},
     search_method="bayesian"
   )
   → Experiment #42 created (status: proposed)

3. User reviews:
   g2 experiment list --status proposed
   → Shows experiment details, estimated trials

4. User approves:
   g2 experiment approve --id 42

5. AI runs experiment:
   g2 experiment run --id 42
   → Executes trials, tracks results

6. AI reports results:
   g2 experiment results --id 42
   → Best: lookback_days=15, Sharpe 1.8 (up from 1.2)

7. AI proposes follow-up (chaining):
   g2 experiment chain --parent-id 42 \
     --name "fine_tune" \
     --search-space '{"entry_threshold": {"type": "float", ...}}'
```

## Best Practices

1. **Start with RandomSearch** for large parameter spaces to quickly find promising regions
2. **Use BayesianSearch** for final optimization - it adapts based on results
3. **Set realistic goals** with `--early-stop` to save compute time
4. **Chain experiments** for multi-stage optimization (coarse → fine)
5. **Use `--json`** for programmatic access to results
6. **Review proposals** before approving - check trial count and compute cost

## Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture
- [USER_GUIDE.md](USER_GUIDE.md) - CLI command reference
- [BACKTESTING.md](BACKTESTING.md) - Backtest engine details
