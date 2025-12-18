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
- [ ] Add CLI command (deferred):
  - `g2 backtest run` - execute backtest
  - Can be used programmatically for now
- [ ] Advanced features (deferred):
  - Transaction cost modeling
  - Slippage simulation
  - Trade log export (CSV)
  - Equity curve visualization
  - Additional metrics (Calmar, win rate, turnover)
  - Output formats (CSV, JSON)

**Files created**:

- `src/g2/backtest/__init__.py`
- `src/g2/backtest/engine.py`
- `src/g2/backtest/portfolio.py`
- `src/g2/backtest/metrics.py`
- `tests/test_backtest_engine.py`

**Files modified**:

- `CHANGELOG.md` (added detailed documentation)
- `NEXT_STEPS.md` (this file)

**Core Implementation Complete**: Engine can run strategies, track portfolios, and calculate performance metrics. CLI and advanced features are natural extensions for future work.

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
