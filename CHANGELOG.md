# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

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
