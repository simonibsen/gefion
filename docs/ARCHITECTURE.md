# Gefion Architecture

## Overview

gefion is a database-first technical analysis platform with versioned feature engineering capabilities. Feature definitions and custom feature implementations are stored in the database and exported to git for version control.

**Note:** Built-in technical indicators are currently implemented in Python code for performance, but will be migrated to the database-first pattern for consistency (see ML Roadmap). This migration will enable users to modify indicator parameters without code changes.

## Core Design Principles

### 1. Database as Source of Truth

**Feature Functions** and **Feature Definitions** are stored in PostgreSQL, not in code:

```
Database (Source of Truth)
    ↓ export
Git Repository (Version Control)
    ↓ import
Database (Deploy)
```

**Benefits**:
- Version control through git (one JSON file per function/definition)
- Easy rollback (re-import older versions)
- No code deployment required for feature changes
- Consistent representation across environments

### 2. Feature Functions vs Feature Definitions

**Feature Functions** (`feature_functions` table):
- Reusable computation logic (Python code)
- Versioned (name + version = unique)
- Sandboxed execution (restricted imports, no file/network access)
- Examples: `indicator` (v1.0), `derivative` (v1.0)

**Feature Definitions** (`feature_definitions` table):
- Configuration for what to compute
- References a feature function + parameters
- Specifies source data and storage location
- Examples: `indicator_rsi_14`, `indicator_macd_12_26_9`

**Relationship**:
```
feature_definition.function_name → feature_functions.name
feature_definition.params → passed to function
```

## Database Schema

### Core Tables

```sql
-- Time-series price data (TimescaleDB hypertable)
stock_ohlcv (
    data_id → stocks.id,
    date,
    open, high, low, close, adjusted_close, volume
)

-- Feature function implementations
feature_functions (
    name, version,           -- Unique identifier
    function_body,           -- Python code as text
    language,                -- 'python'
    enabled,                 -- Can be disabled without deletion
    status,                  -- 'active', 'deprecated', 'archived'
    inputs, param_schema,    -- Define function signature
    output_name, output_type -- Define return value
)

-- Feature definitions (what to compute)
feature_definitions (
    name,                    -- Unique identifier
    function_name,           -- → feature_functions.name
    params,                  -- Parameters passed to function
    source_table,            -- Where to read data (e.g., stock_ohlcv)
    source_column,           -- Which column (e.g., close)
    store_table,             -- Where to write (e.g., computed_features)
    active                   -- Can be disabled
)

-- Computed results (TimescaleDB hypertable)
computed_features (
    data_id → stocks.id,
    date,
    feature_id → feature_definitions.id,
    value
)

-- Quarterly financial data
quarterly_financials (
    data_id → stocks.id,
    fiscal_date,
    reported_date,
    metric_name,
    value
)
```

### Data model at a glance
- `stocks`: one row per symbol (`id` PK, `symbol`, `name`, `exchange`)
- `stock_ohlcv`: Timescale hypertable keyed by (`data_id`, `date`), with OHLCV columns and a composite index on `data_id, date DESC`
- `feature_functions`: registry of callable implementations (`name`, `version`, `language`, `function_body`, `enabled`, `status`, `inputs`, `param_schema`, `output_name`, `output_type`), indexed on `enabled, status, name`
- `feature_definitions`: configuration of what to compute (`name`, `function_name`, `params`, `source_table`, `source_column`, `store_table`, `active`)
- `computed_features`: tall store for outputs (`data_id`, `date`, `feature_id`, `value`), composite index on `data_id, feature_id, date DESC`

### Indexing Strategy

**Optimized for**:
- Date range queries (TimescaleDB chunks by month)
- Symbol lookups (B-tree index on data_id)
- Feature filtering (composite index on feature_id + date)
- Function lookups (index on enabled + status + name)

## Feature Computation Pipeline

### Dispatcher Architecture

```
1. Load active feature definitions
   ↓
2. For each definition, load referenced function
   ↓
3. Fetch source data (e.g., last 200 days of stock_ohlcv)
   ↓
4. Execute function in sandbox with params
   ↓
5. Validate output schema
   ↓
6. Upsert to computed_features
```

**Key Components**:

- **Sandboxing** ([dispatcher.py](../src/gefion/features/dispatcher.py)):
  - Restricted `__builtins__` (no `open`, `exec`, `eval`)
  - Limited imports (pandas, numpy, talib allowed)
  - No network or filesystem access
  - Resource limits (not yet implemented)

- **Parallel Execution**:
  - ThreadPoolExecutor for parallel symbol processing
  - Worker threads read from queue, write to database
  - Adaptive resource scaling based on CPU/memory/DB connections

- **Error Handling**:
  - Feature-level failures don't stop the batch
  - Errors logged with context (symbol, feature, reason)
  - Progress tracking with rich UI

### Local vs API Mode

**Local Mode** (default):
```python
# Compute features from price data in database
rows = fetch_price_data(conn, symbol, feature.source_column)
result = execute_function(rows, feature.params)
```

**API Mode** (legacy):
```python
# Fetch pre-computed indicators from AlphaVantage
payload = client.fetch_indicator(symbol, indicator_type)
rows = parse_indicator_payload(payload)
```

## Security Model

### Sandboxed Execution

Feature functions run in a restricted Python environment:

**Allowed**:
- Core Python: math, datetime, statistics
- Data libraries: pandas, numpy, talib
- Pure computation (no side effects)

**Blocked**:
- File I/O: `open`, `Path`, `os`
- Network: `requests`, `urllib`, `socket`
- Code execution: `exec`, `eval`, `compile`, `__import__`
- Database access: `psycopg`, `sqlalchemy`

**Implemented via**:
```python
restricted_globals = {
    '__builtins__': {k: v for k, v in __builtins__.items()
                     if k not in BLOCKED_BUILTINS},
    'pd': pandas,
    'np': numpy,
    'talib': talib,
}
exec(function_body, restricted_globals, {})
```

**Future Improvements** (see [archive/ml/SECURITY_SANDBOXING.md](archive/ml/SECURITY_SANDBOXING.md)):
- Resource limits (CPU, memory, time)
- Process isolation (separate processes per function)
- Output validation (type checking, range limits)

### Security checklist
- Enforced now: blocked builtins (`open`, `exec`, `eval`, `compile`, `__import__`), no filesystem or network modules (`os`, `pathlib`, `requests`, `socket`), sandbox globals limited to pandas/numpy/talib/math/datetime/statistics, functions executed in isolated namespace.
- Not yet enforced (planned): per-function CPU/memory/time limits, process isolation instead of threads, stronger output validation (schema and bounds checking), explicit import allowlist for any new packages.

## Export/Import System

### CLI Commands

```bash
# Export functions to git (one file per function)
gefion feat-fx-export --dir feature-functions
# Creates: feature-functions/indicator_v1.0.json

# Export definitions to git
gefion feat-def-export --dir feature-definitions
# Creates: feature-definitions/indicator_rsi_14.json

# Import from git (upsert on name+version)
gefion feat-fx-import --dir feature-functions
gefion feat-def-import --dir feature-definitions
```

### File Format

**Feature Function**:
```json
{
  "name": "indicator",
  "version": "1.0",
  "language": "python",
  "function_body": "def compute(rows, specs): ...",
  "inputs": {"rows": "DataFrame", "specs": "dict"},
  "output_name": "value",
  "output_type": "double precision",
  "param_schema": {"indicator": "string"},
  "enabled": true,
  "status": "active"
}
```

**Feature Definition**:
```json
{
  "name": "indicator_rsi_14",
  "function_name": "indicator",
  "params": {"indicator": "rsi"},
  "source_table": "stock_ohlcv",
  "source_column": "close",
  "store_table": "computed_features",
  "active": true
}
```

## Rate Limiting

### AlphaVantage Client

The API client enforces strict rate limits to prevent burst patterns:

```python
class RateLimiter:
    # Per-minute limit: 75 calls (premium tier)
    # Per-second enforcement: 1.0 second minimum spacing
    # This prevents bursts even with multiple parallel workers
```

**Why minimum spacing?**
- AlphaVantage requires requests "spread evenly" across 1-minute window
- Token bucket alone allows bursts (e.g., 5 requests in 1 second)
- Minimum spacing ensures truly even distribution

**Implementation**:
```python
min_spacing = (60.0 / calls_per_minute) * 1.25  # 1.0 sec for 75/min
if time_since_last >= min_spacing and tokens >= 1:
    # Grant permission
```

## Performance Optimizations

### Bulk Filtering

Before ingestion, filter symbols that don't need updates:

```sql
-- Find symbols with up-to-date data
SELECT s.symbol
FROM stocks s
JOIN stock_ohlcv p ON s.id = p.data_id
WHERE p.date >= :expected_latest_date
```

**Impact**: 91% skip rate, avoids unnecessary API calls

### Resource-Aware Scaling

Dynamically adjust worker counts based on:
- Available CPU (2x CPU cores max)
- Available memory (leave 20% buffer)
- Available database connections
- Current queue depth

### TimescaleDB Chunks

Price and feature data are chunked by month:
- Faster range queries (only scan relevant chunks)
- Efficient compression for old data
- Automatic retention policies (future)

## Testing Strategy

### Unit Tests
- Feature function sandboxing
- Export/import roundtrip
- CLI command structure

### Integration Tests
- Full export/import workflow
- Feature computation end-to-end
- Database migrations

### Test Isolation
- Each test uses fresh database connection
- Transactions rolled back after test
- Mocked AlphaVantage API for unit tests

### Cross-Sectional Features

Sector-relative and market-relative features (e.g., stock return vs sector average) are supported via a dedicated table:

```sql
CREATE TABLE cross_sectional_features (
    data_id INTEGER REFERENCES stocks(id),
    date DATE,
    sector VARCHAR(50),
    feature_name VARCHAR(255),
    value DOUBLE PRECISION,
    rank INTEGER,  -- Rank within sector/market
    percentile DOUBLE PRECISION,  -- Percentile within sector/market
    PRIMARY KEY (data_id, date, feature_name)
);
```

This enables:
- Sector-relative metrics (stock performance vs sector benchmark)
- Market-relative metrics (stock performance vs market index)
- Cross-sectional ranking and screening
- Sector rotation strategies

## AI Experimentation Framework

### Overview

The experiments module enables autonomous experimentation across the full pipeline — features, hyperparameters, model choice, prediction targets, and strategy parameters. AI proposes experiments (cycles auto-approve within guardrails); a statistical gate, not human judgment, decides what gets promoted.

### Core Concepts

**Experiment Types**:
- `feature_engineering` - New computed features (AI-generated code, sandboxed)
- `hyperparameter` - Model tuning with purged CV
- `model_comparison` - Algorithm choice vs the incumbent
- `label_engineering` - Changed prediction targets (evaluated via a signal
  contest on realized returns — prediction metrics are not comparable
  across different targets)
- `feature_selection`, `strategy_params`, `pipeline`

**Experiment Lifecycle**:
```
proposed → approved → running → completed/failed
    ↓                              ↓
 rejected              holdout evaluation (one-sided p-value)
                                   ↓
                     BH-FDR across the cycle (fail-closed:
                     no p-value → cannot survive)
                                   ↓
                  promoted (+ 7-day probation window opens)
                                   ↓
              apply → dataset rebuild → retrain → predict → backtest
                                   ↓
        probation-check (auto on every data-update) → passed / demoted
```

**Statistical guardrails** (FR-017/019/020):
- Trials and CV train on **pre-holdout rows only**; the holdout window
  (most recent ~6 weeks, stored per cycle) is touched exactly once, at
  final evaluation
- The holdout p-value is **one-sided** — only improvement over the
  baseline counts; a significantly worse experiment gets p ≈ 1
- Promotion is never based on best_score; best_score only selects the
  configuration that holdout evaluation then judges

**Search Strategies**:
- `GridSearch` - Exhaustive search over parameter grid
- `RandomSearch` - Random sampling from parameter space
- `BayesianSearch` - Adaptive optimization using Optuna's TPE sampler

**Search Strategies**:
- `GridSearch` - Exhaustive search over parameter grid
- `RandomSearch` - Random sampling from parameter space
- `BayesianSearch` - Adaptive optimization using Optuna's TPE sampler

### Database Schema

```sql
-- Core experiment tracking
experiments (
    id, name, experiment_type,
    config JSONB,              -- Type-specific configuration
    search_space JSONB,        -- Parameters to explore
    objective_metric,          -- What to optimize (sharpe_ratio, etc.)
    objective_direction,       -- maximize or minimize
    goal_target, goal_type,    -- Optional goal (achieve, improve)
    status,                    -- proposed, approved, running, completed, failed
    parent_experiment_id,      -- For chaining
    results JSONB,             -- Best params, metrics, holdout summary, applied artifacts
    best_score, total_trials, completed_trials,
    cycle_id,                  -- Cycle membership (FDR family)
    holdout_p_value,           -- One-sided; NULL = never survives FDR
    fdr_survived, promoted_at,
    probation_until, demoted_at
)

-- Cycle container (holdout window + FDR rate per family of experiments)
experiment_cycles (
    id, name, holdout_start_date, holdout_end_date,
    fdr_rate, status, summary JSONB
)

-- Individual trial results
experiment_trials (
    id, experiment_id, trial_number,
    params JSONB,              -- Parameters tested
    metrics JSONB,             -- All metrics (sharpe, return, drawdown)
    score,                     -- Primary optimization metric
    duration_seconds
)
```

### Experiment Chaining

Experiments can be chained where child experiments use parent outputs:

```
Experiment A (feature_selection)
    ↓ output: best_features
Experiment B (hyperparameter, uses best_features)
    ↓ output: best_model
Experiment C (threshold_tuning)
```

### Integration Points

- **Backtest Engine**: Strategy param experiments run backtests for evaluation
- **ML Pipeline**: Hyperparameter experiments train and evaluate models
- **MCP Server**: AI can propose experiments via MCP tools
- **CLI**: Full experiment management via `gefion experiment` commands

### Module Structure

```
src/gefion/experiments/
├── __init__.py          # Public API exports
├── core.py              # ExperimentConfig, ExperimentRunner (+ holdout step)
├── cycle_runner.py      # Autonomous cycle orchestration + FDR gate
├── production.py        # apply: winner → rebuild → retrain → predict → backtest
├── probation.py         # probation checks + demotion
├── statistical.py       # BH-FDR, one-sided holdout p-values
├── holdout.py           # Holdout window management
├── discovery.py         # Data/gap discovery
├── principles.py        # Principles catalog access
├── search.py            # GridSearch, RandomSearch, BayesianSearch
└── types/
    ├── holdout_eval.py          # Shared holdout-evaluation helpers
    ├── feature_engineering.py
    ├── hyperparameter.py        # + PurgedKFold
    ├── model_comparison.py
    ├── label_engineering.py
    ├── feature_selection.py
    ├── strategy_params.py
    └── pipeline.py
```

See [specs/004-autonomous-experiments/](../specs/004-autonomous-experiments/) for the full specification and [specs/004-autonomous-experiments/quickstart.md](../specs/004-autonomous-experiments/quickstart.md) for a hands-on walkthrough.

## Related Documentation

- **Experiments**: [specs/004-autonomous-experiments/](../specs/004-autonomous-experiments/) - AI experimentation framework spec
- **Backlog**: [.specify/memory/backlog.md](../.specify/memory/backlog.md) - Future features and backlog
- **ML Vision**: [archive/ml/HIGHLEVEL.md](archive/ml/HIGHLEVEL.md) - Long-term ML goals
- **Security Deep Dive**: [archive/ml/SECURITY_SANDBOXING.md](archive/ml/SECURITY_SANDBOXING.md) - Threat model and mitigation
- **Performance**: [PERFORMANCE.md](PERFORMANCE.md) - Optimization techniques
- **User Guide**: [USER_GUIDE.md](USER_GUIDE.md) - How to use Gefion
- **Troubleshooting**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
