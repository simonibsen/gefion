---
description: Run an autonomous experiment cycle — discover data, consult principles, propose experiments, execute, and evaluate
---

## Arguments

$ARGUMENTS

## Instructions

Autonomous experiment skill — orchestrates a full experiment cycle using the principles catalog and data discovery.

### Workflow

1. **Discover available data**:
   ```bash
   .venv/bin/python -m gefion.cli experiment discover --json
   ```
   Review the output: what data sources exist, what features are computed, what gaps and opportunities are identified.

2. **Load principles catalog** (relevant to the experiment type if specified):
   ```bash
   .venv/bin/python -m gefion.cli principles suggest --json
   ```
   If arguments specify an experiment type, add `--type <type>`.
   Review which hypotheses are "ready" (data available) vs "blocked" (missing data).

3. **Start an experiment cycle** (if not already in one):
   ```bash
   .venv/bin/python -m gefion.cli experiment cycle-start --json
   ```
   This creates a holdout window and FDR configuration.

4. **Propose experiments** from the ready hypotheses:
   For each promising hypothesis, propose an experiment:
   ```bash
   .venv/bin/python -m gefion.cli experiment propose --name "<name>" --type <type> --search-space '<json>' --principle <principle_id> --null-hypothesis "<hypothesis>" --cycle <cycle_id> --json
   ```

   The agent should:
   - Select 3-5 experiments from different principles (diversity requirement)
   - Include both principle-driven and data-driven hypotheses
   - Each must have a null hypothesis
   - Prioritize "ready" feasibility over "blocked"

5. **Approve and run experiments**:
   ```bash
   .venv/bin/python -m gefion.cli experiment approve --id <id> --json
   .venv/bin/python -m gefion.cli experiment run --id <id> --json
   ```

6. **Review results**:
   ```bash
   .venv/bin/python -m gefion.cli experiment results --id <id> --json
   ```

7. **Report** a summary of what was discovered, proposed, executed, and learned.

### Experiment Sources

Experiments can originate from multiple sources — not just principles:

| Source | Example |
|--------|---------|
| **Principle-driven** | "López de Prado says fractional differentiation preserves memory — let's test it" |
| **Data-driven** | "Discovery found fundamentals data with no derived features — let's create book-to-market ratio" |
| **Performance-driven** | "Model accuracy dropped last week — let's retune hyperparameters" |
| **User request** | User says "test whether volume-weighted features help" |

### Usage Examples

| Command | Meaning |
|---------|---------|
| `/gefion-experiment` | Full autonomous cycle: discover → suggest → propose → run |
| `/gefion-experiment feature_engineering` | Focus on feature experiments only |
| `/gefion-experiment suggest` | Just show suggestions, don't execute |
| `/gefion-experiment status` | Show status of current cycle |

### Available Experiment Types

| Type | Risk | Evaluation |
|------|------|-----------|
| `feature_engineering` | Medium | Model metrics on holdout |
| `feature_selection` | Low | Model metrics on holdout |
| `hyperparameter` | Low | Purged CV + holdout |
| `model_comparison` | Low | Identical splits + holdout |
| `label_engineering` | High | Backtest metrics on holdout |
| `strategy_params` | Low | Backtest metrics |
| `pipeline` | High | End-to-end holdout |

### Statistical Guardrails

- **Holdout**: Most recent 6 weeks structurally excluded from training
- **FDR**: Benjamini-Hochberg at 10% across all experiments in cycle
- **Promotion**: Automatic for experiments surviving FDR correction
- **Probation**: 7 days — auto-demote if performance degrades

### Key Principles

The agent has access to 62 principles across 5 domains:
- **statistical**: Variance ratios, stationarity, cointegration, GARCH
- **ml_finance**: Fractional differentiation, purged CV, meta-labeling, triple-barrier
- **factor**: Value, momentum, quality factors, Fama-MacBeth, factor crowding
- **risk_portfolio**: Fundamental law (IR=IC×√BR), Kelly criterion, risk budgeting
- **microstructure**: Bid-ask spread, VPIN, fat tails, antifragility

Use `gefion principles show <id>` to get full details on any principle.
