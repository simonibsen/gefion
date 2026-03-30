# Research: Autonomous AI Experimentation Framework

**Date**: 2026-03-29 | **Branch**: 004-autonomous-experiments

## R1: Purged Cross-Validation Implementation

**Decision**: Implement purged k-fold CV using scikit-learn's `TimeSeriesSplit` as base with custom purging logic.

**Rationale**: López de Prado (2018, ch. 7) shows that standard k-fold CV on financial time series inflates accuracy by 30-50% due to information leakage from overlapping labels. Purged CV adds an embargo period between train and test folds, removing observations that overlap with the test set's label horizon.

**Implementation approach**:
- Custom `PurgedKFold` splitter compatible with scikit-learn's CV interface
- Parameters: `n_splits`, `embargo_pct` (fraction of test set size to purge), `prediction_horizon` (label lookahead in days)
- Purge window = prediction_horizon + embargo buffer on both sides of each test fold
- Works with existing `train_quantile_model()` and `train_classifier()` by passing as `cv` parameter

**Alternatives considered**:
- Standard `TimeSeriesSplit`: No purging — still leaks through overlapping labels
- Walk-forward optimization: Valid but produces fewer train/test splits, reducing statistical power
- López de Prado's `CombinatorialPurgedCV`: More statistically powerful but significantly more complex; defer to future iteration

## R2: Benjamini-Hochberg FDR Control

**Decision**: Use `scipy.stats.false_discovery_control()` (available since scipy 1.11) or manual BH implementation.

**Rationale**: When running N experiments per cycle, individual p < 0.05 tests give ~5% false discovery rate per test. With 20 experiments, expect 1 false positive. BH-FDR controls the expected proportion of false discoveries among all rejected hypotheses.

**Implementation approach**:
- Each experiment produces a p-value from holdout evaluation (paired t-test across stocks or bootstrap CI)
- All p-values in a cycle are collected and passed to BH procedure
- Configurable FDR rate (default 10%)
- Return: boolean mask of which experiments survive correction
- Track both raw p-value and FDR-corrected status per experiment

**Alternatives considered**:
- Bonferroni correction: Too conservative — divides significance threshold by N, rejecting most real effects
- Holm-Bonferroni: Less conservative than Bonferroni but still family-wise error rate (FWER), not FDR
- Permutation tests: More robust but computationally expensive (need 1000+ permutations per experiment)

## R3: Holdout Window Management

**Decision**: Rolling holdout window using the most recent N weeks of data, structurally excluded via date filtering in dataset-build.

**Rationale**: The holdout must be (a) the most recent data (closest to real trading conditions), (b) long enough for statistical power (~6 weeks × 5000 stocks = 150,000 stock-day observations), and (c) structurally excluded (not just by convention — the dataset builder physically filters it out).

**Implementation approach**:
- `HoldoutManager` class with configurable `holdout_weeks` (default 6)
- Computes `holdout_start_date` = max(stock_ohlcv.date) - holdout_weeks
- All dataset-build operations within experiments receive `max_date=holdout_start_date` parameter
- Holdout evaluation is a separate step that queries data in [holdout_start_date, max_date]
- As new data arrives (daily updates), holdout window rolls forward automatically
- Holdout dates stored in experiment_cycle record for reproducibility

**Alternatives considered**:
- Fixed holdout (one-time split): Doesn't roll with new data; holdout becomes stale
- Random holdout (scattered dates): Breaks time series structure; introduces look-ahead bias
- Multiple holdouts (nested CV): More robust but multiplies compute cost by number of outer folds

## R4: Data Discovery Approach

**Decision**: Discovery queries database metadata (information_schema, pg_stat, feature_definitions) to produce a structured inventory, then cross-references against principles catalog.

**Rationale**: The agent needs to know what data is available before proposing experiments. Discovery must be fast (< 30s) and not require scanning actual data — use metadata and statistics only.

**Implementation approach**:
- Query `information_schema.columns` for table schemas
- Query `pg_stat_user_tables` / hypertable chunk stats for row counts and freshness
- Query `feature_definitions` for existing features (active and inactive)
- Query `feature_functions` for available compute functions and their parameter schemas
- Cross-reference with principles catalog: for each principle that suggests a feature, check if the required data source exists and has coverage
- Output: structured dict with data_sources, features, gaps, hypotheses

**Alternatives considered**:
- Full data scan (COUNT, MIN, MAX per column): Too slow on 100M+ row hypertables
- Cached discovery (periodic background job): Adds complexity; metadata queries are fast enough
- Discovery as a separate CLI command only: Agent needs it programmatically; make it a Python function callable from experiments and CLI

## R5: Principles Catalog Schema

**Decision**: YAML files with structured principle entries, one file per domain area.

**Rationale**: Principles are curated knowledge, not runtime data. YAML is human-readable, diffable in git, and easy to load as LLM context. Split by domain so the agent can load only relevant principles for the experiment type.

**Schema per principle**:
```yaml
- id: ldp-fractional-diff
  source:
    author: "López de Prado"
    title: "Advances in Financial Machine Learning"
    year: 2018
    chapter: "Ch. 5: Fractionally Differentiated Features"
  claim: "Fractional differentiation preserves memory while achieving stationarity"
  mechanism: "Applying a fractional difference operator (d between 0 and 1) to price series removes unit root while retaining long-range dependence that integer differencing destroys"
  experiment_types: [feature_engineering]
  testable_prediction: "Features using fractional differentiation (d~0.3-0.5) will show higher predictive power than integer-differenced returns while passing ADF stationarity test"
  experiment_design: "Create fractionally differentiated features for close price at d=[0.3, 0.4, 0.5]. Compare feature importance and model accuracy against standard returns."
  known_limitations: "Optimal d is asset-specific; computation requires full price history (not just rolling window)"
  data_requirements: ["stock_ohlcv.close"]
  empirical_status: untested
  experiments: []
```

**Alternatives considered**:
- Markdown per source (lens-of-power style): Harder to query programmatically; agent needs structured data
- Database table: Mixes curated knowledge with runtime data; principles change rarely, not per-transaction
- JSON: Less readable than YAML for human editing; functionally equivalent

## R6: Experiment Configuration Serialization

**Decision**: Extend existing `ExperimentConfig` dataclass with full serialization to/from JSON, stored in the experiments table's `config` JSONB column.

**Rationale**: The existing framework already stores config as JSONB. Extending rather than replacing preserves backward compatibility. Full serialization enables reproducibility (re-run same experiment) and reusability (apply config to different data window).

**Implementation approach**:
- Add fields to ExperimentConfig: `holdout_config`, `principle_id`, `null_hypothesis`, `discovery_context`, `data_split`
- `to_dict()` / `from_dict()` methods for JSON serialization
- Configs stored in experiments.config JSONB column (existing)
- CLI: `experiment show-config <id>` outputs full config; `experiment rerun <id> --start-date X --end-date Y` re-applies

**Alternatives considered**:
- Separate config table: Unnecessary indirection; JSONB column already handles arbitrary structure
- File-based configs: Harder to associate with experiment records; DB is already the source of truth

## R7: Resource Safety Checks

**Decision**: Pre-flight checks before experiment execution and periodic checks during long-running trials.

**Rationale**: Autonomous experiments could exhaust disk (computing features for 5000 stocks), memory (training large models), or overwhelm the database. Safety checks prevent cascading failures.

**Implementation approach**:
- Pre-flight: check disk space (>1GB free), memory (>500MB available), DB connection health
- During execution: check every N trials (configurable, default every 5)
- If threshold breached: pause experiment, preserve partial results, log warning
- Use `shutil.disk_usage()` for disk, `psutil.virtual_memory()` for memory (or `/proc/meminfo` fallback)
- New dependency: `psutil` (lightweight, widely used)

**Alternatives considered**:
- No checks (rely on OS): Experiments crash ungracefully; partial results lost
- External monitoring (Prometheus/alerts): Over-engineered for a single-user development tool
- Docker resource limits: Only works in containerized deployments; dev setup runs natively

## R8: D3 Experiment Visualizations

**Decision**: 4 new D3 templates extending the existing chart framework, with renderer functions in `renderers.py`.

**Rationale**: Reuses the proven D3/Jinja2 pattern from the chart migration (Phase 1-4 of the D3 plan). Templates are self-contained HTML strings rendered via `st.components.v1.html()`.

**Charts**:
1. **Trial scatter** (`experiment_trials.html`): X=trial number, Y=score, color=promoted/rejected. Highlights best trial. Tooltip shows parameters.
2. **FDR cycle summary** (`experiment_fdr.html`): X=experiment, Y=p-value (log scale). Horizontal line at FDR threshold. Green dots = promoted, red = rejected.
3. **Parameter heatmap** (`experiment_heatmap.html`): 2D grid of parameter combinations, color = score. For hyperparameter experiments with 2 key parameters.
4. **Feature importance** (`experiment_features.html`): Paired bar chart showing before/after feature importance rankings. Highlights new/modified features.

**Alternatives considered**:
- Plotly: Already replaced with D3 across the codebase; don't reintroduce
- Matplotlib static images: No interactivity (tooltip, zoom); poor UX
- Raw Streamlit charts: Limited customization; can't do FDR threshold line or interactive tooltips
