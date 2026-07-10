# Gefion Project Status

**Last Updated**: 2026-07-10

## Current Capabilities

### SPA Re-Verdict (spec 010 — new)
- Selection-aware check over a discovery run's counted family: `gefion regime
  discover spa <run>` answers "could the best candidate be the best of many
  lucky draws?" — Hansen SPA (consistent p is the verdict; lower/upper as
  diagnostics), joint stationary bootstrap (Politis–Romano, automatic
  Politis–White block length), seeded and reproducible
- Reconstruction honesty core: the family is rebuilt via the run's OWN code
  paths from ledger + pre-registration; recomputed per-unit p-values must
  reproduce stored ones (1e-9/1e-6) before any verdict — drift (price
  backfills) means an honest refusal naming the divergent units, never a
  verdict from a different world
- `spa_reverdicts` table: append-only beside the run (latest by timestamp);
  BH verdicts and the candidate ledger are never rewritten; surfaced in
  show/verdicts (`SPA: not yet run` when absent) and grades (loud
  family-failed flag, never auto-demotion); MCP `regime_discover_spa`
- Budget gate ENFORCED: `--budget > 200` or `--depth > 2` (V1_MAX_BUDGET/
  V1_MAX_DEPTH) refused unless the 2 most recent completed runs (same
  dataset version) carry passing latest re-verdicts; satisfaction recorded
  as `{gate: "spa", runs, reverdict_ids}` in the new run's pre-registration
- Standing negative control in CI: 40 seeded noise families in the
  production unit form (sign-aligned — measured nominal 3/60 vs raw 0/60)
  bounded by the exact binomial 99% bound; planted edge must reject; plus a
  full-pipeline noise world (empty family refuses; open family doesn't
  reject). In-run gate and signal_source rungs remain follow-ups (#87)

### Short-Side Execution (spec 009)
- gefion detects both directions but until 009 could only *act* long; now
  `gefion backtest run --mode long_short` makes a short a first-class position
  so negative-directionality edges are actable
- Signed positions (Portfolio.short/cover); the existing calculate_equity marks
  shares×price, already correct for negatives; short P&L is (entry−exit)×size
- Honestly costed & risk-bounded: borrow fee accrues daily, dividends owed while
  short, Reg-T margin + forced-cover guardrail (margin_events), exposure limits;
  equity may go negative (represented, not clamped)
- Metrics correct under shorts (a winning short is price-down); gross/net/long/
  short exposure series
- All six strategies emit short/cover on their bearish branch (mode-gated);
  pairs_trading is genuinely long-short; ml_signal/ml_filter short on down-class
  / low q10
- `long_only` (default) is byte-identical to pre-009 (SC-902 regression gate);
  short is opt-in and recorded. Live/paper trading stays out of scope — gefion
  emits validated signals, it doesn't trade

### Data Quality (spec 008)
- Provider-garbage detection without losing degenerate-but-real extremes:
  a declarative catalog (`data-quality/catalog.yaml`, every bound carrying its
  `why`) drives two convicting tiers (definitional bounds, cross-field
  recompute vs price) and two suspect-only corroboration tiers (temporal
  spike, robust-z cross-sectional)
- `data_quality_findings` audit ledger (DOUBLE PRECISION so it can't overflow
  on what it convicts; no FKs so it survives entity-delete); idempotent,
  supersede-never-erase
- Validation rides the write paths (`fundamentals-update`, `macro ingest`),
  never blocking a write; convicted values excluded at the feature-computation
  chokepoint so research is clean by default (`--include-flagged` opts in)
- `gefion quality findings|catalog|backfill|resolve` (+ MCP tools); db-health
  `data_quality` section; the backfill flags already-stored garbage changing
  zero values
- Universe hardening: test tickers excluded from research universes;
  asset_type/exchange fail-closed selectors (closes the universe-quality
  backlog item)

### First-Class Entities (spec 007)
- Entity identity is declared, not hard-wired: `computed_features.data_id`
  carries no FK; the pair (`feature_definitions.entity_table`, `data_id`) is
  the logical key, validated at registration (`gefion.entities.registry`)
- The constraint's replacements shipped BEFORE its removal (safety ordering):
  db-health `entity_integrity` orphan scan + `gefion data entity-delete`
  (registry-driven, dry-run default, blocker-aware — issues #75/#76's first
  landed increment)
- Macro home: `macro_series` + `macro_series_values`; `gefion macro ingest|list`
  (+ MCP `macro_ingest`/`macro_list`); VIX via `fred:VIXCLS` (INDEX_DATA is
  premium — key not entitled, verified live 2026-07-08); `macro_vix` feature
  consumable by discovery atoms and `regime interaction --by macro_vix` with
  zero equity-pipeline changes (SC-201 full-suite regression gate green)
- The registry is the feeds graph: data dictionary renders solid-FK/dashed-
  registry Mermaid edges by declared layer, flags consumer-less raw tables
- Docs-drift enforcement widened: every MCP tool, every CLI command, and
  every CLI group in the curriculum are now test-enforced
- **Prod rollout complete (T027, 2026-07-08)**: sloth migrated (compressed-
  hypertable dance documented in docs/DEPLOYMENT.md gotchas), VIX live
  (9,224 values 1990→present, fred:VIXCLS), db-health entity_integrity
  zeros, and run `vix-atom-proof` (id 7) evaluated a macro_vix atom with an
  EMPTY diagnostics ledger — the uncomputable-VIX diagnostic is gone (SC-203)

### Agentic Regime Discovery (spec 006)
- The system proposes and tests candidate regimes under structural guardrails:
  pre-registered bounded search spaces (atom grammar to depth K, three declared
  seams: `signal_source`, `grading_scheme`, `universe_filter`), nested segregation
  (`DiscoveryDataContext` — discovery cannot touch the outer holdout), candidate
  freeze before evaluation, inner-evidence screen, and ONE flat FDR family (0.01)
  that counts every candidate including the losers
- Four ledger tables: `regime_discovery_runs`, `regime_candidates`,
  `discovery_diagnostics` (sample-dependent vs structural), `regime_trust_grades`
- Three expressiveness tiers shipped: continuous-interaction, bounded grammar, and
  expressive (free-form ASTs + sandboxed detectors) gated by a single-use
  fresh-holdout reserve with recorded re-declaration justifications
- Forward-only trust grading: fold 1 = probation; backward era-slices descriptive
  only; regime-limited flag on early fold failure
- Standing negative control in CI: zero admissions across 20 noise seeds, ≥95%
  planted-regime recovery (measured 40/40); byte-reproducible runs
- Surfaces: `gefion regime discover` CLI group (start/list/show/ledger/verdicts/
  diagnostics/grades/grade-fold), mirrored `regime_discover_*` MCP tools, UI
  Regimes → Discovery tab; `regime_discovery` experiment type (high risk, never
  auto-approved); Module 10 in the learning curriculum
- **Follow-up filed**: Reality-Check/SPA bootstrap must land before per-cycle
  search budgets are raised beyond v1 defaults (backlog)
- **T047 first real-data run (sloth, 2026-07-07, run id 1 `first-hunt-prod`)**:
  bounded tier-1+2 discovery vs the 26.7-year dataset (18 candidates from 6
  atoms, 6 signals, depth 2, seed 42, `test_tickers` chain). Outcome: 6 grammar
  candidates showed genuine inner evidence (best inner p 0.005–0.015 over ~25y),
  **0 admitted, family size 0** — every outer bucket test refused at the
  effective-N floor (84 min-sample refusals, effective_n≈1 vs floor 20), all
  ledgered sample-dependent with quantitative reasons. Mostly/entirely
  rejections was the success criterion; the loop behaved exactly as designed.
  Learnings: (a) a 26-week outer holdout cannot hold 20 independent episodes of
  slow market regimes — future runs need a wider declared holdout or a declared
  lower floor, and `--min-effective-n` is not yet exposed on the CLI (small
  follow-up); (b) `stocks.asset_type` is entirely NULL on prod, so the default
  quality universe chain refuses — the run declared `test_tickers` explicitly;
  unblocks when the universe-quality backlog item populates asset types.

### Regime Slicing (spec 005)
- First-class regimes: named, causal, persistent market/sector/asset states
  (`regime_definitions` + `regime_labels` hypertable; declarative expression AST)
- `gefion regime` CLI group (define/compute/list/show/labels/interaction/archive/export/import),
  mirrored `regime_*` MCP tools, and a UI Regimes page
- Regime-sliced backtests: `backtest run --by-regime` → per-regime metrics that reconcile
  to the aggregate, with effective-sample low-power flags
- Continuous-interaction test (OLS + Newey-West HAC): does an edge scale with a variable?
- Conditional experiment verdicts: per-regime holdout p-values entered into one flat
  Benjamini-Hochberg family; fail-closed on low-power/undefined buckets

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
- Conformal calibration (`gefion ml calibrate`): additive shift corrections for nominal quantile coverage
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
- Full CLI (`gefion` command with subcommands)
- MCP server for natural language interaction (51 tools, RBAC with operator/developer roles)
- Claude Code skills: `/g2-dev` (development), `/g2` (operations), `/g2-services` (infrastructure)
- Textual TUI (in development on `siUI` branch)

### UI Error Feedback Loop
- Errors logged to `~/.gefion/ui_errors.jsonl` during UI sessions (background process failures, exceptions)
- On `gefion ui` exit, prints error summary to stdout — visible to Claude Code for diagnosis
- `g2.ui.errors` module: `log_ui_error()`, `read_session_errors()`, `clear_errors()`

### UI Reliability (branch: `001-ui-reliability`)
- **AI Actions page**: Renamed from "AI Prompts", promoted to 2nd position in sidebar
- **Conversation history**: Persistent chat thread (`~/.gefion/ai_history.jsonl`), capped at 100 exchanges, survives refresh
- **Error surfacing**: Session error count badge + expandable error list in UI (no external monitoring needed)
- **Command execution**: Form submission (no Enter needed), auto-refresh, Run button always available
- **CLI mapping correctness**: Fixed 8 broken MCP_TOOL_MAP entries, regression tests validate all mappings against `gefion --help`
- **Environment**: `CLAUDECODE` env var stripped for nested `claude -p` support
- **Layout**: Chat input above proactive actions and system overview
- **Documentation**: UI section added to USER_GUIDE.md

### Testing
- 1282 tests passing, 14 skipped
- Database tests require `ENABLE_DB_TESTS=1`
- Full suite: `ENABLE_DB_TESTS=1 DATABASE_URL="postgresql://gefion:gefionpass@localhost:6432/gefion" OTEL_ENABLED=false .venv/bin/python -m pytest`

### Data Coverage (as of 2026-02-16)
- Price data: 1999-11-01 to 2026-01-30
- Symbols: 102 (NASDAQ)
