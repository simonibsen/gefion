# Gefion

**Database-first ML platform for quantitative stock analysis.** Ingests price and fundamental data, computes features, trains models, runs autonomous experiments, and backtests trading strategies.

`v0.2.0` — Alpha, actively developed.

![Dashboard](images/ui-dashboard.png)

## What Gefion Does

- **Data ingestion** — daily OHLCV prices + quarterly financials from AlphaVantage for 5,800+ stocks
- **Feature engineering** — technical indicators (RSI, MACD, Bollinger Bands, etc.), cross-sectional rankings, and fundamental features (PE ratio, market cap) computed via a metadata-driven dispatcher
- **ML pipeline** — quantile regression (q10/q50/q90), trend classification (5-class), model ensembles, hyperparameter tuning, SHAP feature importance
- **Autonomous experiments** — propose, approve, run, and statistically evaluate experiments with FDR correction. AI-generated feature functions via code generation
- **Backtesting** — 8 rule-based + 2 ML strategies with execution modeling (costs, slippage, position sizing)
- **Streamlit UI** — 10 interactive pages with contextual AI chat ("Ask Gefion") on every page
- **Observability** — OpenTelemetry tracing to Grafana Tempo, performance feedback via TraceQL

## UI

<table>
<tr>
<td><img src="images/ui-data-management.png" width="400"><br><b>Data Management</b></td>
<td><img src="images/ui-features-definitions.png" width="400"><br><b>Features</b></td>
</tr>
<tr>
<td><img src="images/ui-ml-train.png" width="400"><br><b>ML Pipeline — Train</b></td>
<td><img src="images/ui-ml-evaluate.png" width="400"><br><b>ML Pipeline — Evaluate</b></td>
</tr>
<tr>
<td><img src="images/ui-experiments.png" width="400"><br><b>Experiments</b></td>
<td><img src="images/ui-charts.png" width="400"><br><b>Charts</b></td>
</tr>
<tr>
<td><img src="images/ui-backtesting.png" width="400"><br><b>Backtesting</b></td>
<td><img src="images/ui-system-operations.png" width="400"><br><b>System Operations</b></td>
</tr>
</table>

## Quick Start

### Prerequisites

- Python 3.10+
- Docker & Docker Compose
- AlphaVantage API key — get one at [alphavantage.co](https://www.alphavantage.co/support/#api-key)

### 1. Install

```bash
make venv
source .venv/bin/activate

cp .env.example .env
# Edit .env: set ALPHAVANTAGE_API_KEY=your_key_here
```

### 2. Start Services

```bash
# PostgreSQL (TimescaleDB) + Grafana Tempo + Grafana
docker compose up -d postgres
docker compose -f docker/tempo/docker-compose.tempo.yml up -d
```

### 3. Initialize

```bash
gefion init    # Creates tables, hypertables, indexes, seeds features
```

### 4. Ingest Sample Data

```bash
# Offline sample (no API key needed)
gefion prices-ingest --symbol IBM --input tests/fixtures/demo_time_series_daily_adjusted.json

# Compute features
gefion feat-compute --features indicator_rsi_14 --symbols IBM
```

### 5. Next Steps

- **Live data**: `gefion data-update --exchange NASDAQ --limit 50`
- **ML pipeline**: [docs/ML_QUICKSTART.md](docs/ML_QUICKSTART.md)
- **Full CLI reference**: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)

## Autonomous Experiments

Gefion includes an AI-powered experiment framework that systematically searches for improvements across the ML pipeline — better features, better model settings, better trading parameters — and statistically proves they're not just fitting to noise.

![Experiments](images/ui-experiments.png)

**How it works:**

1. **Discover** — scans available data, features, and a catalog of quantitative finance principles to identify testable hypotheses
2. **Propose** — generates experiments: new feature functions (via AI code generation), model hyperparameters, label engineering variants, or strategy parameters
3. **Run** — executes experiments with PurgedKFold cross-validation (time-series aware, prevents data leakage)
4. **Evaluate** — applies Benjamini-Hochberg FDR correction across all experiments in a cycle to filter false discoveries
5. **Promote** — surviving experiments are promoted: feature functions become active, model configs are adopted

```bash
# Run a full autonomous cycle
gefion experiment cycle-run --name exploration-1 --max-experiments 10

# Or step by step
gefion experiment discover                    # What can we test?
gefion experiment propose --type feature_engineering  # Generate an experiment
gefion experiment approve --id 1              # Review and approve
gefion experiment run --id 1                  # Execute
gefion experiment results --id 1              # Check results
```

AI-generated feature functions run in a security sandbox (whitelisted imports: numpy, pandas, scipy, sklearn, talib). Functions that survive FDR testing are stored in the database and automatically used in future model training.

## CLI Reference

### Data

| Command | Description |
|---------|-------------|
| `gefion data-update` | Update prices, compute features, refresh fundamentals |
| `gefion prices-ingest` | Ingest daily adjusted prices from AlphaVantage |
| `gefion universe-ingest` | Fetch listing status and ingest prices for filtered universe |
| `gefion fundamentals-update` | Update company metadata (sector, industry) from OVERVIEW |
| `gefion financials-backfill` | Backfill quarterly financials (income, balance sheet, cash flow, earnings) |
| `gefion data cull` | Delete old data in dependency order |

### Features

| Command | Description |
|---------|-------------|
| `gefion feat-compute` | Compute features using the generic dispatcher |
| `gefion feat-def-list` | List feature definitions |
| `gefion feat-def-show` | Show a single feature definition |
| `gefion feat-def-import` | Import feature definitions from JSON files |
| `gefion feat-def-export` | Export feature definitions to JSON files |
| `gefion feat-fx-list` | List registered feature functions |
| `gefion feat-fx-import` | Import feature functions from JSON files |
| `gefion feat-fx-export` | Export feature functions to JSON files |
| `gefion feat-drop` | Drop feature definitions and their data |
| `gefion feat-trim` | Trim computed features by date range |
| `gefion cross-sectional-compute` | Compute cross-sectional rankings |

### ML Pipeline

| Command | Description |
|---------|-------------|
| `gefion ml dataset-build` | Build training dataset from prices + features |
| `gefion ml train` | Train quantile regression model |
| `gefion ml train-classifier` | Train 5-class trend classifier |
| `gefion ml train-ensemble` | Train multi-algorithm ensemble |
| `gefion ml predict` | Generate quantile predictions |
| `gefion ml predict-classifier` | Generate trend class predictions |
| `gefion ml predict-ensemble` | Generate ensemble predictions |
| `gefion ml eval` | Evaluate model calibration and loss |
| `gefion ml tune` | Hyperparameter tuning with Optuna |
| `gefion ml e2e-test` | Run full pipeline end-to-end test |
| `gefion ml feature-importance` | SHAP-based feature importance |
| `gefion ml calibrate` | Conformal prediction calibration |

### Experiments

| Command | Description |
|---------|-------------|
| `gefion experiment discover` | Discover data sources and experiment opportunities |
| `gefion experiment propose` | Propose a new experiment |
| `gefion experiment approve` | Approve a proposed experiment |
| `gefion experiment run` | Run an approved experiment |
| `gefion experiment results` | View experiment results |
| `gefion experiment cycle-start` | Start an autonomous experiment cycle |
| `gefion experiment cycle-run` | Run a full autonomous cycle (discover → propose → run → evaluate) |

### Backtesting & Strategies

| Command | Description |
|---------|-------------|
| `gefion backtest run` | Run backtest for a trading strategy |
| `gefion backtest compare` | Compare multiple strategies side-by-side |
| `gefion strategy list` | List registered strategies |
| `gefion strategy create-config` | Create a strategy configuration |

### System

| Command | Description |
|---------|-------------|
| `gefion init` | Initialize database schema and seed features |
| `gefion health` | Check infrastructure health |
| `gefion db-health` | Database health report |
| `gefion db-migrate` | Run database migrations |
| `gefion span-check` | Check recent traces for slow operations |
| `gefion ui` | Launch Streamlit web UI |
| `gefion backup` / `gefion restore` | Backup and restore database |

Full CLI reference with flags and examples: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)

## Architecture

```mermaid
graph LR
    AV[AlphaVantage] -->|prices| OHLCV[(stock_ohlcv)]
    AV -->|quarterly| QF[(quarterly_financials)]
    OHLCV --> FC[feat-compute]
    QF --> FC
    FC --> CF[(computed_features)]
    OHLCV & CF -->|join| DS[ml dataset-build]
    DS --> TR[ml train]
    TR --> MOD[(ml_models)]
    MOD --> PR[ml predict]
    PR --> PRED[(predictions)]
    PRED --> EV[ml eval]
    PRED --> BT[backtest run]
```

Key concepts:

- **Database-first**: features, functions, and configuration live in the database. JSON files in `feature-functions/` and `feature-definitions/` are exports, not primary sources
- **Metadata-driven features**: `feature_definitions` describes *what* to compute; `feature_functions` stores *how*. The dispatcher routes by `function_name`
- **Hypertables**: TimescaleDB partitions time-series tables (`stock_ohlcv`, `computed_features`, `quarterly_financials`, `predictions`) for efficient queries
- **Sandboxed functions**: compute functions run in a security sandbox (whitelisted imports only)

Full architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Development

Gefion enforces TDD and observability via automated hooks.

```
1. Start services       docker compose up -d  (or /gefion-services start)
2. Write failing tests  tests/ before src/
3. Implement            Minimum code to pass
4. Instrument           from gefion.observability import create_span
5. Check traces         /gefion-perf or gefion span-check
6. Commit               Tests + implementation together
```

Full guide: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)

## Documentation

| Doc | Contents |
|-----|----------|
| [USER_GUIDE.md](docs/USER_GUIDE.md) | Full CLI reference with flags and examples |
| [ML_QUICKSTART.md](docs/ML_QUICKSTART.md) | End-to-end ML workflow |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, ER diagram, dispatcher pattern |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | TDD workflow, observability, performance, hooks |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | OpenTelemetry + Tempo setup and usage |
| [BACKTESTING.md](docs/BACKTESTING.md) | Strategy reference and backtesting guide |
| [STRATEGIES.md](docs/STRATEGIES.md) | All 10 trading strategies documented |
| [MCP_WORKFLOWS.md](docs/MCP_WORKFLOWS.md) | Natural language interface workflows |
| [MCP_PRODUCTION.md](docs/MCP_PRODUCTION.md) | MCP server production deployment |
| [E2E_TEST_GUIDE.md](docs/E2E_TEST_GUIDE.md) | Pipeline validation guide |
| [DATABASE_MIGRATIONS.md](docs/DATABASE_MIGRATIONS.md) | Migration system reference |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and solutions |

## Running Tests

```bash
# Unit tests (no database required)
make test

# Full suite including database tests
make test-db

# Manual
ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://gefion:gefionpass@localhost:6432/gefion" \
  OTEL_ENABLED=false pytest tests/
```

Test suite: 1,971 tests (as of v0.2.0)

## License

[Elastic License 2.0 (ELv2)](LICENSE) — free to use, modify, and distribute. You may not offer it as a hosted/managed service.
