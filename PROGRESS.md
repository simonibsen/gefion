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
- `g2 data-update` - Update prices and compute indicators
- `g2 feat-fx-export/import` - Version control for feature functions
- `g2 feat-def-export/import` - Version control for feature definitions
- `g2 feat-fx-list` - List registered functions
- `g2 feat-def-list` - List feature definitions
- `g2 prices-ingest` - Ingest specific symbols
- `g2 feat-compute` - Compute features for symbols

### Performance
- **Parallel processing**: Adaptive worker scaling (2-16 workers)
- **Bulk operations**: Single query filters 5,600 symbols in <1s
- **Rate limiting**: 1.0s minimum spacing prevents API throttling
- **Batch inserts**: 200-row chunks for 10-50x faster writes

## Recent Changes

### December 13, 2025
- **Rate limiting fix**: Added minimum 1.0s spacing to prevent burst pattern errors
- **Error detection**: AlphaVantage API errors now properly detected and reported (vs misleading "empty payload")
- **Documentation consolidation**: Reorganized docs/ into focused architecture/performance guides + archive/

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

**Status**: Data pipeline complete, ML implementation not started

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
