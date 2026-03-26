# Gefion MCP Server

Natural language interface to the g2 ML platform using the Model Context Protocol (MCP).

## What is this?

The G2 MCP Server lets you interact with Gefion's ML pipeline through natural language in Claude Desktop, while still having full access to the CLI for scripting and automation.

**Example conversation:**
```
You: "Build a dataset with AAPL, MSFT, GOOGL for 7 and 30 day horizons"
Claude: [Creates dataset using g2 ml dataset-build]

You: "Train a quantile regression model on that dataset"
Claude: [Trains model using g2 ml train]

You: "What are the latest predictions for AAPL?"
Claude: [Queries database and shows predictions]
```

## Features

### ML Workflow Tools
- `ml_dataset_build` - Create training datasets with features and labels
- `ml_train` - Train quantile regression models (sklearn/XGBoost/LightGBM)
- `ml_train_classifier` - Train 5-class trend classifiers (strong_down to strong_up)
- `ml_predict` - Generate multi-horizon predictions (7/30/90 days)
- `ml_predict_classifier` - Generate trend class predictions with probabilities
- `ml_eval` - Evaluate model calibration and performance

### Database Query Tools
- `query_predictions` - Search predictions by symbol, model, date, horizon
- `query_model_performance` - View calibration metrics and evaluation results
- `query_database` - Execute read-only SQL for data exploration and analysis

### Data Management Tools
- `data_update` - Update prices and compute features (time-aware: before 4pm ET = yesterday's data, after 4pm ET = today's data)
- `features_list` - List all registered technical indicators + cross-sectional features (percentile ranks, z-scores)
- `cross_sectional_compute` - Compute rankings for a feature across comparison groups (market, sector, industry)

### Observability Tools
- `span_check` - Check recent traces for performance monitoring and debugging (backend-agnostic)
- `trace_search` - Search for traces by criteria (tags, duration, service name)
- `trace_detail` - Get detailed trace information for a specific trace ID
- `trace_compare` - Compare two traces to quantify performance improvements

### Infrastructure Tools
- **`system_status`** - **Comprehensive system status with intelligent suggestions** (use this first!)
  * Infrastructure health (PostgreSQL, Tempo, Docker)
  * Data freshness analysis (days since last update)
  * Missing components detection (features, data)
  * Feature/function registration detection (files on disk not in DB)
  * Prioritized issues (critical/high/medium/low)
  * Actionable suggestions with exact commands
  * Ordered next steps workflow
- **`dev_status`** - **Development roadmap and next steps guidance**
  * Parses DEVELOPMENT.md, .specify/memory/progress.md, .specify/memory/backlog.md
  * Current development phase identification
  * Completed/in-progress/planned items tracking
  * Strategic path options (Trading-First/ML-First/Scale-First)
  * Ready-to-start tasks with prerequisites met
  * Development rules reminders (TDD, commit format)
  * Quick wins identification (high priority, low effort)
- `health_check` - Quick infrastructure health check only (use system_status for full picture)
- `docker_status` - Docker-specific status check (use system_status for full picture)

## Intelligent System Status

The `system_status` tool provides comprehensive analysis with actionable suggestions:

**Example Output:**
```json
{
  "status": "needs_attention",
  "summary": "3 issue(s) found",
  "infrastructure": {
    "docker": {"running": true},
    "postgres": {"running": true},
    "tempo": {"running": true}
  },
  "data": {
    "stocks": 2,
    "ohlcv_rows": 3,
    "latest_date": "2024-01-01",
    "days_since_update": 358,
    "feature_rows": 0
  },
  "issues": [
    {
      "type": "stale_data",
      "description": "Price data is 358 days old (last: 2024-01-01)",
      "priority": "high",
      "command": "g2 data-update --exchange NASDAQ --limit 10"
    },
    {
      "type": "unregistered_feature_definitions",
      "description": "18 feature definition(s) on disk not imported to database",
      "priority": "medium",
      "command": "g2 feat-def-import --directory feature-definitions"
    },
    {
      "type": "no_features",
      "description": "Features not computed (0 rows)",
      "priority": "medium",
      "command": "g2 feat-compute --symbols AAPL,MSFT --all-features"
    }
  ],
  "next_steps": [
    "1. Update price data: g2 data-update",
    "2. Compute features: g2 feat-compute",
    "3. Build ML dataset: g2 ml dataset-build",
    "4. Train model: g2 ml train"
  ]
}
```

**Use Cases:**
- "What should I do next?" → Shows ordered workflow
- "Is my data current?" → Analyzes staleness
- "Why aren't predictions working?" → Identifies missing components
- "How do I get started?" → Guides through setup

## Development Roadmap Guidance

The `dev_status` tool analyzes development documentation to provide roadmap guidance:

**Example Output:**
```json
{
  "success": true,
  "current_phase": "Strategic Direction Choice (Path A/B/C)",
  "development_rules": {
    "tdd_required": true,
    "commit_format": "conventional commits",
    "test_minimum": 488,
    "no_ai_attribution": true
  },
  "completed_items": [
    {
      "number": 1,
      "title": "Fix Trim Command Behavior",
      "status": "completed",
      "priority": "high",
      "effort": "2-3 days"
    }
  ],
  "in_progress_items": [],
  "planned_items": [
    {
      "number": 13,
      "title": "Strategy Comparison Framework",
      "status": "planned",
      "priority": "medium",
      "effort": "1-2 weeks",
      "path": "A"
    }
  ],
  "strategic_paths": {
    "A": {
      "name": "Trading-First (Production Trading Platform)",
      "goal": "Ship a complete, production-ready trading platform",
      "timeline": "6-8 weeks",
      "best_for": "Users who want to make real trading decisions"
    }
  },
  "recommended_next_steps": [
    {
      "item": "#13",
      "title": "Strategy Comparison Framework",
      "priority": "medium",
      "effort": "1-2 weeks",
      "path": "A"
    }
  ],
  "quick_wins": [
    {
      "number": 15,
      "title": "Parquet Dataset Format",
      "priority": "high",
      "effort": "1 week"
    }
  ]
}
```

**Use Cases:**
- "What should I work on next?" → Shows prioritized tasks
- "Am I ready for Item #13?" → Check prerequisites (implicit via status)
- "Show high-priority tasks" → Filter by priority
- "What's left in Path A?" → Filter by strategic path
- "What are quick wins?" → High priority, low effort items
- "What are the TDD requirements?" → Development rules reminder

**Filters Available:**
- `path`: A (Trading), B (ML), C (Scale)
- `status`: completed, in_progress, planned
- `priority`: high, medium, low

## Service Health Checks

The MCP server automatically checks required services before executing tools and provides helpful error messages if services are down.

### Intelligent Error Messages

If a required service is not running, you'll get actionable suggestions:

**PostgreSQL not running:**
```
❌ POSTGRES is not available

Status: PostgreSQL is not running

Start PostgreSQL:
  docker compose up -d postgres

Check status:
  docker compose ps postgres
```

**Tempo not running:**
```
❌ TEMPO is not available

Status: Tempo is not running or not accessible

Start Tempo (for tracing):
  cd docker/tempo
  docker compose -f docker-compose.tempo.yml up -d

Or disable tracing:
  export OTEL_ENABLED=false
```

### Health Check Performance

- **Caching**: Health status cached for 60 seconds (minimal overhead)
- **Lazy checking**: Only checks required services for each tool
- **Fast failure**: Immediate helpful error if service down (~50ms vs 5-30s timeout)
- **Overhead**: <0.2% of typical operation time when cached

### Service Dependencies

- **PostgreSQL required**: All ML tools, data_update, query tools
- **Tempo required**: Observability tools (span_check, trace_*)
- **Docker recommended**: For managing PostgreSQL and Tempo containers

## Installation

### Quick Setup (Recommended)

The easiest way to set up the MCP server:

1. **Install g2 with ML dependencies:**
   ```bash
   cd /path/to/g2
   pip install -e ".[ml_extended]"
   ```

2. **Install MCP server dependencies:**
   ```bash
   cd mcp-server
   pip install -r requirements.txt
   ```

3. **Run the setup command:**
   ```bash
   g2 mcp-setup
   ```

   This automatically:
   - Detects your platform and config file location
   - Finds the correct Python interpreter and server path
   - Creates or updates your MCP configuration
   - Uses DATABASE_URL and ALPHAVANTAGE_API_KEY from environment if set

   Optional flags:
   ```bash
   g2 mcp-setup --db-url postgresql://user:pass@localhost/db
   g2 mcp-setup --api-key your_api_key_here
   g2 mcp-setup --force  # Overwrite existing configuration
   ```

4. **Start PostgreSQL:**
   ```bash
   cd /path/to/g2
   docker compose up -d postgres
   ```

5. **Restart your AI assistant**

   The g2 tools should now appear in the tool list.

### Manual Setup

If you prefer to configure manually:

1. **Install dependencies** (same as quick setup steps 1-2)

2. **Edit your AI assistant config file:**
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
   - Linux: `~/.config/Claude/claude_desktop_config.json`

   Add this configuration:
   ```json
   {
     "mcpServers": {
       "g2": {
         "command": "python",
         "args": ["/absolute/path/to/g2/mcp-server/server.py"],
         "env": {
           "DATABASE_URL": "postgresql://gefion:gefionpass@localhost:5432/gefion",
           "ALPHAVANTAGE_API_KEY": "your_api_key_here"
         }
       }
     }
   }
   ```

3. **Follow steps 4-5 from Quick Setup**

### Option 2: Docker Deployment (Production)

1. **Build the Docker image:**
   ```bash
   cd /path/to/g2
   docker build -t gefion-mcp-server -f mcp-server/Dockerfile .
   ```

2. **Start services:**
   ```bash
   cd mcp-server
   docker-compose up -d
   ```

3. **Configure Claude Desktop to use Docker:**
   ```json
   {
     "mcpServers": {
       "g2": {
         "command": "docker",
         "args": [
           "compose",
           "-f", "/absolute/path/to/g2/mcp-server/docker-compose.yml",
           "run", "--rm", "mcp-server"
         ]
       }
     }
   }
   ```

4. **Restart Claude Desktop**

### Option 3: Docker with GPU Support

If you have an NVIDIA GPU and want to use XGBoost/LightGBM with GPU acceleration:

1. **Install nvidia-container-toolkit:**
   ```bash
   # Ubuntu/Debian
   sudo apt-get install -y nvidia-container-toolkit
   sudo systemctl restart docker
   ```

2. **Update docker-compose.yml:**
   ```yaml
   mcp-server:
     deploy:
       resources:
         reservations:
           devices:
             - driver: nvidia
               count: 1
               capabilities: [gpu]
   ```

3. **Build and run:**
   ```bash
   docker-compose up -d
   ```

## Usage Examples

### Quick Start Workflow

Once configured, open Claude Desktop and try these prompts:

**1. Build a dataset:**
```
Build a dataset named "test_mvp" version "v1" with symbols AAPL, MSFT, GOOGL
for horizons 7 and 30 days. Export the CSVs to datasets/test_mvp.
```

**2. Train a model:**
```
Train a quantile regression model on dataset "test_mvp" version "v1".
Name the model "test_model" with version "20251214".
Save artifacts to the models directory.
```

**3. Generate predictions:**
```
Generate predictions for AAPL, MSFT, GOOGL using model "test_model"
version "20251214" for today's date.
```

**4. Query results:**
```
Show me the latest predictions for AAPL from the test_model.
```

**5. Evaluate performance:**
```
Evaluate test_model version 20251214 from 2024-01-01 to 2024-12-01.
```

### Advanced Queries

**Find high-conviction predictions:**
```
Show me predictions where q50 (median return) is greater than 3%
and the uncertainty (IQR) is less than 2%.
```

**Compare model performance:**
```
Show me the calibration metrics for all models evaluated in the last month.
```

**Data updates:**
```
Update prices and features for NASDAQ stocks, limit to 100 symbols,
use local computation.
```

## Architecture

```
┌─────────────────────┐
│  Claude Desktop     │  ← Natural language interface
└──────────┬──────────┘
           │ MCP stdio protocol
           ▼
┌─────────────────────┐
│  MCP Server         │  ← Translates to g2 commands
│  (server.py)        │
└──────────┬──────────┘
           │ subprocess.run(['g2', 'ml', ...])
           ▼
┌─────────────────────┐
│  g2 CLI             │  ← ML pipeline (unchanged)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  TimescaleDB        │  ← Database
└─────────────────────┘
```

**Key Points:**
- MCP server is a thin wrapper around g2 CLI
- All g2 commands still work directly (for scripts, cron jobs, etc.)
- Natural language → MCP tools → `gefion --json` commands
- Database queries use psql for flexibility

## Tool Reference

### ml_dataset_build

Build ML training dataset with features and labels.

**Parameters:**
- `name` (required): Dataset name
- `version` (required): Dataset version (e.g., "v1")
- `symbols`: Comma-separated symbols (e.g., "AAPL,MSFT,GOOGL")
- `exchange`: Exchange name (e.g., "NASDAQ") - alternative to symbols
- `limit`: Limit number of symbols from exchange
- `horizons`: Forecast horizons in days (default: "7,30,90")
- `weak_thresholds`: Weak move thresholds (default: "0.02,0.05,0.10")
- `strong_thresholds`: Strong move thresholds (default: "0.05,0.10,0.20")
- `out_dir`: Output directory (default: "datasets")
- `export`: Export CSVs (default: true)

**Example prompt:**
> "Build dataset 'nasdaq_100' version 'v1' for NASDAQ exchange, limit 100 symbols,
> horizons 7,30,90 days, export to datasets/nasdaq_100"

### ml_train

Train quantile regression models for multi-horizon prediction.

**Parameters:**
- `dataset_name` (required): Dataset name
- `dataset_version` (required): Dataset version
- `model_name` (required): Model name
- `model_version` (required): Model version (e.g., "20251214")
- `algorithm`: "quantile_regression" (default), "xgboost", or "lightgbm"
- `out_dir`: Output directory (default: "models")

**Example prompt:**
> "Train a model named 'prod_model' version '20251214' on dataset 'nasdaq_100' v1
> using XGBoost algorithm"

### ml_predict

Generate predictions for symbols on a specific date.

**Parameters:**
- `model_name` (required): Model name
- `model_version` (required): Model version
- `prediction_date` (required): Date in YYYY-MM-DD format
- `symbols`: Comma-separated symbols
- `exchange`: Exchange name (alternative to symbols)
- `limit`: Limit symbols from exchange

**Example prompt:**
> "Generate predictions for AAPL, MSFT, GOOGL using prod_model version 20251214
> for 2024-12-14"

### ml_eval

Evaluate model performance on historical predictions.

**Parameters:**
- `model_name` (required): Model name
- `model_version` (required): Model version
- `start_date` (required): Start date (YYYY-MM-DD)
- `end_date` (required): End date (YYYY-MM-DD)

**Example prompt:**
> "Evaluate prod_model version 20251214 from 2024-01-01 to 2024-12-01"

### ml_train_classifier

Train multi-class trend classifier (5-class: strong_down, weak_down, flat, weak_up, strong_up).

**Parameters:**
- `dataset_name` (required): Dataset name
- `dataset_version` (required): Dataset version
- `model_name` (required): Model name
- `model_version` (required): Model version (e.g., "20251214")
- `algorithm`: "xgboost" (default) or "lightgbm"
- `out_dir`: Output directory (default: "models")

**Example prompt:**
> "Train a trend classifier named 'trend_model' version '20251218' on dataset 'nasdaq_100' v1 using XGBoost"

### ml_predict_classifier

Generate trend class predictions for symbols on a specific date.

**Parameters:**
- `model_name` (required): Model name
- `model_version` (required): Model version
- `prediction_date` (required): Date in YYYY-MM-DD format
- `symbols`: Comma-separated symbols
- `exchange`: Exchange name (alternative to symbols)
- `limit`: Limit symbols from exchange

**Example prompt:**
> "Generate trend predictions for AAPL, MSFT using trend_model version 20251218 for today"

### query_predictions

Query stored predictions from database.

**Parameters:**
- `symbol`: Filter by symbol (e.g., "AAPL")
- `model_name`: Filter by model name
- `start_date`: Start date (YYYY-MM-DD)
- `end_date`: End date (YYYY-MM-DD)
- `horizon`: Filter by horizon (7, 30, or 90)
- `limit`: Limit results (default: 100)

**Example prompt:**
> "Show predictions for AAPL from the last week, 7-day horizon only"

### query_model_performance

Query model performance metrics.

**Parameters:**
- `model_name`: Filter by model name
- `limit`: Limit results (default: 10)

**Example prompt:**
> "Show performance metrics for all models"

### data_update

Update prices and features for an exchange.

**Parameters:**
- `exchange`: Exchange name (default: "NASDAQ")
- `timeframe`: "auto", "compact", or "full" (default: "auto")
- `local`: Use local computation (default: true)
- `limit`: Limit number of symbols

**Example prompt:**
> "Update NASDAQ data for 50 symbols using local computation"

### features_list

List all registered feature definitions.

**Example prompt:**
> "List all available features"

### cross_sectional_compute

Compute cross-sectional rankings for a feature across comparison groups.

**Parameters:**
- `feature_name` (required): Feature to rank (e.g., "indicator_rsi_14")
- `date`: Target date (YYYY-MM-DD), defaults to latest available
- `include_market`: Include market-wide rankings (default: true)
- `include_sectors`: Include sector-relative rankings (default: true)
- `include_industries`: Include industry-relative rankings (default: false)

**What it does:**
1. Fetches latest feature values for all stocks
2. Computes rankings within each comparison group:
   - `market` - rank vs all stocks
   - `sector:X` - rank vs same sector peers (e.g., `sector:TECHNOLOGY`)
   - `industry:X` - rank vs same industry peers
3. Stores results in `cross_sectional_features` table with rank and percentile

**Example prompts:**
> "Compute cross-sectional rankings for RSI"

> "Rank stocks by MACD within their sectors"

> "Generate market and industry rankings for all features"

**Output example:**
```json
{
  "success": true,
  "feature_name": "indicator_rsi_14",
  "date": "2025-12-24",
  "stocks_count": 100,
  "total_rankings": 156,
  "groups": ["market", "sector:TECHNOLOGY", "sector:HEALTHCARE", "sector:FINANCE"]
}
```

**Prerequisite:** Stocks need sector/industry data. Use `fundamentals-update` CLI command to load this data from AlphaVantage.

### query_database

Execute read-only SQL queries for data exploration and analysis.

**Parameters:**

- `sql` (required): SQL query to execute (SELECT only)
- `description`: Human-readable description of what you're querying

**Safety Features:**

- ✅ Read-only: Only SELECT and WITH (CTEs) allowed
- ✅ Blocks dangerous keywords: DROP, DELETE, UPDATE, INSERT, etc.
- ✅ Auto-limit: Adds LIMIT 1000 if missing
- ✅ 60-second timeout

**Example prompts:**

**Data coverage:**
> "How many stocks do we have price data for?"

**Performance analysis:**
> "Show me the top 10 stocks by number of computed features"

**ML exploration:**
> "What's the distribution of q50 predictions for AAPL in the last month?"

**Schema discovery:**
> "What tables exist in the database?"

**Advanced analytics:**
> "Show me stocks where the 7-day q50 prediction is greater than 3% and the IQR is less than 2%"

**Example conversation:**

```text
You: "Explore the performance of price ingestion - how much data coverage do we have?"

Claude: [Runs query_database with SQL]:
SELECT
    COUNT(DISTINCT s.id) as total_stocks,
    COUNT(*) as total_records,
    MIN(o.date) as earliest_date,
    MAX(o.date) as latest_date
FROM stocks s
JOIN stock_ohlcv o ON s.id = o.data_id

Results:
- 5,623 stocks tracked
- 8.2M price records
- Date range: 2020-01-01 to 2024-12-14

You: "How many were updated today?"

Claude: [Runs another query to check today's coverage]

Results:
- 4,892 stocks updated today (87% coverage)
```

## Troubleshooting

### MCP server not appearing in Claude Desktop

1. **Check config path:**
   ```bash
   # macOS
   cat ~/Library/Application\ Support/Claude/claude_desktop_config.json

   # Verify g2 path is correct and absolute
   ```

2. **Check logs:**
   - Open Claude Desktop
   - Check Developer Console (Help → View Logs)
   - Look for MCP connection errors

3. **Test server manually:**
   ```bash
   cd /path/to/g2/mcp-server
   python server.py
   # Should wait for input (MCP uses stdio)
   # Press Ctrl+C to exit
   ```

### "Command not found: g2"

**Cause:** g2 not in PATH or not installed in the Python environment.

**Solutions:**

1. **Check installation:**
   ```bash
   which g2
   pip show g2
   ```

2. **Use absolute path in config:**
   ```json
   {
     "command": "/absolute/path/to/.venv/bin/python",
     "args": ["/absolute/path/to/g2/mcp-server/server.py"]
   }
   ```

3. **For Docker, rebuild image:**
   ```bash
   docker build -t gefion-mcp-server -f mcp-server/Dockerfile .
   ```

### Database connection errors

1. **Check DATABASE_URL in config:**
   ```json
   "env": {
     "DATABASE_URL": "postgresql://gefion:gefionpass@localhost:5432/gefion"
   }
   ```

2. **Verify PostgreSQL is running:**
   ```bash
   docker compose ps postgres
   psql postgresql://gefion:gefionpass@localhost:5432/gefion -c "SELECT 1"
   ```

3. **Check network (Docker):**
   - If using Docker, ensure MCP server container can reach postgres container
   - Use `postgres` hostname, not `localhost`

### Predictions not found

**Cause:** No predictions exist for the query parameters.

**Solution:**
1. Generate predictions first:
   ```
   Generate predictions for AAPL using model_name version_YYYYMMDD for today
   ```

2. Check date range:
   ```
   Show me all predictions from the last month
   ```

### Model artifacts not found

**Cause:** Model path structure mismatch or training didn't complete.

**Solutions:**

1. **Check model directory:**
   ```bash
   ls -la models/
   # Should see: model_name_version_h7/, model_name_version_h30/, etc.
   ```

2. **Verify training completed:**
   ```
   Show me model performance for model_name
   ```

3. **Retrain if needed:**
   ```
   Train a new model on the dataset
   ```

## Direct CLI Access

You can still use g2 CLI directly for scripting, automation, and batch operations:

```bash
# Cron job example: daily predictions
0 9 * * * cd /path/to/g2 && .venv/bin/g2 ml predict \
  --model-name prod_model --model-version 20251214 \
  --prediction-date $(date +\%Y-\%m-\%d) \
  --exchange NASDAQ --limit 500

# Batch dataset building
for exchange in NASDAQ NYSE; do
  g2 ml dataset-build --name "${exchange}_full" --version v1 \
    --exchange $exchange --export
done

# Weekly model retraining
g2 ml train --dataset-name nasdaq_full --dataset-version v1 \
  --model-name prod_model --model-version $(date +\%Y\%m\%d)
```

## Development

### Running tests

```bash
cd mcp-server
pytest -v
```

### Adding new tools

1. Edit `server.py`
2. Add tool definition to `list_tools()`
3. Add implementation function (e.g., `_my_new_tool()`)
4. Add handler in `call_tool()`
5. Test with Claude Desktop

### Debugging

Set environment variable for verbose logging:

```bash
export MCP_DEBUG=1
python server.py
```

## Security Notes

- **Database credentials:** Never commit `.env` or config files with real credentials
- **API keys:** Use environment variables, not hardcoded values
- **SQL injection:** The query tools use parameterized queries (safe)
- **Sandboxing:** MCP server runs with same permissions as g2 CLI

## Performance

- **MCP overhead:** ~10-50ms per tool call (negligible)
- **Database queries:** <100ms for typical predictions query
- **Training:** Same as direct CLI (5-15s for small datasets)
- **Predictions:** Same as direct CLI (~450ms for 5 symbols)

## Contributing

See main g2 CONTRIBUTING.md for development workflow.

For MCP server specific issues:
1. Check existing issues: https://github.com/your-org/g2/issues
2. Include MCP server version, Claude Desktop version, OS
3. Attach logs from Claude Desktop developer console

## License

Same as g2 project (see LICENSE file in root directory).

## Resources

- **g2 Documentation:** [../docs/](../docs/)
- **MCP Protocol:** https://modelcontextprotocol.io/
- **Claude Desktop:** https://claude.ai/download
- **g2 ML Quickstart:** [../docs/ML_QUICKSTART.md](../docs/ML_QUICKSTART.md)
