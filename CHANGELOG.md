# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

#### Trend Classification Model

Implemented multi-class classifier for predicting trend labels (5-class):

**CLI Commands:**

- `g2 ml train-classifier` - Train classifier on dataset with trend labels
- `g2 ml predict-classifier` - Generate trend predictions (placeholder)

**Classes:**

- `strong_up` - Return >= strong_threshold
- `weak_up` - weak_threshold <= return < strong_threshold
- `neutral` - |return| < weak_threshold
- `weak_down` - -strong_threshold < return <= -weak_threshold
- `strong_down` - Return <= -strong_threshold

**Algorithms:**

- `sklearn` - RandomForestClassifier (default)
- `xgboost` - XGBClassifier (requires ml_extended)
- `lightgbm` - LGBMClassifier (requires ml_extended)

**Features:**

- Load datasets with trend labels from CSV/Parquet
- Train multi-class classifier with configurable algorithms
- Automatic label encoding for 5 trend classes
- Missing value handling with median imputation
- Evaluation metrics: accuracy, confusion matrix, per-class precision/recall/F1
- Model artifacts saved with metadata
- Database integration (ml_models, ml_runs tables)

**Usage:**

```bash
# Train classifier
g2 ml train-classifier \
  --dataset-name tech --dataset-version v1 \
  --model-name trend-clf --model-version v1 \
  --algorithm sklearn --horizon 7

# View metadata
cat models/trend-clf_v1_h7_classifier/metadata.json
```

**Files Created:**

- `src/g2/ml/classifier.py` - Classifier training, prediction, evaluation
- `tests/test_ml_classifier.py` - 6 tests (5 passing, 1 skipped)

**Tests**: All 5 core tests passing using TDD approach (RED → GREEN)

#### JSON-Based Indicator Functions (Proof of Concept)

Migrated 3 indicators from Python code to JSON-based database-stored functions:

**Indicators Migrated:**

- RSI (Relative Strength Index)
- SMA (Simple Moving Average)
- EMA (Exponential Moving Average)

**Benefits:**

- **Database-First Architecture**: Indicators stored as data, not code
- **Dynamic Execution**: Load and execute functions from JSON files
- **Extensibility**: Users can add custom indicators without modifying source code
- **Version Control**: Indicator definitions tracked in git alongside code
- **Sandboxed Execution**: Functions run in controlled environment

**Files Created:**

```
feature-functions/indicator_rsi.json
feature-functions/indicator_sma.json
feature-functions/indicator_ema.json
tests/test_indicator_json_functions.py (8 tests, all passing)
```

**Proof of Concept**: Demonstrates feasibility of migrating all indicators to JSON format. Existing implementation in `src/g2/indicators/local.py` remains for backward compatibility.

**Next Steps**: Migrate remaining indicators (MACD, Bollinger Bands, ADX, Stochastic, PSAR) and integrate with `g2 seed-features` command.

#### Parquet Export Support

Added Parquet format support for ML dataset exports:

**New CLI Parameter:**

- `--format` - Export format: `csv` (default) or `parquet`

**Usage Examples:**

```bash
# Export as Parquet for better performance and smaller file sizes
g2 ml dataset-build --name tech --version v1 \
  --symbols AAPL,MSFT,GOOGL --horizons 7,30 \
  --format parquet \
  --export

# CSV is still the default (backward compatible)
g2 ml dataset-build --name tech --version v1 \
  --symbols AAPL,MSFT,GOOGL --horizons 7,30 \
  --export
```

**Benefits:**

- **Performance**: 5-10x faster read/write compared to CSV
- **File Size**: 5-10x smaller files (columnar compression)
- **Type Preservation**: Maintains int64, float64 types (CSV converts to strings)
- **Industry Standard**: Compatible with pandas, polars, spark, and ML frameworks

**Notes:**

- Requires `pyarrow>=14.0` (install with: `pip install g2[ml_extended]`)
- Format is stored in dataset manifest for reproducibility
- Default is CSV for backward compatibility

#### Feature Selection for Dataset Build

Added ability to select specific features when building ML datasets:

**New CLI Parameters:**

- `--features` - Whitelist mode: include only specified features
- `--exclude-features` - Blacklist mode: exclude specified features

**Usage Examples:**

```bash
# Include only specific features
g2 ml dataset-build --name selective --version v1 \
  --symbols AAPL,MSFT --horizons 7,30 \
  --features indicator_rsi_14,indicator_macd,indicator_bollinger_bands \
  --export

# Exclude specific features
g2 ml dataset-build --name filtered --version v1 \
  --symbols AAPL,MSFT --horizons 7,30 \
  --exclude-features indicator_obv,indicator_adx \
  --export
```

**Notes:**

- Cannot use both `--features` and `--exclude-features` together
- Feature names must match those in `feature_definitions` table
- Non-existent feature names are silently ignored
- Default behavior unchanged: all features included when neither flag is specified

### Changed

#### ⚠️ BREAKING CHANGE: Inverted Trim Command Defaults

The default behavior of `trim-features` and `trim-prices` commands has been inverted for better data safety:

**Before (old behavior):**
- `g2 feat-trim --feature indicator_rsi_14 --before 2024-01-01` → Trimmed BOTH features AND prices
- `g2 prices-trim --before 2024-01-01` → Trimmed ONLY prices

**After (new behavior):**
- `g2 feat-trim --feature indicator_rsi_14 --before 2024-01-01` → Trims ONLY features (safer default)
- `g2 prices-trim --before 2024-01-01` → Trims BOTH prices AND features (cascade delete)

**Migration Guide:**

If you were relying on the old defaults, update your commands:

```bash
# Old command that trimmed features + prices:
g2 feat-trim --feature indicator_rsi_14 --before 2024-01-01

# New equivalent (add --trim-prices flag):
g2 feat-trim --feature indicator_rsi_14 --before 2024-01-01 --trim-prices

# Old command that trimmed only prices:
g2 prices-trim --before 2024-01-01

# New equivalent (add --no-trim-features flag):
g2 prices-trim --before 2024-01-01 --no-trim-features
```

**Rationale:**
- `trim-features` now defaults to feature-only deletion (safer, avoids accidental price loss)
- `trim-prices` now defaults to cascading delete of derived features (maintains data consistency)
- Use explicit flags when you need the non-default behavior

### Added

- New function `trim_all_computed_features()` in `g2.db.ingest` for trimming all computed features by date range and optional symbols
- New flag `--trim-prices` for `feat-trim` command (default: False)
- New flag `--no-trim-features` for `prices-trim` command (default: trims features)
- Comprehensive tests for new trim behavior in `tests/test_trim_commands.py`
