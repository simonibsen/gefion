# Quickstart: Autonomous AI Experimentation

## Prerequisites

- Gefion installed with data in the database (at least `stock_ohlcv` and some computed features)
- At least one trained ML model (`gefion ml train`)
- Principles catalog populated (`data/principles/*.yaml`)

## Run an Experiment Cycle

### 1. Discover available data

```bash
gefion experiment discover
```

Shows what data sources exist, which features are computed, and where gaps exist relative to the principles catalog.

### 2. Start a cycle

```bash
gefion experiment cycle start --name "first-cycle" --holdout-weeks 6 --fdr-rate 0.10
```

This reserves the most recent 6 weeks as holdout and creates a cycle container.

### 3. Propose experiments (agent or manual)

Via Ask Gefion:
> "Suggest experiments for feature engineering based on the principles catalog"

Or manually:
```bash
gefion experiment propose "frac-diff-close" \
  --type feature_engineering \
  --principle ldp-fractional-diff \
  --cycle 1 \
  --null-hypothesis "Fractionally differentiated close has no higher feature importance than standard returns" \
  --search-space '{"d": [0.3, 0.4, 0.5]}'
```

### 4. Run experiments

```bash
gefion experiment run <experiment_id>
```

The experiment runner:
- Computes new features (training data only — holdout excluded)
- Rebuilds dataset with new features
- Trains model with purged CV
- Evaluates on holdout
- Records p-value

### 5. Evaluate the cycle

```bash
gefion experiment cycle evaluate 1
```

Applies BH-FDR across all experiments in the cycle. Survivors are auto-promoted. Results show which experiments passed and which were rejected.

### 6. Review results

In the UI: Navigate to Experiments → Cycles → select cycle → view D3 charts (FDR summary, trial scatter, feature importance).

Via CLI:
```bash
gefion experiment results <experiment_id> --show-trials
```

## Agent-Driven Cycle (via skill)

```
/gefion-experiment
```

The agent autonomously: discovers data → consults principles → proposes experiments → executes → evaluates → promotes survivors → reports.
