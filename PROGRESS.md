# g2 Project Status

## Current Capabilities

g2 is a production-ready database-first technical analysis platform with:

### Data Infrastructure

- **5,600+ NASDAQ stocks** tracked daily
- **TimescaleDB** for efficient time-series storage
- **AlphaVantage API** integration with rate limiting
- **Optimized ingestion**: 91% skip rate, ~5 min full update

### Feature Engineering

- **17 technical indicators** computed locally (RSI, MACD, Bollinger Bands, ADX, PSAR, Stochastic, etc.)
- **DB-first architecture**: Functions and definitions stored in database, exported to git
- **Sandboxed execution**: Feature functions run in restricted Python environment
- **Versioned exports**: One JSON file per function/definition for clean git diffs

### CLI Tools

**Data Pipeline:**

- `g2 data-update` - Update prices and compute indicators (full pipeline)
- `g2 prices-ingest` - Ingest specific symbols from AlphaVantage
- `g2 feat-compute` - Compute features for specific symbols

**Feature Management:**

- `g2 feat-fx-export/import` - Version control for feature functions
- `g2 feat-def-export/import` - Version control for feature definitions
- `g2 feat-fx-list` - List registered functions
- `g2 feat-def-list` - List feature definitions

**ML Workflow (Production Ready):**

- `g2 ml init` - Initialize ML schema tables
- `g2 ml device` - Check GPU/CPU availability
- `g2 ml dataset-build` - Create dataset manifests + export CSVs
- `g2 ml train` - Train quantile regression models (sklearn/XGBoost/LightGBM)
- `g2 ml predict` - Generate multi-horizon predictions and store in DB
- `g2 ml eval` - Evaluate calibration metrics and performance

### Performance

- **Parallel processing**: Adaptive worker scaling (2-16 workers)
- **Bulk operations**: Single query filters 5,600 symbols in <1s
- **Rate limiting**: 1.0s minimum spacing prevents API throttling
- **Batch inserts**: 200-row chunks for 10-50x faster writes

## Recent Changes

### December 17, 2025

**Phase 1 Completion - All Tactical Items (#1-10) Complete:**

- **Cross-Sectional Features (Item #7)**: Implemented market-relative features (return_vs_market, volume_vs_market, rank_by_return) with database storage in cross_sectional_features hypertable
- **Backtesting Engine (Item #8)**: Complete point-in-time backtesting with Portfolio tracking, equity curves, and performance metrics (Sharpe, max drawdown)
- **Momentum Strategy (Item #9)**: Implemented price-based momentum strategy with rebalancing, position sizing, and comprehensive tests
- **Error Messages & CLI Help (Item #10)**: Enhanced UX with helpful validation messages and practical examples in --help text for 7 key commands
- **Testing**: 20 new tests passing (5 cross-sectional + 8 backtest + 7 momentum)
- **Documentation Standardization**: Clarified 'python' as preferred language type for feature functions (backward compatible with 'python_expr')

**New Modules Created:**

- `src/g2/compute/cross_sectional.py` - Market-relative computations
- `src/g2/db/cross_sectional.py` - Database persistence
- `src/g2/backtest/portfolio.py` - Portfolio tracking (173 lines)
- `src/g2/backtest/engine.py` - Backtesting engine (168 lines)
- `src/g2/backtest/metrics.py` - Performance metrics (122 lines)
- `src/g2/strategies/momentum.py` - Momentum strategy (195 lines)

**User Experience Improvements:**

- Database connection errors now provide actionable guidance (suggest docker compose up, check DATABASE_URL)
- API key validation links to AlphaVantage and shows .env setup instructions
- Feature/stock validation suggests specific commands to run
- CLI help text includes practical examples for common workflows

**Strategic Direction:**

- Defined three strategic paths for Phase 2 (Trading-First, ML-First, Scale-First)
- Path A focuses on additional strategies and real-world validation
- Path B focuses on advanced ML infrastructure (Parquet, ensembles, API)
- Path C focuses on production infrastructure (monitoring, optimization, CI/CD)

### December 18, 2025

**Critical Bug Fixes & Infrastructure Improvements:**

- **Deadlock Fix**: Resolved writer thread deadlock during data-update shutdown (universe.py:240)
  - Root cause: Sentinel objects not sent when fetch phase interrupted, blocking queue.get() forever
  - Solution: Added try/finally block ensuring writers always receive shutdown signal
  - Impact: Clean Ctrl+C handling, no more hanging processes
- **Partial Data Protection**: Fixed mid-day ingestion inserting incomplete intraday data
  - Root cause: filter_new_rows() accepted any date newer than existing, including today's partial data
  - Solution: Added target_date parameter (calculated via _expected_market_date()) to filter future dates
  - Time-aware logic: Before 4pm ET = yesterday's data, After 4pm ET = today's data
  - Impact: Clean historical data, no contamination from partial trading day snapshots
- **Rate Limiter Optimization**: Reduced safety buffer from 25% to 10%
  - Before: ~60 calls/min (80% capacity utilization)
  - After: ~68 calls/min (90% capacity utilization)
  - Impact: 13% faster data ingestion

**MCP Server Enhancements:**

- **New Tools**: Added ml_train_classifier and ml_predict_classifier for Phase 1 trend classification
- **Updated Descriptions**: Enhanced data_update and features_list tool descriptions
  - Now mentions time-aware filtering (4pm ET cutoff)
  - Cross-sectional features (percentile ranks, z-scores)
  - Market-relative computations
- **Comprehensive Documentation**:
  - Updated mcp-server/README.md with new classifier tools and detailed parameters
  - Created docs/MCP_WORKFLOWS.md (600 lines) with end-to-end workflows:
    * Complete ML Pipeline (quantile regression)
    * Trend Classification System (5-class predictions)
    * Combined Signal System (multi-signal screening with SQL)
    * Model Performance Monitoring (degradation detection, A/B testing)
    * Data Quality & Exploration (coverage audits, sanity checks)
    * Production Deployment Patterns (cron jobs, automation, alerts)
    * Best Practices & Troubleshooting

**Files Modified:**

- src/g2/ingest/universe.py - Added target_date filtering, deadlock fix
- src/g2/db/ingest.py - Enhanced filter_new_rows() with date limits
- src/g2/cli.py - Updated all ingest call sites to pass target_date
- src/g2/alphavantage/client.py - Optimized rate limiter buffer
- mcp-server/server.py - Added classifier tools, updated capability descriptions
- mcp-server/README.md - Added tool documentation
- docs/MCP_WORKFLOWS.md - Created comprehensive workflow guide

**Impact:**

- **Data Quality**: Time-aware filtering ensures clean historical data, no partial intraday contamination
- **Reliability**: Deadlock fix prevents process hangs during updates
- **Performance**: 13% faster ingestion via optimized rate limiting
- **User Experience**: MCP server now exposes full Phase 1 capabilities with detailed workflows

### December 14, 2025

**ML Implementation Complete (Phase 1):**

- **Core ML modules**: Implemented `g2/ml/models.py` (456 lines) and `g2/ml/evaluation.py` (251 lines)
- **Quantile regression**: Full sklearn QuantileRegressor implementation with XGBoost/LightGBM support
- **Training workflow**: `ml train` loads CSV datasets, trains q10/q50/q90 models per horizon, saves artifacts
- **Prediction workflow**: `ml predict` fetches features from DB, generates predictions, stores in quantile_predictions
- **Evaluation workflow**: `ml eval` calculates actual returns, computes calibration metrics, generates reports
- **Dependencies**: Added scikit-learn>=1.3, joblib>=1.3 to pyproject.toml (plus optional ml_extended extras)
- **Comprehensive testing**: 27 ML tests passing (14 new tests for models + evaluation)
- **Feature validation**: Handles missing features via median imputation, enforces quantile ordering (q10 ≤ q50 ≤ q90)
- **Calibration metrics**: Pinball loss, coverage percentages, IQR statistics, interval coverage
- **Production ready**: Full end-to-end ML pipeline operational

### December 13, 2025

**Bug Fixes & Improvements:**

- **Rate limiting fix**: Added minimum 1.0s spacing to prevent burst pattern errors
- **Error detection**: AlphaVantage API errors now properly detected and reported (vs misleading "empty payload")
- **Code quality**: Fixed missing returns after emit_error, improved NaN/inf handling in labels, optimized CSV writes
- **Documentation consolidation**: Reorganized docs/ into focused architecture/performance guides + archive/

**ML Infrastructure (Phase 1 - Foundation):**

- **ML foundations**: Added `g2 ml` CLI group and DB schema (7 tables: ml_datasets, ml_runs, ml_models, quantile_predictions, trend_class_predictions, prediction_outcomes, model_performance)
- **Dataset management**: Implemented `g2 ml dataset-build` with manifest registration and CSV export (prices, features, labels)
- **TDD implementation**: Added `g2 ml train/predict/eval` commands with 21 passing tests
  - Database integration complete (run tracking, model registry, lineage)
- **Infrastructure**: GPU-capable ML runner container, check constraints for data integrity

### December 12, 2025

- **Feature management**: Added Future Work section for enable/disable commands and inactive function handling

### December 10, 2025

- **DB-first architecture complete**: Feature functions and definitions fully exportable/importable
- **18 integration tests passing**: Full export/import workflow validated

### December 9, 2025

- **Project organization**: Moved docs to docs/, scripts to scripts/, removed duplicate files
- **Feature definitions exported**: Created feature-definitions/ directory with 17 definitions

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed system design.

**Key Concepts**:

- **Database as Source of Truth**: All features stored in PostgreSQL, exported to git
- **Sandboxed Execution**: Feature functions run in restricted environment
- **Dispatcher Pattern**: Parallel feature computation with error isolation
- **TimescaleDB Chunks**: Monthly partitions for efficient time-range queries

## Future Work

See "Future Work / Technical Debt" section (line 354+) for planned enhancements:

- **Feature Management CLI**: `feat-fx-enable/disable`, `feat-def-enable/disable` commands
- **Inactive Function Handling**: Validation and warnings when functions are disabled
- **Resource Limits**: CPU/memory/time limits for sandboxed functions
- **Process Isolation**: Run untrusted code in separate processes

## Long-Term Vision

See [docs/archive/ml/HIGHLEVEL.md](docs/archive/ml/HIGHLEVEL.md) for ML-driven analysis roadmap.

**Goal**: ML-powered return distribution prediction and trend classification

**Systems**:

1. **Quantile Regression**: Predict return distributions (q10, q50, q90) for 7/30/90-day horizons
2. **Trend Classification**: Identify stocks likely to make strong directional moves

**Status**: Data pipeline ✅ complete, ML infrastructure ✅ complete, ML implementation ✅ complete (Phase 1)

**Phase 1 Complete:**

- ✅ Database schema (7 ML tables with proper constraints)
- ✅ Dataset building and export (features.csv, labels.csv, prices.csv)
- ✅ CLI commands implementation (train/predict/eval)
- ✅ Run tracking and model registry (ml_runs, ml_models tables)
- ✅ Model training (sklearn QuantileRegressor + XGBoost/LightGBM support)
- ✅ Prediction generation (DB feature fetch → inference → quantile_predictions)
- ✅ Performance evaluation (calibration metrics, pinball loss, evaluation reports)
- ✅ Comprehensive test suite (27 tests passing)

---

## Future Work / Technical Debt

### Feature Management Enhancements

**Status**: Deferred for future implementation

#### Enable/Disable CLI Commands

Currently, enabling/disabling features requires editing JSON files and re-importing. Need dedicated commands:

```bash
# Feature Functions
g2 feat-fx-enable --name indicator --version 1.0
g2 feat-fx-disable --name indicator --version 1.0

# Feature Definitions
g2 feat-def-enable --name indicator_rsi_14
g2 feat-def-disable --name indicator_rsi_14
```

**Implementation Notes**:

- Simple UPDATE queries on `feature_functions.enabled` and `feature_definitions.active`
- Add `--all` flag for bulk operations
- Consider `--status` option for feature_functions (active/deprecated/archived)

#### Inactive Function Handling

Feature definitions can reference feature functions that are disabled or missing. Need proper error handling:

**Current State**:

- No validation when feature definitions reference inactive functions
- May fail silently or with unclear errors during computation

**Required Improvements**:

1. **Validation on Import**: Check that referenced functions exist and are enabled
2. **Runtime Checks**: Skip or warn when computing features with inactive functions
3. **List Command Enhancement**: Show function status in `feat-def-list` output

   ```text
   indicator_rsi_14 (function: indicator v1.0 [DISABLED])
   ```

4. **Bulk Operations**: Commands to find and fix orphaned feature definitions

   ```bash
   g2 feat-def-validate  # Find definitions with inactive/missing functions
   g2 feat-def-fix       # Disable definitions with inactive functions
   ```

**Test Cases Needed**:

- [ ] Feature definition with disabled function (should warn/skip)
- [ ] Feature definition with missing function (should error clearly)
- [ ] Enabling function should make dependent definitions work again
- [ ] Bulk validation across all definitions

**Related Files**:

- [src/g2/cli.py](src/g2/cli.py) - Add new commands
- [src/g2/ingest/dispatcher.py](src/g2/ingest/dispatcher.py) - Add runtime validation
- [src/g2/cli_helpers.py](src/g2/cli_helpers.py) - Add validation helper functions
