# Changelog

## v0.2.0

### Quarterly Financials Pipeline
- New `quarterly_financials` hypertable for income statement, balance sheet, cash flow, and earnings data
- `gefion financials-backfill` command with parallel workers and progress reporting
- 4 new AlphaVantage endpoints and parsers
- Forward-fill feature function for daily fundamental features (PE ratio, market cap, EPS)
- `--quarterly` flag on `gefion fundamentals-update` for incremental refresh

### Fundamentals Update Performance
- Parallelized with 3 workers (configurable via `--workers`)
- Live progress table with rate, ETA, and error tracking
- Empty payload no-retry for ETFs/funds (saves ~45s per symbol)
- Skipped symbols marked to prevent retries for 30 days

### Tempo MCP Integration
- Enabled Tempo's built-in MCP server for native TraceQL queries
- `/gefion-perf` skill queries Tempo directly
- OTEL tracing added to MCP server tool calls
- Trace analysis helper script (`scripts/analyze_trace.py`)

### Data Update Reliability
- Auto-refresh process detection fixes (poll, PID, orphan state)
- Phase-aware progress counters with proper resets
- Event Log with single-line JSON parsing
- Weekend/market-closed warnings
- Large update warnings (suggests CLI for >200 symbols)

### Developer Experience
- CLI auto-loads `.env` (no more `source .env` before commands)
- OTEL late initialization (`reinitialize()` after `.env` load)
- Ask Gefion uses valid command list (prevents hallucinated CLI commands)

### Documentation
- README rewritten with screenshots and accurate CLI reference
- New `docs/DEVELOPMENT.md` (TDD, observability, performance workflow, versioning)
- `docs/OBSERVABILITY.md` rewritten with Tempo MCP integration
- Fixed 26 inaccuracies across 11 doc files
- Archived 6 redundant performance docs
- Version bumped to 0.2.0

## v0.1.0

Initial release.

- Data ingestion from AlphaVantage (5,800+ stocks, daily OHLCV)
- Feature engineering: technical indicators (RSI, MACD, Bollinger Bands, ADX, Stochastic, PSAR, EMA, SMA), cross-sectional rankings, price derivatives
- Database-first architecture: feature functions and definitions stored in PostgreSQL, exported to JSON
- Generic feature dispatcher with security sandbox
- ML pipeline: quantile regression (q10/q50/q90), trend classification (5-class), model ensembles, hyperparameter tuning (Optuna), SHAP feature importance, conformal calibration
- Autonomous experiment framework: propose, approve, run, evaluate with FDR correction, AI code generation via `claude -p`
- Backtesting engine: 8 rule-based strategies (momentum, mean reversion, MA crossover, breakout, pairs trading, RSI divergence, volatility contraction) + 2 ML strategies, execution modeling (costs, slippage, position sizing)
- Streamlit UI: 10 pages (Dashboard, System Ops, Data Management, Features, ML Pipeline, Backtesting, Experiments, Charts, Documentation, Settings) with contextual AI chat
- MCP server for natural language interface
- OpenTelemetry observability with Grafana Tempo
- D3.js interactive charts (13 chart types)
- TimescaleDB hypertables with compression policies
