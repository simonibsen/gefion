# g2 Project Status

**Last Updated**: 2026-03-01

## Current Capabilities

### Data Infrastructure
- 5,600+ NASDAQ stocks tracked daily
- TimescaleDB for time-series storage (hypertables on `stock_ohlcv`, `computed_features`)
- AlphaVantage API integration with rate limiting (1.0s min spacing, ~68 calls/min)
- Optimized ingestion: 91% skip rate, ~5 min full update
- Time-aware filtering: before 4pm ET = yesterday's data, after 4pm ET = today's data

### Feature Engineering
- 17 technical indicators computed locally (RSI, MACD, Bollinger Bands, ADX, PSAR, Stochastic, etc.)
- DB-first architecture: functions and definitions stored in database, exported to git
- Sandboxed execution: feature functions run in restricted Python environment
- Cross-sectional features: market-relative percentile ranks, z-scores
- Versioned exports: one JSON file per function/definition

### ML Pipeline (Production Ready)
- Quantile regression: q10/q50/q90 predictions for 7/30/90-day horizons
- Trend classification: 5-class classifier (strong_down to strong_up)
- Model ensembles: weighted averaging of multiple algorithms
- Conformal calibration (`g2 ml calibrate`): additive shift corrections for nominal quantile coverage
- Feature importance: SHAP-based analysis
- Hyperparameter tuning: Bayesian optimization via Optuna
- Warm-start retraining: 10-100x faster for XGBoost/LightGBM
- Parquet and CSV export formats

### Trading & Backtesting
- 7 strategies: momentum, mean_reversion, ma_crossover, breakout, pairs_trading, rsi_divergence, volatility_contraction
- Execution modeling: transaction costs, slippage, position sizing
- Strategy comparison: side-by-side performance metrics
- Experiment framework: propose/approve/run with grid/random/bayesian search

### Interfaces
- Full CLI (`g2` command with subcommands)
- MCP server for natural language interaction (51 tools, RBAC with operator/developer roles)
- Claude Code skills: `/g2-dev` (development), `/g2` (operations), `/g2-services` (infrastructure)
- Textual TUI (in development on `siUI` branch)

### UI Error Feedback Loop
- Errors logged to `~/.g2/ui_errors.jsonl` during UI sessions (background process failures, exceptions)
- On `g2 ui` exit, prints error summary to stdout — visible to Claude Code for diagnosis
- `g2.ui.errors` module: `log_ui_error()`, `read_session_errors()`, `clear_errors()`

### Testing
- 1282 tests passing, 14 skipped
- Database tests require `ENABLE_DB_TESTS=1`
- Full suite: `ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://g2:g2pass@localhost:6432/g2" OTEL_ENABLED=false .venv/bin/python -m pytest`

### Data Coverage (as of 2026-02-16)
- Price data: 1999-11-01 to 2026-01-30
- Symbols: 102 (NASDAQ)
