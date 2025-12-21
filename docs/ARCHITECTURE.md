# g2 Architecture

## Overview

g2 is a database-first technical analysis platform with versioned feature engineering capabilities. Feature definitions and custom feature implementations are stored in the database and exported to git for version control.

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

- **Sandboxing** ([dispatcher.py](../src/g2/ingest/dispatcher.py)):
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
g2 feat-fx-export --dir feature-functions
# Creates: feature-functions/indicator_v1.0.json

# Export definitions to git
g2 feat-def-export --dir feature-definitions
# Creates: feature-definitions/indicator_rsi_14.json

# Import from git (upsert on name+version)
g2 feat-fx-import --dir feature-functions
g2 feat-def-import --dir feature-definitions
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

## Future Enhancements

### Cross-Sectional Features

For sector-relative and market-relative features (e.g., stock return vs sector average), a dedicated table structure is planned:

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

## Related Documentation

- **ML Roadmap**: [ML_ROADMAP.md](ML_ROADMAP.md) - Future features and enhancements
- **ML Vision**: [archive/ml/HIGHLEVEL.md](archive/ml/HIGHLEVEL.md) - Long-term ML goals
- **Security Deep Dive**: [archive/ml/SECURITY_SANDBOXING.md](archive/ml/SECURITY_SANDBOXING.md) - Threat model and mitigation
- **Performance**: [PERFORMANCE.md](PERFORMANCE.md) - Optimization techniques
- **User Guide**: [USER_GUIDE.md](USER_GUIDE.md) - How to use g2
- **Troubleshooting**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
