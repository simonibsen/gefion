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

**Status**: Planned
**Priority**: High (most requested feature)
**Effort**: 3-4 days

**Context**: Currently all features in `computed_features` are included in datasets. Users want to select specific features.

**Action Items**:
- [ ] Add `--features` parameter to `g2 ml dataset-build`:
  - Parse comma-separated feature names
  - Filter features before pivot in dataset builder
  - Validate feature names exist in database
- [ ] Add `--exclude-features` parameter (blacklist mode):
  - Parse comma-separated feature names
  - Exclude from feature set before pivot
- [ ] Update `src/g2/ml/dataset.py`:
  - Modify `export_dataset_artifacts()` to accept feature filter
  - Update SQL query to filter features by name
- [ ] Add tests:
  - Build dataset with specific features only
  - Build dataset excluding certain features
  - Error handling for non-existent feature names
- [ ] Update documentation:
  - `docs/ML_QUICKSTART.md` - add feature selection examples
  - `docs/USER_GUIDE.md` - document new flags
- [ ] Update help text in CLI

**Files to modify**:
- `src/g2/cli.py` (ml dataset-build command)
- `src/g2/ml/dataset.py` (export_dataset_artifacts)
- `tests/test_ml_dataset_export.py`
- `docs/ML_QUICKSTART.md`
- `docs/USER_GUIDE.md`

---

### 3. Add White Paper to Documentation Index

**Status**: Planned
**Priority**: Low (already written, just needs linking)
**Effort**: 30 minutes

**Action Items**:
- [ ] Add white paper link to `docs/README.md`
- [ ] Add white paper link to main `README.md` (optional)
- [ ] Consider adding to project website or blog (future)

**Files to modify**:
- `docs/README.md`
- `README.md` (optional)

---

## Sprint 2: Performance & Polish (2 weeks)

### 4. Add Parquet Export Support

**Status**: Planned
**Priority**: Medium (better performance, professional polish)
**Effort**: 4-5 days

**Context**: CSV export works but Parquet is industry standard for ML pipelines.

**Action Items**:
- [ ] Add `--format` parameter to `g2 ml dataset-build`:
  - Options: `csv` (default), `parquet`
  - Validate format choice
- [ ] Install dependencies:
  - Add `pyarrow` to `setup.py` (optional dependency?)
  - Test with/without pyarrow installed
- [ ] Update `src/g2/ml/dataset.py`:
  - Add parquet writer functions
  - Use `pandas.to_parquet()` for prices, features, labels
  - Preserve data types (no string conversion)
- [ ] Add tests:
  - Export dataset as parquet
  - Verify file exists and can be read back
  - Compare parquet vs CSV file sizes
  - Test error handling when pyarrow not installed
- [ ] Benchmark performance:
  - Export time: CSV vs Parquet
  - File size: CSV vs Parquet
  - Load time: CSV vs Parquet
  - Document results in PERFORMANCE.md
- [ ] Update documentation:
  - `docs/ML_QUICKSTART.md` - add parquet examples
  - `docs/USER_GUIDE.md` - document --format flag
  - Remove "Future" note from ML_QUICKSTART.md

**Files to modify**:
- `src/g2/cli.py` (ml dataset-build command)
- `src/g2/ml/dataset.py` (add parquet export)
- `setup.py` (add pyarrow dependency)
- `tests/test_ml_dataset_export.py`
- `docs/ML_QUICKSTART.md`
- `docs/USER_GUIDE.md`

---

### 5. Move Indicators to Database (Proof of Concept)

**Status**: Planned
**Priority**: Medium (foundational for DB-first architecture)
**Effort**: 5-7 days (start with 3-5 indicators)

**Context**: Built-in indicators are in Python code. Move to database for consistency.

**Action Items**:
- [ ] Choose 3-5 indicators to migrate (start simple):
  - RSI (simple, single parameter)
  - SMA (simple, single parameter)
  - EMA (slightly more complex)
  - MACD (multiple parameters)
  - Bollinger Bands (multiple outputs)
- [ ] Create JSON files in `feature-functions/`:
  - `indicator_rsi.json`
  - `indicator_sma.json`
  - `indicator_ema.json`
  - `indicator_macd.json`
  - `indicator_bollinger.json`
- [ ] Extract function bodies from `src/g2/compute/indicators.py`
- [ ] Test sandboxed execution:
  - Verify functions work in restricted globals
  - Test with various parameter combinations
  - Compare outputs to original implementation
- [ ] Update `g2 seed-features`:
  - Import functions from JSON files
  - Register feature definitions
  - Maintain backward compatibility (keep old code path for now)
- [ ] Add tests:
  - Import indicator functions from JSON
  - Execute in sandbox
  - Verify outputs match original implementation
- [ ] Document migration path:
  - Add guide to `docs/ARCHITECTURE.md`
  - Document how users can migrate custom indicators

**Files to create**:
- `feature-functions/indicator_rsi.json`
- `feature-functions/indicator_sma.json`
- `feature-functions/indicator_ema.json`
- `feature-functions/indicator_macd.json`
- `feature-functions/indicator_bollinger.json`

**Files to modify**:
- `src/g2/cli.py` (seed-features command)
- `tests/test_indicators.py` (add JSON-based tests)
- `docs/ARCHITECTURE.md` (document migration)

**Note**: Keep original `src/g2/compute/indicators.py` for backward compatibility during transition.

---

## Sprint 3: ML Enhancements (3-4 weeks)

### 6. Implement Trend Classification Model

**Status**: Planned
**Priority**: Medium (completes dual-system vision)
**Effort**: 2-3 weeks

**Context**: Trend labels are computed and stored but not used for predictions.

**Action Items**:
- [ ] Create new CLI commands:
  - `g2 ml train-classifier` - train multi-class model
  - `g2 ml predict-classifier` - generate trend predictions
- [ ] Implement trainer in `src/g2/ml/`:
  - New file: `classifier.py`
  - Load trend labels from dataset
  - Train XGBoost multi-class classifier
  - Save model artifacts with metadata
- [ ] Implement predictor:
  - Load classifier model
  - Generate class probabilities
  - Store in `trend_class_predictions` table
- [ ] Add evaluation:
  - Confusion matrix
  - Per-class precision/recall
  - Overall accuracy
- [ ] Add tests:
  - Train classifier on sample data
  - Generate predictions
  - Evaluate model
- [ ] Create combined screening examples:
  - SQL query: strong_up trend + q10 > 0
  - Document in ML_QUICKSTART.md
- [ ] Update documentation:
  - Remove "Future" note from ML_QUICKSTART.md
  - Add full usage guide
  - Add strategy examples

**Files to create**:
- `src/g2/ml/classifier.py`
- `tests/test_ml_classifier.py`

**Files to modify**:
- `src/g2/cli.py` (add ml train-classifier, ml predict-classifier)
- `docs/ML_QUICKSTART.md` (add classifier section)
- `docs/ML_ROADMAP.md` (mark as complete)

---

### 7. Add Cross-Sectional Features

**Status**: Planned
**Priority**: Medium (enables sector strategies)
**Effort**: 2-3 weeks

**Context**: Currently only time-series features exist. Need sector-relative features.

**Action Items**:
- [ ] Create database schema:
  - Add migration: `sql/migrations/add_cross_sectional_features.sql`
  - Create `cross_sectional_features` table (as documented in ARCHITECTURE.md)
- [ ] Implement computation:
  - New file: `src/g2/compute/cross_sectional.py`
  - Compute return vs sector average
  - Compute volume vs sector average
  - Compute sector rankings
  - Compute percentiles
- [ ] Add CLI command:
  - `g2 feat-compute-cross-sectional` - compute sector features
  - Add `--features` parameter (which cross-sectional features)
  - Add `--exchange` parameter (universe)
- [ ] Add sector data:
  - Where to get sector mapping? (AlphaVantage, manual, GICS codes?)
  - Add `sector` column to `stocks` table
  - Populate sector data
- [ ] Add tests:
  - Compute cross-sectional features
  - Verify sector aggregations
  - Verify rankings and percentiles
- [ ] Add example queries:
  - Find sector leaders
  - Sector rotation signals
  - Document in ML_QUICKSTART.md

**Files to create**:
- `sql/migrations/add_cross_sectional_features.sql`
- `src/g2/compute/cross_sectional.py`
- `tests/test_cross_sectional.py`

**Files to modify**:
- `src/g2/cli.py` (add feat-compute-cross-sectional)
- `docs/ARCHITECTURE.md` (move from "Future" to "Implemented")
- `docs/ML_QUICKSTART.md` (add examples)

---

## Sprint 4: Trading & Backtesting (4-6 weeks)

### 8. Implement Backtesting Engine

**Status**: Planned
**Priority**: High (validates entire pipeline, demo-able)
**Effort**: 3-4 weeks

**Context**: Need to validate predictions and demonstrate end-to-end value.

**Action Items**:
- [ ] Design backtesting architecture:
  - Point-in-time data (no look-ahead bias)
  - Portfolio state tracking
  - Transaction cost modeling
  - Rebalancing logic
- [ ] Implement core engine:
  - New directory: `src/g2/backtest/`
  - `engine.py` - main backtesting loop
  - `portfolio.py` - position tracking
  - `metrics.py` - performance calculations
- [ ] Add CLI command:
  - `g2 backtest run` - execute backtest
  - Parameters: strategy, start/end dates, capital, constraints
- [ ] Implement metrics:
  - Total return
  - Sharpe ratio
  - Max drawdown
  - Calmar ratio
  - Win rate
  - Average gain/loss
  - Turnover
- [ ] Add tests:
  - Simple buy-and-hold strategy
  - Verify point-in-time correctness
  - Verify transaction costs
- [ ] Output formats:
  - Trade log CSV
  - Equity curve CSV
  - Summary statistics JSON
  - Performance report (text)
- [ ] Documentation:
  - Add to USER_GUIDE.md
  - Add examples to ML_QUICKSTART.md

**Files to create**:
- `src/g2/backtest/engine.py`
- `src/g2/backtest/portfolio.py`
- `src/g2/backtest/metrics.py`
- `tests/test_backtest_engine.py`

**Files to modify**:
- `src/g2/cli.py` (add backtest run)
- `docs/USER_GUIDE.md`
- `docs/ML_QUICKSTART.md`

---

### 9. Implement First Trading Strategy (Momentum Following)

**Status**: Planned
**Priority**: High (validates ML predictions, generates results)
**Effort**: 2-3 weeks (after backtesting engine)

**Context**: Need a concrete strategy to demonstrate the platform.

**Action Items**:
- [ ] Implement Momentum Following strategy:
  - New file: `src/g2/strategies/momentum.py`
  - Strategy logic:
    - Screen: trend_class = 'strong_up' + confidence > 0.7
    - Filter: q50 > 3% (expected return threshold)
    - Position size: inverse IQR (lower uncertainty = larger position)
    - Exit: trend reversal or horizon reached
- [ ] Add signal generation:
  - Query predictions from database
  - Apply strategy rules
  - Generate buy/sell signals
- [ ] Backtest the strategy:
  - Run on historical data (2023-2024)
  - Calculate performance metrics
  - Compare to buy-and-hold benchmark
- [ ] Add tests:
  - Signal generation logic
  - Position sizing calculations
  - Strategy integration with backtest engine
- [ ] Document results:
  - Write up strategy design
  - Present backtest results
  - Add to ML_QUICKSTART.md as case study

**Files to create**:
- `src/g2/strategies/momentum.py`
- `tests/test_strategy_momentum.py`

**Files to modify**:
- `docs/ML_QUICKSTART.md` (add strategy case study)
- `docs/ML_ROADMAP.md` (mark momentum strategy as complete)

---

### 10. Improve Error Messages & CLI Help

**Status**: Planned
**Priority**: Low (polish)
**Effort**: 3-5 days (ongoing)

**Context**: Make CLI more user-friendly and self-documenting.

**Action Items**:
- [ ] Audit all CLI commands for error messages:
  - Add references to documentation
  - Add actionable suggestions
  - Add examples in error messages
- [ ] Enhance `--help` text:
  - Add examples for common use cases
  - Add links to documentation
  - Add warnings about common pitfalls
- [ ] Add validation with helpful messages:
  - Validate database connection (suggest docker compose up)
  - Validate API key (suggest .env setup)
  - Validate required tables exist (suggest schema.sql)
- [ ] Add progress indicators:
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
