# g2 Project Progress Analysis

## Current State vs HIGHLEVEL.md Plan

### ✅ Phase 1: Foundation (COMPLETE)
- [x] Project setup (repo structure, dependencies, configs)
- [x] Database schema and connection layer (PostgreSQL + TimescaleDB)
- [x] AlphaVantage API client with rate limiting
- [x] Multi-symbol ingestion (5,595 stocks)
- [x] Test framework setup
- [x] **Bonus**: Advanced optimizations (bulk filtering, skip logic, chunk management)

**Status**: Phase 1 is **fully complete** and exceeded requirements with production-grade optimizations.

### ⚠️ Phase 2: Data Pipeline (PARTIAL)
- [x] Multi-symbol ingestion
- [x] Data validation and cleaning (chunk safety, error handling)
- [ ] **Feature engineering module** ← NEXT STEP
- [ ] **Dataset builder with rolling windows** ← NEXT STEP
- [ ] **Label generation (actual returns)** ← NEXT STEP

**Status**: Infrastructure is excellent, but ML-specific data preparation is **not started**.

### ❌ Phase 3-6: Not Started
- [ ] Model development
- [ ] Inference pipeline
- [ ] Analytics layer
- [ ] Production monitoring

---

## What You Have Now

### Data Infrastructure (Excellent)
- **5,595 stocks** with OHLCV data
- **Technical indicators**: RSI, ADX, PSAR, SMA, EMA, MACD, Bollinger Bands, Stochastic
- **Optimized ingestion**: Bulk filtering (91% skip rate), no deadlocks
- **TimescaleDB**: Chunk management for historical data
- **Connection pooling**: Production-grade concurrency

### What's Missing for ML

1. **Feature Engineering Module** - No code to prepare features for ML
   - Need: Normalize indicators, create lagged features, handle missing data
   - Need: Create sliding windows (e.g., past 30 days → predict next 7/30/90 days)

2. **Label Generation** - No code to calculate target returns
   - Need: Forward returns (e.g., 7-day, 30-day, 90-day % change)
   - Need: Handle splits, dividends, survivorship bias

3. **Dataset Builder** - No code to create train/val/test splits
   - Need: Walk-forward validation (time-aware splits)
   - Need: PyTorch DataLoader

4. **Model Training** - No PyTorch model exists
   - Need: Model architecture (LSTM/TCN/Transformer)
   - Need: Training loop, validation, checkpointing

---

## Immediate Next Steps (Phase 2 Completion)

### Step 1: Feature Engineering Module (Week 1)

Create `src/g2/features/engineer.py`:

```python
class FeatureEngineer:
    """Transform raw data into ML-ready features"""

    def __init__(self, lookback_days=30):
        self.lookback_days = lookback_days

    def prepare_features(self, symbol, as_of_date):
        """
        Get features for a single prediction.

        Returns:
            features: Dict[str, float]  # Normalized indicators + derived features
        """
        # 1. Fetch last N days of data
        # 2. Normalize indicators (z-score or min-max)
        # 3. Create derived features (momentum, volatility, trend)
        # 4. Handle missing data
        pass

    def create_training_dataset(self, symbols, start_date, end_date):
        """
        Create full training dataset with labels.

        Returns:
            X: np.ndarray (N, lookback_days, n_features)
            y: np.ndarray (N, n_horizons, n_quantiles)
            metadata: List[Dict]  # symbol, date for each sample
        """
        pass
```

**Deliverables**:
- [ ] Feature normalization (z-score per indicator)
- [ ] Sliding window creation (30-day lookback)
- [ ] Missing data handling (forward fill + mark)
- [ ] Derived features (price momentum, volatility, trend strength)
- [ ] Tests for feature generation

### Step 2: Label Generation (Week 1)

Create `src/g2/features/labels.py`:

```python
class LabelGenerator:
    """Generate forward return labels for training"""

    def calculate_forward_returns(self, symbol, date, horizons=[7, 30, 90]):
        """
        Calculate forward returns for given horizons.

        Returns:
            returns: Dict[int, float]  # {7: 0.05, 30: 0.12, 90: -0.03}
        """
        # 1. Get future prices
        # 2. Calculate % returns
        # 3. Handle corporate actions (splits)
        # 4. Mark survivorship bias
        pass

    def generate_quantile_labels(self, returns, quantiles=[0.1, 0.5, 0.9]):
        """Convert returns to quantile labels for quantile regression"""
        pass
```

**Deliverables**:
- [ ] Forward return calculation (7/30/90 day)
- [ ] Adjustment for splits/dividends
- [ ] Survivorship bias handling
- [ ] Quantile label generation
- [ ] Tests for label calculation

### Step 3: Dataset Builder (Week 2)

Create `src/g2/ml/dataset.py`:

```python
import torch
from torch.utils.data import Dataset, DataLoader

class StockDataset(Dataset):
    """PyTorch Dataset for time-series prediction"""

    def __init__(self, X, y, metadata):
        self.X = torch.FloatTensor(X)
        self.y = torch.FloatTensor(y)
        self.metadata = metadata

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]

def create_dataloaders(symbols, train_split, val_split, batch_size=32):
    """
    Create train/val/test dataloaders with walk-forward splits.

    Args:
        symbols: List of stock symbols
        train_split: ('2015-01-01', '2020-12-31')
        val_split: ('2021-01-01', '2021-12-31')

    Returns:
        train_loader, val_loader, test_loader
    """
    pass
```

**Deliverables**:
- [ ] PyTorch Dataset class
- [ ] Walk-forward validation splits
- [ ] DataLoader with batching
- [ ] Data augmentation (optional)
- [ ] Tests for dataset creation

### Step 4: Verify Data Quality (Week 2)

Before training, validate:

```python
# Create a diagnostic script
def validate_dataset():
    """Run data quality checks"""

    # 1. Check feature distributions
    # 2. Verify no lookahead bias
    # 3. Check for data leakage
    # 4. Validate label distributions
    # 5. Test on small sample (10 stocks)

    print("✓ All quality checks passed")
```

**Deliverables**:
- [ ] Data quality dashboard
- [ ] No lookahead bias verification
- [ ] Feature correlation analysis
- [ ] Sample predictions on validation set

---

## After Phase 2: Next Phases

### Phase 3: Model Development (Weeks 3-5)

**Goal**: Train first working model for 7-day horizon

**Tasks**:
1. **Baseline Model**: Simple LSTM or TCN
2. **Training Loop**: Loss function (quantile loss), optimizer, scheduler
3. **Validation**: Track metrics on validation set
4. **Checkpointing**: Save best models
5. **Experiment Tracking**: Log hyperparameters and results

**Deliverable**: Model that predicts 7-day return distribution (q10, q50, q90)

### Phase 4: Inference & Validation (Weeks 6-7)

**Goal**: Deploy model and measure real performance

**Tasks**:
1. **Inference CLI**: `g2 predict AAPL --horizon 7`
2. **Backtesting**: Run predictions on historical data
3. **Performance Metrics**: Quantile loss, calibration plots, Sharpe ratio
4. **Multi-Horizon**: Extend to 30-day and 90-day models

**Deliverable**: Three trained models with backtest results

### Phase 5: Analytics & Use Cases (Weeks 8-9)

**Goal**: Build useful applications on top of predictions

**Tasks**:
1. **Screening**: `g2 screen --upside-potential 0.2`
2. **Risk Analysis**: `g2 risk-report AAPL`
3. **Signals**: `g2 signals --strategy momentum`
4. **Dashboards**: Web interface or Jupyter notebooks

**Deliverable**: Complete trading toolkit

---

## Recommended Priority

### Option A: Start ML Now (Recommended)

**Why**: You have excellent data infrastructure. Time to use it!

**This Week**:
1. Create feature engineering module
2. Generate labels for 100 stocks (test dataset)
3. Build PyTorch Dataset
4. Train tiny model (1-layer LSTM) on 10 stocks
5. Verify predictions are reasonable

**Next Week**:
1. Scale to all 5,595 stocks
2. Implement proper validation
3. Train production model
4. Backtest results

### Option B: Finish Data Pipeline First

**Why**: Ensure data quality before ML

**This Week**:
1. Run full `g2 data-update` (verify no deadlocks)
2. Fix 76s write latency if it's still an issue
3. Create data quality dashboard
4. Verify all indicators are computing correctly

**Next Week**:
1. Start ML (same as Option A)

---

## Key Questions to Answer

### Before Starting ML:

1. **Data Quality**: Are you confident in your indicator calculations?
   - Run: `g2 validate-indicators --symbols AAPL,MSFT,GOOGL`
   - Check: Compare RSI/MACD values with TradingView or Yahoo Finance

2. **Data Completeness**: Do you have enough history?
   - Check: `SELECT symbol, MIN(date), MAX(date), COUNT(*) FROM stock_ohlcv GROUP BY symbol LIMIT 10`
   - Need: At least 2 years of data per stock for training

3. **Target Metric**: What defines success?
   - Directional accuracy > 55%?
   - Sharpe ratio > 1.0 in backtest?
   - Beat buy-and-hold?

### Architecture Decisions:

1. **Model Type**: Start with LSTM? TCN? Transformer?
   - **Recommendation**: Start with LSTM (proven, simple, fast)

2. **Loss Function**: Quantile loss? MSE? Combined?
   - **Recommendation**: Quantile loss (pinball loss) for distribution prediction

3. **Features**: Which indicators to use initially?
   - **Recommendation**: Start with core set (RSI, ADX, PSAR, SMA_50, SMA_200, Volume)
   - Add more later based on feature importance

---

## Success Criteria (Phase 2)

You'll know Phase 2 is complete when you can run:

```bash
# Generate training dataset
$ g2 create-dataset --symbols AAPL,MSFT --start 2020-01-01 --end 2023-12-31 --output train.pt

# Output should show:
# Created dataset: train.pt
#   Samples: 1,500
#   Features: (30, 15)  # 30 days lookback, 15 features
#   Labels: (3, 3)      # 3 horizons, 3 quantiles
#   Time range: 2020-01-01 to 2023-12-31
```

And have tests that verify:
- No lookahead bias (features only use past data)
- Labels are correctly calculated (forward returns match actual)
- Missing data is handled gracefully
- Dataset is reproducible

---

## Recommendation

**Start with Feature Engineering and Label Generation** (Option A, Week 1 focus).

You have excellent infrastructure. The ML part is now the bottleneck. Begin by:

1. **Today**: Create `src/g2/features/engineer.py` with feature normalization
2. **Tomorrow**: Create `src/g2/features/labels.py` with forward return calculation
3. **This Week**: Build small dataset (100 stocks, 1 year) and verify quality
4. **Next Week**: Train first model on test dataset

Your data pipeline is solid. Time to put it to use! 🚀

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
