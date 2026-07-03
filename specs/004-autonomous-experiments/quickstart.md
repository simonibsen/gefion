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
gefion experiment cycle-start --name "first-cycle" --holdout-weeks 6 --fdr-rate 0.10
```

This reserves the most recent 6 weeks of data as holdout and creates a cycle
container. Optionally pass `--config <file.json>` with guardrails
(allowed_types, max_trials_per_experiment, dataset_uri, algorithm, ...).

### 3. Run the cycle autonomously

```bash
gefion experiment cycle-run 1
```

The orchestrator discovers hypotheses, proposes experiments (generating
feature functions via AI when needed — demoted functions are never reused),
runs them, and applies the statistical gate.

Or propose manually:
```bash
gefion experiment propose --name "frac-diff-close" \
  --type feature_engineering \
  --principle ldp-fractional-diff \
  --cycle 1 \
  --null-hypothesis "Fractionally differentiated close has no higher feature importance than standard returns" \
  --search-space '{"d": [0.3, 0.4, 0.5]}'
gefion experiment approve --id <experiment_id>
gefion experiment run --id <experiment_id>
```

For each experiment the runner:
- Runs trials with purged CV on **pre-holdout rows only** (holdout structurally excluded)
- After trials, trains the best configuration and a baseline on identical
  pre-holdout data and scores both on the holdout window — exactly once
- Records a **one-sided holdout p-value** (only improvement counts; label
  experiments are scored by a signal contest on realized returns, not
  prediction metrics)

### 4. The statistical gate (automatic in cycle-run)

BH-FDR is applied across the cycle's holdout p-values. **Fail-closed**: an
experiment with no p-value cannot survive. Survivors are promoted (feature
functions become active) and a 7-day probation window opens.

### 5. Take a winner to production

```bash
gefion experiment apply --id <experiment_id>
```

Rebuilds the dataset with the promoted feature, retrains with the winning
parameters, generates predictions, and backtests the `ml_signal` strategy.
Artifacts are recorded on the experiment; probation monitoring arms.

Probation runs automatically at the end of every `gefion data-update`, or
manually:

```bash
gefion experiment probation-check      # auto-demotes measurable degradation
gefion experiment demote --id <id> --reason "..."   # manual demotion
```

### 6. Review results

In the UI: Experiments → Cycles → select cycle → holdout p-values, FDR chart,
lifecycle badges (🟡 on probation / 🟢 promoted / 🔴 demoted). Loaded
experiment results show trial charts and the Apply to Production button.

Via CLI:
```bash
gefion experiment results --id <experiment_id> --trials
gefion experiment cycle-list
gefion chart experiment-fdr <cycle_id>       # FDR summary chart
gefion chart experiment-trials <experiment_id>
```

## Agent-Driven Cycle (via skill)

```
/gefion-experiment
```

The agent autonomously: discovers data → consults principles → proposes
experiments → executes → evaluates on holdout → FDR gate → promotes genuine
survivors → reports.
