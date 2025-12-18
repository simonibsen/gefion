# Next Steps

Tactical, prioritized list of implementation tasks for g2. For long-term vision, see [docs/ML_ROADMAP.md](docs/ML_ROADMAP.md).

**Last Updated**: 2024-12-17

---

## Sprint 1: Quick Wins & Documentation (2 weeks)

### 1. Fix Trim Command Behavior ⚠️ BREAKING CHANGE

**Status**: ✅ Complete (2025-12-17)
**Priority**: High (documented but not implemented)
**Effort**: 2-3 days

**Context**: We documented inverted trim behavior in USER_GUIDE.md, but the CLI code still has the old behavior.

**Action Items**:
- [x] Update `src/g2/cli.py` trim-features command:
  - Remove default `--trim-prices` behavior
  - Add `--trim-prices` flag (default False)
  - Update help text
- [x] Update `src/g2/cli.py` trim-prices command:
  - Add default trim features behavior
  - Add `--no-trim-features` flag
  - Update help text
- [x] Update `src/g2/db/ingest.py` trim functions to match new behavior
- [x] Add/update tests in `tests/` to verify:
  - `trim-features` doesn't touch prices by default
  - `trim-features --trim-prices` does trim prices
  - `trim-prices` trims features by default
  - `trim-prices --no-trim-features` keeps features
- [x] Add migration note to CHANGELOG.md
- [x] Test manually with real data to verify no regressions

**Files to modify**:
- `src/g2/cli.py` (trim-features, trim-prices commands)
- `src/g2/db/ingest.py` (trim helper functions)
- `tests/test_cli.py` or `tests/test_trim_commands.py`
- `CHANGELOG.md`

---

### 2. Add Feature Selection to Dataset Build

**Status**: ✅ Complete (2025-12-17)
**Priority**: High (most requested feature)
**Effort**: 3-4 days

**Context**: Currently all features in `computed_features` are included in datasets. Users want to select specific features.

**Action Items**:
- [x] Add `--features` parameter to `g2 ml dataset-build`:
  - Parse comma-separated feature names
  - Filter features before pivot in dataset builder
  - Validate feature names exist in database
- [x] Add `--exclude-features` parameter (blacklist mode):
  - Parse comma-separated feature names
  - Exclude from feature set before pivot
- [x] Update `src/g2/ml/dataset.py`:
  - Modify `export_dataset_artifacts()` to accept feature filter
  - Update SQL query to filter features by name
- [x] Add tests:
  - Build dataset with specific features only
  - Build dataset excluding certain features
  - Error handling for non-existent feature names
- [x] Update documentation:
  - `docs/ML_QUICKSTART.md` - add feature selection examples
  - `docs/USER_GUIDE.md` - document new flags
- [x] Update help text in CLI

**Files to modify**:
- `src/g2/cli.py` (ml dataset-build command)
- `src/g2/ml/dataset.py` (export_dataset_artifacts)
- `tests/test_ml_dataset_export.py`
- `docs/ML_QUICKSTART.md`
- `docs/USER_GUIDE.md`

---

### 3. Add White Paper to Documentation Index

**Status**: ✅ Complete (2025-12-17)
**Priority**: Low (already written, just needs linking)
**Effort**: 30 minutes

**Action Items**:
- [x] Add white paper link to `docs/README.md`
- [x] Add white paper link to main `README.md` (optional)
- [ ] Consider adding to project website or blog (future)

**Files to modify**:
- `docs/README.md`
- `README.md` (optional)

---

## Sprint 2: Performance & Polish (2 weeks)

### 4. Add Parquet Export Support

**Status**: ✅ Complete (2025-12-17)
**Priority**: Medium (better performance, professional polish)
**Effort**: 4-5 days

**Context**: CSV export works but Parquet is industry standard for ML pipelines.

**Action Items**:
- [x] Add `--format` parameter to `g2 ml dataset-build`:
  - Options: `csv` (default), `parquet`
  - Validate format choice
- [x] Install dependencies:
  - Add `pyarrow` to `pyproject.toml` (ml_extended optional dependency)
  - Test with pyarrow installed
- [x] Update `src/g2/ml/dataset.py`:
  - Add parquet writer functions
  - Use `pandas.to_parquet()` for prices, features, labels
  - Preserve data types (no string conversion)
- [x] Add tests:
  - Export dataset as parquet (TDD approach)
  - Verify file exists and can be read back
  - Verify backward compatibility (CSV default)
- [x] Update documentation:
  - `docs/ML_QUICKSTART.md` - add parquet examples
  - `CHANGELOG.md` - document new feature
  - Remove "Future" note from ML_QUICKSTART.md

**Files modified**:
- `src/g2/cli.py` (ml dataset-build command)
- `src/g2/ml/dataset.py` (add parquet export)
- `pyproject.toml` (add pyarrow dependency)
- `tests/test_ml_dataset_parquet_export.py` (new test file)
- `docs/ML_QUICKSTART.md`
- `CHANGELOG.md`

---

### 5. Move Indicators to Database (Proof of Concept)

**Status**: ✅ Complete (2025-12-17) - Proof of Concept
**Priority**: Medium (foundational for DB-first architecture)
**Effort**: 5-7 days (start with 3-5 indicators)

**Context**: Built-in indicators are in Python code. Move to database for consistency.

**Action Items**:
- [x] Choose 3-5 indicators to migrate (started with 3):
  - RSI (simple, single parameter)
  - SMA (simple, single parameter)
  - EMA (slightly more complex)
- [x] Create JSON files in `feature-functions/`:
  - `indicator_rsi.json` ✅
  - `indicator_sma.json` ✅
  - `indicator_ema.json` ✅
- [x] Extract function bodies from `src/g2/indicators/local.py`
- [x] Test sandboxed execution:
  - Verify functions work in restricted globals
  - Test with various parameter combinations
  - Compare outputs to original implementation
- [x] Add tests:
  - Import indicator functions from JSON (TDD approach)
  - Execute in sandbox
  - Verify outputs match original implementation

**Files created**:
- `feature-functions/indicator_rsi.json` ✅
- `feature-functions/indicator_sma.json` ✅
- `feature-functions/indicator_ema.json` ✅
- `tests/test_indicator_json_functions.py` ✅ (8 tests, all passing)

**Proof of Concept Complete**: Successfully demonstrated that indicators can be stored as JSON and executed dynamically. Ready for full migration of remaining indicators (MACD, Bollinger Bands, ADX, etc.) and integration with seed-features command.

**Note**: Original `src/g2/indicators/local.py` remains for backward compatibility.

---

## Sprint 3: ML Enhancements (3-4 weeks)

### 6. Implement Trend Classification Model

**Status**: ✅ Complete (2025-12-17) - Core Implementation
**Priority**: Medium (completes dual-system vision)
**Effort**: 2-3 weeks

**Context**: Trend labels are computed and stored but not used for predictions.

**Action Items**:
- [x] Create new CLI commands:
  - `g2 ml train-classifier` - train multi-class model ✅
  - `g2 ml predict-classifier` - generate trend predictions (placeholder) ✅
- [x] Implement trainer in `src/g2/ml/`:
  - New file: `classifier.py` ✅
  - Load trend labels from dataset ✅
  - Train multi-class classifier (sklearn, xgboost, lightgbm) ✅
  - Save model artifacts with metadata ✅
- [x] Implement predictor:
  - Load classifier model ✅
  - Generate class probabilities ✅
  - Store in database (placeholder for full workflow)
- [x] Add evaluation:
  - Confusion matrix ✅
  - Per-class precision/recall ✅
  - Overall accuracy ✅
- [x] Add tests:
  - Train classifier on sample data ✅
  - Generate predictions ✅
  - Evaluate model ✅
- [ ] Create combined screening examples:
  - SQL query: strong_up trend + q10 > 0 (future)
  - Document in ML_QUICKSTART.md
- [x] Update documentation:
  - CHANGELOG.md with full feature documentation ✅

**Files created**:
- `src/g2/ml/classifier.py` ✅
- `tests/test_ml_classifier.py` ✅ (6 tests, 5 passing, 1 skipped)

**Files modified**:
- `src/g2/cli.py` (added ml train-classifier, ml predict-classifier) ✅
- `CHANGELOG.md` ✅

**Core Implementation Complete**: Classifier can train, predict, and evaluate. Prediction workflow (loading features from DB and storing predictions) is a natural extension for future work.

---

### 7. Add Cross-Sectional Features

**Status**: ✅ Complete (2025-12-17) - Market-Relative MVP
**Priority**: Medium (enables peer-relative strategies)
**Effort**: 2-3 weeks

**Context**: Currently only time-series features exist. Implemented market-relative features as MVP (sector-relative requires sector data source).

**Action Items**:
- [x] Create database schema:
  - Add migration: `sql/migrations/002_cross_sectional_features.sql`
  - Create `cross_sectional_features` table (TimescaleDB hypertable)
  - Add indexes for feature and date-based queries
- [x] Implement computation:
  - New file: `src/g2/compute/cross_sectional.py`
  - `compute_return_vs_market()` - stock return vs market average
  - `compute_market_rankings()` - rank stocks by performance
  - `compute_percentiles()` - percentile rankings (0-1)
- [x] Implement database persistence:
  - New file: `src/g2/db/cross_sectional.py`
  - `insert_cross_sectional_features()` - batch insert with upsert
  - Automatic stock ID lookup from symbols
- [x] Add tests:
  - TDD approach (RED → GREEN)
  - Compute cross-sectional features (3 tests passing)
  - Database integration tests (2 tests - require DB)
- [x] Update documentation:
  - `CHANGELOG.md` - detailed feature documentation
  - Usage examples in CHANGELOG
- [ ] Add CLI command (deferred):
  - `g2 feat-compute-cross-sectional` - compute features
  - Can be used programmatically for now
- [ ] Add sector-relative features (future):
  - Requires sector data source (AlphaVantage, GICS, etc.)
  - Add `sector` column to `stocks` table
  - Implement sector aggregations

**Files created**:

- `sql/migrations/002_cross_sectional_features.sql`
- `src/g2/compute/__init__.py`
- `src/g2/compute/cross_sectional.py`
- `src/g2/db/cross_sectional.py`
- `tests/test_cross_sectional.py`

**Files modified**:

- `CHANGELOG.md` (added cross-sectional features section)

---

## Sprint 4: Trading & Backtesting (4-6 weeks)

### 8. Implement Backtesting Engine

**Status**: ✅ Complete (2025-12-17) - Core Engine MVP
**Priority**: High (validates entire pipeline, demo-able)
**Effort**: 3-4 weeks (MVP completed in 1 session)

**Context**: Need to validate predictions and demonstrate end-to-end value. Implemented core engine with point-in-time correctness.

**Action Items**:

- [x] Design backtesting architecture:
  - Point-in-time data (no look-ahead bias) ✓
  - Portfolio state tracking ✓
  - Simple strategy interface ✓
- [x] Implement core engine:
  - New directory: `src/g2/backtest/` ✓
  - `engine.py` - main backtesting loop ✓
  - `portfolio.py` - position tracking ✓
  - `metrics.py` - performance calculations ✓
- [x] Implement metrics:
  - Total return ✓
  - Sharpe ratio ✓
  - Max drawdown ✓
- [x] Add tests:
  - Simple buy-and-hold strategy ✓
  - Verify point-in-time correctness ✓
  - Portfolio tracking ✓
  - Metrics calculation ✓
  - All 8 tests passing ✓
- [x] Update documentation:
  - CHANGELOG.md with usage examples ✓
- [x] Add CLI command:
  - `g2 backtest run` - execute backtest ✓
  - Symbol/exchange filtering with limit ✓
  - Configurable strategy parameters ✓
  - Rich formatted output + JSON mode ✓
- [ ] Advanced features (deferred):
  - Transaction cost modeling
  - Slippage simulation
  - Trade log export (CSV)
  - Equity curve visualization
  - Additional metrics (Calmar, win rate, turnover)
  - CSV output format (JSON already supported)

**Files created**:

- `src/g2/backtest/__init__.py`
- `src/g2/backtest/engine.py`
- `src/g2/backtest/portfolio.py`
- `src/g2/backtest/metrics.py`
- `src/g2/backtest/data_loader.py` (CLI data loading)
- `tests/test_backtest_engine.py`
- `tests/test_backtest_cli.py` (CLI integration tests)

**Files modified**:

- `CHANGELOG.md` (added detailed documentation)
- `src/g2/cli.py` (added backtest command group)
- `NEXT_STEPS.md` (this file)

**Core Implementation Complete**: Engine can run strategies, track portfolios, and calculate performance metrics. CLI interface with real-world data integration complete. Advanced features (transaction costs, slippage, CSV export) are natural extensions for future work.

---

### 9. Implement First Trading Strategy (Momentum Following)

**Status**: ✅ Complete (2025-12-17) - Price-Based MVP
**Priority**: High (validates platform, generates results)
**Effort**: 2-3 weeks (MVP completed in 1 session)

**Context**: Need a concrete strategy to demonstrate the platform. Implemented price-based momentum as MVP (ML-based version deferred).

**Action Items**:
- [x] Implement Momentum Following strategy:
  - New file: `src/g2/strategies/momentum.py` ✓
  - Strategy logic (price-based MVP):
    - Calculate momentum over lookback period ✓
    - Select top N stocks with highest momentum ✓
    - Equal-weight position sizing ✓
    - Periodic rebalancing ✓
- [x] Add signal generation:
  - Momentum calculation function ✓
  - Stock ranking by momentum ✓
  - Buy signal generation ✓
- [x] Add tests:
  - Signal generation logic ✓
  - Position sizing calculations ✓
  - Rebalance timing ✓
  - All 7 tests passing ✓
- [x] Update documentation:
  - CHANGELOG.md with usage examples ✓
- [ ] ML-based version (deferred):
  - Query predictions from database (trend_class, confidence)
  - Filter: q50 > 3% (expected return threshold)
  - Position size: inverse IQR (risk-adjusted)
  - Exit rules: trend reversal detection
- [ ] Backtest with real data (deferred):
  - Run on historical data (2023-2024)
  - Calculate performance metrics
  - Compare to buy-and-hold benchmark
  - Document results in ML_QUICKSTART.md

**Files created**:

- `src/g2/strategies/__init__.py`
- `src/g2/strategies/momentum.py` (195 lines)
- `tests/test_strategy_momentum.py` (207 lines, 7 tests)

**Files modified**:

- `CHANGELOG.md` (added detailed documentation)
- `NEXT_STEPS.md` (this file)

**Core Implementation Complete**: Price-based momentum strategy works with backtesting engine. ML-based version and real-data backtesting are natural extensions for future work.

---

### 10. Improve Error Messages & CLI Help

**Status**: ✅ Complete (2025-12-17)
**Priority**: Low (polish)
**Effort**: 3-5 days (ongoing)

**Context**: Make CLI more user-friendly and self-documenting.

**Action Items**:
- [x] Audit all CLI commands for error messages:
  - Add references to documentation
  - Add actionable suggestions
  - Add examples in error messages
- [x] Enhance `--help` text:
  - Add examples for common use cases (7 key commands)
  - Add links to documentation
- [x] Add validation with helpful messages:
  - Validate database connection (suggest docker compose up)
  - Validate API key (suggest .env setup, link to AlphaVantage)
  - Validate features exist (suggest feat-def-list, feat-seed)
  - Validate stocks exist (suggest prices-ingest, universe-ingest)
- [ ] Add progress indicators (already mostly implemented):
  - Long-running operations show ETA
  - Clear success/failure messages
  - Suggested next steps after completion
- [ ] Examples to add:
  ```python
  if not features_found:
      raise ValueError(
          "No features found for dataset.\n"
          "Run: g2 feat-compute --exchange NASDAQ --local\n"
          "See: docs/USER_GUIDE.md#feature-definitions"
      )
  ```

**Files to modify**:
- `src/g2/cli.py` (all commands)
- Various modules with error handling

---

## Quick Reference

**Sprint 1 (Weeks 1-2)**: Items #1, #2, #3
**Sprint 2 (Weeks 3-4)**: Items #4, #5
**Sprint 3 (Weeks 5-8)**: Items #6, #7
**Sprint 4 (Weeks 9-14)**: Items #8, #9
**Ongoing**: Item #10

---

## How to Use This Document

1. **Start at the top**: Work through items in order (they build on each other)
2. **Check off tasks**: Use `- [x]` to mark completed items
3. **Update status**: Change "Planned" to "In Progress" or "Complete"
4. **Update PROGRESS.md**: When items are complete, record in PROGRESS.md
5. **Update ML_ROADMAP.md**: Mark roadmap items as complete when implemented

---

## Related Documentation

- [PROGRESS.md](PROGRESS.md) - Historical record of completed work
- [docs/ML_ROADMAP.md](docs/ML_ROADMAP.md) - Long-term ML vision and phases
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - System design
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) - How to use g2
- [CONTRIBUTING.md](CONTRIBUTING.md) - Development guidelines (if exists)

---

## Strategic Direction: Three Paths Forward

**Status**: All tactical items (#1-10) complete as of 2025-12-17

With the foundation in place, choose your strategic direction based on goals:

---

## Path A: Trading-First (Production Trading Platform)

**Goal**: Ship a complete, production-ready trading platform with multiple strategies and real-world validation.

**Timeline**: 6-8 weeks

**Best For**: Users who want to make real trading decisions, deploy strategies, analyze performance.

### 11. Real-World Validation & Integration Testing

**Status**: In Progress (CLI Complete, Real-World Testing Pending)
**Priority**: High (validates foundation)
**Effort**: 1-2 weeks

**Context**: Test the backtesting engine and momentum strategy with real historical data to validate correctness and identify edge cases.

**Action Items**:
- [ ] Run momentum backtest on 50+ NASDAQ stocks (6+ months history)
  - Test with real market gaps (weekends, holidays)
  - Test with stock halts and circuit breakers
  - Validate point-in-time correctness (no look-ahead bias)
- [ ] Create end-to-end integration test:
  - `g2 db-init` → `g2 data-update` → backtest
  - Document any rough edges or failure modes
  - Measure performance (time, memory) with real data volumes
- [x] Document real performance metrics:
  - Total return vs. buy-and-hold benchmark ✓
  - Sharpe ratio, max drawdown explanation ✓
  - Real-world example with interpretation (-12.99% return, 0.403 Sharpe, -64.18% drawdown) ✓
  - Created comprehensive docs/BACKTESTING.md guide (394 lines) ✓
  - Transaction costs impact (noted as future work)
- [x] Add CLI command for backtesting:
  - Implemented `g2 backtest run` with full parameter support ✓
  - Symbol/exchange filtering with optional limit ✓
  - Configurable strategy parameters (lookback, top_n, rebalance_days) ✓
  - Rich formatted output + JSON mode ✓
  - Data loader module for efficient price data loading ✓
  - 6 TDD tests (all passing) ✓

**Files created**:

- `src/g2/backtest/data_loader.py` (151 lines)
- `tests/test_backtest_cli.py` (381 lines, 6 tests)
- `docs/BACKTESTING.md` (394 lines) - Complete backtesting guide with examples and troubleshooting
- `examples/momentum_backtest.py` (301 lines) - Example script for programmatic backtest usage

**Files modified**:

- `src/g2/cli.py` (added backtest command group and run command)

---

### 12. Core Trading Strategies Suite

**Status**: In Progress (1/6 complete)
**Priority**: High (expand capabilities)
**Effort**: 3-4 weeks

**Context**: Implement 4-6 additional trading strategies to provide variety and enable strategy comparison.

**Action Items**:
- [x] **Mean Reversion Strategy**:
  - Buy oversold (RSI < 30), sell overbought (RSI > 70) ✓
  - RSI-based signal generation ✓
  - Tests in `tests/test_strategy_mean_reversion.py` (9 tests passing) ✓
  - CLI integration via `g2 backtest run --strategy mean_reversion` ✓
- [ ] **Pairs Trading Strategy**:
  - Cointegration-based pairs selection
  - Z-score entry/exit signals
  - Tests for spread calculation and signal generation
- [ ] **Breakout Strategy**:
  - Volatility expansion detection
  - Volume confirmation
  - Trailing stop loss implementation
- [ ] **Moving Average Crossover**:
  - Golden cross (50/200 day) signals
  - EMA-based faster signals
  - Trend confirmation with volume
- [ ] **RSI Divergence Strategy**:
  - Price/RSI divergence detection
  - Combined with support/resistance levels
- [ ] **Volatility Contraction Strategy**:
  - Bollinger Band squeeze
  - Low ATR periods → expansion trades

**Implementation Pattern** (TDD for each):
1. Write failing tests for strategy logic
2. Implement strategy class with `generate_signals()` method
3. Test with synthetic data (predictable outcomes)
4. Test with real historical data
5. Document parameters and expected behavior

**Files created**:

- `src/g2/strategies/mean_reversion.py` (195 lines) ✓
- `tests/test_strategy_mean_reversion.py` (369 lines, 9 tests) ✓

**Files to create**:

- `src/g2/strategies/pairs_trading.py`
- `src/g2/strategies/breakout.py`
- `src/g2/strategies/ma_crossover.py`
- `src/g2/strategies/rsi_divergence.py`
- `src/g2/strategies/volatility_contraction.py`
- Corresponding test files in `tests/`

---

### 13. Strategy Comparison Framework

**Status**: Planned
**Priority**: Medium (enables evaluation)
**Effort**: 1-2 weeks

**Context**: Enable side-by-side comparison of strategy performance to identify best performers for given conditions.

**Action Items**:
- [ ] Implement comparison metrics:
  - Risk-adjusted returns (Sharpe, Sortino, Calmar ratios)
  - Drawdown analysis (max, average, recovery time)
  - Trade statistics (win rate, profit factor, avg win/loss)
  - Consistency metrics (monthly returns, rolling Sharpe)
- [ ] Create comparison CLI command:
  ```bash
  g2 backtest compare \
    --strategies momentum,mean_reversion,breakout \
    --symbols AAPL,MSFT,GOOGL,NVDA,TSLA \
    --start-date 2024-01-01 \
    --end-date 2024-12-31
  ```
- [ ] Generate comparison report:
  - Table of key metrics by strategy
  - Equity curve plots (matplotlib/plotly)
  - Monthly return heatmaps
  - Export to JSON/CSV for further analysis
- [ ] Add statistical significance testing:
  - Bootstrap confidence intervals
  - Paired t-tests for return differences
- [ ] Document best practices for strategy selection:
  - Market regime considerations
  - Portfolio allocation across strategies
  - Parameter sensitivity analysis

**Files to create/modify**:
- `src/g2/backtest/comparison.py`
- `src/g2/backtest/metrics.py` (expand existing)
- `src/g2/backtest/reporting.py`
- `src/g2/cli.py` (add compare command)
- `tests/test_comparison.py`
- `docs/STRATEGY_COMPARISON.md`

---

### 14. Advanced Backtesting Features

**Status**: Planned
**Priority**: Medium (realism)
**Effort**: 2-3 weeks

**Context**: Add features for more realistic backtesting (costs, slippage, position sizing).

**Action Items**:
- [ ] Transaction cost modeling:
  - Configurable commission per trade
  - Bid-ask spread simulation
  - Market impact for larger orders
- [ ] Slippage modeling:
  - Slippage as function of volume
  - Limit vs. market order simulation
- [ ] Position sizing strategies:
  - Fixed dollar amount
  - Kelly criterion
  - Risk parity
  - Volatility-based sizing
- [ ] Risk management:
  - Stop loss orders
  - Take profit targets
  - Maximum position size limits
  - Portfolio-level risk constraints
- [ ] Walk-forward optimization:
  - Rolling train/test windows
  - Parameter stability analysis
  - Out-of-sample performance validation

**Files to create/modify**:
- `src/g2/backtest/costs.py`
- `src/g2/backtest/position_sizing.py`
- `src/g2/backtest/risk_management.py`
- `src/g2/backtest/optimization.py`
- Update `BacktestEngine` to use these components

---

## Path B: ML-First (Advanced ML Infrastructure)

**Goal**: Build state-of-the-art ML infrastructure for research and production model development.

**Timeline**: 6-8 weeks

**Best For**: Researchers, data scientists, users focused on model quality and experimentation.

### 15. Parquet Dataset Format

**Status**: Planned
**Priority**: High (performance)
**Effort**: 1 week

**Context**: Parquet provides 5-10x faster I/O and smaller files compared to CSV.

**Action Items**:
- [ ] Add Parquet export to dataset builder:
  ```bash
  g2 ml dataset-build --name tech --version v1 \
    --symbols AAPL,MSFT --export --format parquet
  ```
- [ ] Update model training to read Parquet:
  - Replace `pd.read_csv()` with `pd.read_parquet()`
  - Handle Parquet metadata for type preservation
- [ ] Benchmark performance improvements:
  - File size comparison (CSV vs. Parquet)
  - Load time comparison
  - Memory usage comparison
- [ ] Add PyArrow as dependency:
  - Update `pyproject.toml`
  - Document installation requirements

**Files to modify**:
- `src/g2/ml/dataset.py`
- `src/g2/ml/models.py`
- `src/g2/cli.py`
- `pyproject.toml`
- `tests/test_ml_dataset_export.py`

---

### 16. Combined Trend + Quantile Screening

**Status**: Planned
**Priority**: High (ML integration)
**Effort**: 2-3 weeks

**Context**: Combine trend classification and quantile predictions for intelligent stock screening.

**Action Items**:
- [ ] Implement screening logic:
  - Filter by predicted trend (e.g., only "strong_up" or "weak_up")
  - Within trend filter, rank by quantile predictions (q90 - q10 spread)
  - Select top N stocks for portfolio
- [ ] Create screening CLI:
  ```bash
  g2 ml screen \
    --trend-model trend_v1 \
    --quantile-model qr_v1 \
    --date 2024-12-17 \
    --trend-filter strong_up,weak_up \
    --top-n 20
  ```
- [ ] Add confidence filtering:
  - Require minimum trend probability (e.g., > 0.7)
  - Require minimum quantile spread (q90 - q50 > threshold)
- [ ] Backtest screening strategy:
  - Compare vs. momentum strategy
  - Analyze turnover and transaction costs
- [ ] Document screening approach in ML_QUICKSTART.md

**Files to create/modify**:
- `src/g2/ml/screening.py`
- `src/g2/cli.py` (add screen command)
- `tests/test_ml_screening.py`
- `docs/ML_QUICKSTART.md`

---

### 17. Feature Engineering Pipeline

**Status**: Planned
**Priority**: Medium (data quality)
**Effort**: 2-3 weeks

**Context**: Structured feature engineering with transformations, selections, and validation.

**Action Items**:
- [ ] Feature transformations:
  - Log transforms for skewed features
  - Winsorization for outliers
  - Standardization (z-score) and normalization
  - Interaction features (products, ratios)
- [ ] Feature selection:
  - Correlation-based filtering
  - Variance threshold
  - Recursive feature elimination (RFE)
  - SHAP-based importance
- [ ] Feature validation:
  - Missing value checks
  - Distribution drift detection
  - Multicollinearity detection (VIF)
  - Target leakage detection
- [ ] Pipeline configuration:
  - YAML/JSON config for feature pipeline
  - Reproducible transformations
  - Version tracking

**Files to create**:
- `src/g2/ml/feature_engineering.py`
- `src/g2/ml/feature_selection.py`
- `src/g2/ml/feature_validation.py`
- `config/feature_pipeline_example.yaml`

---

### 18. Production ML Features

**Status**: Planned
**Priority**: Medium (production readiness)
**Effort**: 3-4 weeks

**Context**: Features needed for production ML deployment.

**Action Items**:
- [ ] **Warm-Start Retraining**:
  - Incremental model updates with new data
  - Transfer learning from previous model
  - A/B testing framework for model comparison
- [ ] **Model Ensembles**:
  - Combine multiple models (voting, stacking)
  - Diversity-based ensemble selection
  - Ensemble weight optimization
- [ ] **Feature Importance Analysis**:
  - SHAP values for global/local explanations
  - Permutation importance
  - Partial dependence plots
- [ ] **Hyperparameter Tuning**:
  - Optuna integration for Bayesian optimization
  - Cross-validation strategies
  - Early stopping and pruning
- [ ] **Online Prediction API**:
  - FastAPI endpoint for predictions
  - Model serving with caching
  - Batch prediction support
  - Monitoring and logging

**Files to create**:
- `src/g2/ml/retraining.py`
- `src/g2/ml/ensemble.py`
- `src/g2/ml/explainability.py`
- `src/g2/ml/tuning.py`
- `src/g2/api/` (new module for API)
- `src/g2/api/server.py`
- `src/g2/api/models.py`

---

## Path C: Scale-First (Production Infrastructure)

**Goal**: Build bulletproof, scalable infrastructure for growing user base and production deployment.

**Timeline**: 6-8 weeks

**Best For**: Teams deploying to production, handling large data volumes, supporting multiple users.

### 19. Observability & Monitoring

**Status**: Planned
**Priority**: High (production requirement)
**Effort**: 2-3 weeks

**Context**: Production systems need comprehensive monitoring, logging, and alerting.

**Action Items**:
- [ ] **Structured Logging**:
  - Migrate from print/echo to structured logging (JSON)
  - Use Python `logging` module with formatters
  - Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
  - Contextual logging (operation ID, user, timestamp)
- [ ] **Metrics Collection**:
  - Track operation duration (data ingestion, feature computation, training)
  - Track success/failure rates
  - Track resource usage (memory, CPU, DB connections)
  - Export to Prometheus/StatsD
- [ ] **Error Tracking**:
  - Integrate Sentry or similar for error aggregation
  - Capture stack traces and context
  - Alert on error spikes
- [ ] **Performance Profiling**:
  - Add profiling decorators for slow operations
  - Identify bottlenecks in data pipeline
  - Memory profiling for large datasets
- [ ] **Health Checks**:
  - Database connectivity check
  - API availability check
  - Data freshness check (last update time)

**Files to create/modify**:
- `src/g2/utils/logging.py`
- `src/g2/utils/metrics.py`
- `src/g2/utils/profiling.py`
- `config/logging.yaml`
- Update all CLI commands to use structured logging

---

### 20. Performance Optimization

**Status**: Planned
**Priority**: High (scalability)
**Effort**: 2-3 weeks

**Context**: Optimize for larger datasets and faster execution.

**Action Items**:
- [ ] **Database Query Optimization**:
  - Analyze slow queries with EXPLAIN ANALYZE
  - Add missing indexes (date, symbol combinations)
  - Optimize JOIN operations
  - Use window functions for time-series operations
- [ ] **Parallel Execution**:
  - Increase parallelism in feature computation
  - Use connection pooling more effectively
  - Optimize batch sizes for throughput
- [ ] **Caching Layer**:
  - Cache frequently accessed data (stock metadata)
  - Cache computed features (Redis/Memcached)
  - Invalidation strategy for stale data
- [ ] **Data Pipeline Optimization**:
  - Use Pandas/Polars optimizations
  - Vectorize operations instead of loops
  - Chunked processing for large datasets
- [ ] **Benchmark Suite**:
  - Create reproducible benchmarks
  - Track performance over time
  - Regression detection

**Files to create/modify**:
- `src/g2/utils/caching.py`
- `benchmarks/` (new directory)
- `benchmarks/bench_feature_computation.py`
- `benchmarks/bench_data_ingestion.py`
- SQL migration for additional indexes

---

### 21. CI/CD & Deployment

**Status**: Planned
**Priority**: Medium (automation)
**Effort**: 1-2 weeks

**Context**: Automate testing, building, and deployment.

**Action Items**:
- [ ] **GitHub Actions Workflows**:
  - Run tests on every PR (pytest, linting)
  - Run tests with multiple Python versions (3.9, 3.10, 3.11)
  - Database integration tests with Docker
- [ ] **Docker Deployment**:
  - Dockerfile for g2 application
  - Docker Compose for full stack (app + DB)
  - Multi-stage builds for smaller images
- [ ] **Release Automation**:
  - Semantic versioning (SemVer)
  - Automated changelog generation
  - PyPI package publishing
  - Docker image publishing to registry
- [ ] **Documentation Site**:
  - MkDocs or Sphinx for documentation
  - Auto-deploy docs on push to main
  - API reference generation from docstrings

**Files to create**:
- `.github/workflows/test.yml`
- `.github/workflows/release.yml`
- `Dockerfile`
- `docker-compose.yml` (production version)
- `mkdocs.yml` or `docs/conf.py`

---

### 22. Documentation & Tutorials

**Status**: Planned
**Priority**: Medium (user experience)
**Effort**: 2-3 weeks

**Context**: Comprehensive documentation and tutorials for new users.

**Action Items**:
- [ ] **Getting Started Guide**:
  - 15-minute quickstart from zero to first backtest
  - Step-by-step with screenshots
  - Common pitfalls and troubleshooting
- [ ] **Video Tutorials**:
  - YouTube series covering main workflows
  - Screen recordings with narration
  - Jupyter notebook walkthroughs
- [ ] **API Documentation**:
  - Full docstring coverage (Google/NumPy style)
  - Auto-generated API reference
  - Type hints for all public APIs
- [ ] **Example Gallery**:
  - 10+ complete examples covering use cases
  - Jupyter notebooks with explanations
  - Runnable in Binder/Colab
- [ ] **FAQ & Cookbook**:
  - Common questions and answers
  - Recipe-style solutions for specific tasks
  - Performance tips and best practices

**Files to create/modify**:
- `docs/QUICKSTART.md` (expand existing)
- `docs/TUTORIALS.md`
- `docs/API_REFERENCE.md`
- `docs/FAQ.md`
- `docs/COOKBOOK.md`
- `examples/notebooks/` (Jupyter notebooks)

---

### 23. Testing & Quality Assurance

**Status**: Planned
**Priority**: High (code quality)
**Effort**: 2-3 weeks

**Context**: Increase test coverage and add quality gates.

**Action Items**:
- [ ] **Increase Test Coverage**:
  - Target 80%+ coverage for core modules
  - Add integration tests for full workflows
  - Add property-based tests (Hypothesis)
  - Performance regression tests
- [ ] **Linting & Formatting**:
  - Enforce Black formatting
  - Enable Ruff for fast linting
  - Type checking with mypy
  - Pre-commit hooks for consistency
- [ ] **Database Test Fixtures**:
  - Reusable fixtures for common scenarios
  - Fast in-memory test database option
  - Data generators for realistic test data
- [ ] **Load Testing**:
  - Simulate high query loads
  - Test with 1000+ stocks, 5+ years data
  - Identify scalability limits
- [ ] **Security Audit**:
  - SQL injection prevention review
  - Dependency vulnerability scanning
  - Secrets management review

**Files to create/modify**:
- `.pre-commit-config.yaml`
- `pyproject.toml` (add linting config)
- `tests/fixtures/` (shared fixtures)
- `tests/performance/` (performance tests)
- Update CI to enforce quality gates

---

## Implementation Recommendations

### For Path A (Trading-First):
1. Start with Item #11 (validation) - 1 week
2. Implement 2-3 strategies from #12 - 2 weeks
3. Add strategy comparison #13 - 1 week
4. **Milestone**: Working multi-strategy platform

### For Path B (ML-First):
1. Add Parquet support #15 - 1 week
2. Build screening system #16 - 2 weeks
3. Add feature engineering #17 - 2 weeks
4. Add 2-3 production features from #18 - 2 weeks
5. **Milestone**: Production-grade ML pipeline

### For Path C (Scale-First):
1. Add observability #19 - 2 weeks
2. Optimize performance #20 - 2 weeks
3. Set up CI/CD #21 - 1 week
4. Write documentation #22 - 2 weeks
5. **Milestone**: Production-ready infrastructure

### Recommended Sequence:
If uncertain, follow this order:
1. **Path A** (validate foundation, add value fast)
2. **Path C** (make it robust before scaling)
3. **Path B** (add advanced ML features)

This ensures you have a working, validated, stable platform before adding complexity.

---

## Progress Tracking

Update this section as items are completed:

**Completed** (2025-12-17):
- ✅ Items #1-10 (all tactical improvements)
- ✅ Cross-sectional features
- ✅ Backtesting engine MVP
- ✅ Momentum strategy MVP
- ✅ Trend classification model
- ✅ Error messages & CLI help

**In Progress**: None

**Next Up**: Choose strategic path (A, B, or C)

