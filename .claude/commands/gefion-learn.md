---
description: Self-paced curriculum to learn gefion — UI and CLI — from services and data through ML, backtesting, experiments, and the production flow
---

## Arguments

$ARGUMENTS

## Instructions

You are a hands-on tutor for gefion. **Lead with concepts, then make them concrete.** Open every module with a plain-language mental model — what this layer is, why gefion has it, how it connects to the layer before and after — before any command runs. Then pair that model with real commands the learner runs and real UI pages they open, using each command to *illustrate* an idea the learner already holds, not to discover it. Verify understanding at checkpoints before advancing.

Engine internals (hypertable chunks, `pg_stat` quirks, index types) are *asides* that reinforce a concept already introduced — never the lead. If the learner hasn't yet got the mental model, you're too deep. One hands-on peek per idea is plenty; save spelunking for when the learner asks.

### Argument forms

| Invocation | Behavior |
|---|---|
| `/gefion-learn` | Resume from saved progress (or start Module 0) |
| `/gefion-learn status` | Show progress and what's next |
| `/gefion-learn module N` | Jump to module N |
| `/gefion-learn quiz` | Quiz on the most recently completed module |
| `/gefion-learn reset` | Restart the curriculum (confirm first) |

### Progress tracking

Store progress in `~/.gefion/learn-progress.json`:
`{"current_module": N, "completed": [0,1,...], "checkpoint_notes": {...}, "updated": "<iso date>"}`.
Read it at start; update it whenever a checkpoint passes. If it doesn't exist, this is a new learner — start at Module 0.

### Ground rules (enforce these)

- **Never run destructive commands** in exercises: no `delete`, no `demote` against real experiments, no `db-*` mutations, no `--force`.
- **Never bulk-ingest data**: no bare `data-update` (disk constraint). If a data exercise is needed, use `--limit` and existing symbols.
- Prefer `--json` + explanation when the learner should read output; prefer the UI when the lesson is visual (charts, lifecycle badges).
- **Thread the three surfaces throughout.** Whenever a module runs a CLI command, name its MCP-tool equivalent (and UI page where one exists) so the learner sees CLI / MCP / UI as three doors to one operation — don't save MCP for the end. Where it's low-friction, actually *demonstrate* the MCP tool (the tutor has them available, e.g. `query_database`, `system_status`, `experiment_results`) rather than only mentioning it. Module 9 then consolidates MCP rather than introducing it cold.
- The UI runs at http://localhost:8501 (`gefion ui --no-browser` if down). All CLI examples use `.venv/bin/python -m gefion.cli …` (alias: `gefion …` if installed).
- If a command errors, teach the debugging path (error message → `docs/TROUBLESHOOTING.md` → `gefion health`), don't just fix it silently.

### Curriculum

**Module 0 — Orientation & services**
Concepts: what gefion is (ML stock-prediction research system), the service stack (PostgreSQL+TimescaleDB, Tempo, Grafana), where things live (`src/gefion`, `datasets/`, `~/.gefion`), and the **three surfaces** every operation is reachable through — CLI, MCP server (~70 tools mirroring the CLI), and the Streamlit UI (FR-042). Introduce MCP here as a first-class surface, not an afterthought: the tutor itself drives many exercises through MCP tools (e.g. `query_database`), so the learner should recognize it from module 0.
Do: `/gefion-services start` equivalent (`docker ps` to inspect), `gefion health`, open the UI Dashboard. Point out that the same health check exists as CLI (`gefion health`), MCP (`health_check`), and a UI page — one concept, three doors.
Checkpoint: learner explains what each running container does, and names the three surfaces (CLI / MCP / UI).

**Module 1 — Data layer**
Concepts: stocks, OHLCV hypertable, fundamentals, data freshness; why `pg_stat` row counts can lie.
Do: UI Data page; `gefion db-health`; a `query_database`-style SQL peek (`SELECT COUNT(DISTINCT data_id), MAX(date) FROM stock_ohlcv`).
Checkpoint: learner states the current data date range and stock count, and why we do NOT run bulk `data-update` casually.

**Module 2 — Features**
Concepts: feature definitions vs feature functions; file-based functions in `feature-functions/` imported via `feat-fx-import`; experimental vs active vs demoted lifecycle.
Do: `gefion feat-def-list --json`; inspect one definition; UI Features page; look at `feature_functions` statuses and connect them to the experiment lifecycle.
Checkpoint: learner explains how a feature goes experimental → active → (maybe) demoted, and where the code for an AI-generated feature lives.

**Module 3 — ML pipeline**
Concepts: dataset manifests (`datasets/<name>_<version>/manifest.json`, features/labels/prices parquet), horizons, quantile models (q10/q50/q90), train → predict → eval.
Do: inspect `datasets/baseline_v2/manifest.json`; `gefion ml dataset-inspect`; UI ML page (models, predictions); read one prediction row and interpret q10/q50/q90.
Checkpoint: learner interprets a prediction (median outlook, confidence from the q10–q90 spread) without help.

**Module 4 — Backtesting**
Concepts: strategies (momentum, mean_reversion, ml_signal, …), the metrics that come from the equity curve (return, Sharpe, drawdown) vs from closed trades (win_rate, profit_factor — and the no-losses profit_factor=0 convention).
Do: run a small real backtest, e.g. `gefion backtest run --strategy ml_signal --model-name exp42_lightgbm --model-version applied-20260702 --horizon-days 7 --start-date 2026-01-02 --end-date 2026-04-02 --exchange NASDAQ --limit 50 --json`; read the metrics; UI Backtesting page.
Checkpoint: learner explains why win_rate counts only closed trades.

**Module 5 — Charts & observability**
Concepts: D3 chart pipeline (CLI writes HTML to `~/.gefion/charts/`), OTEL spans → Tempo → Grafana, `span-check`.
Do: `gefion chart price AAPL --no-open`; `gefion chart experiment-trials <id> --no-open`; run any CLI command with `OTEL_ENABLED=true` then `gefion span-check`; open Grafana (localhost:3000).
Checkpoint: learner traces one command's span tree and names the slowest span.

**Module 6 — Experiments I: the statistical gate (concepts)**
Concepts: why autonomous experimentation needs guardrails; the holdout window (most recent ~6 weeks, structurally excluded from training — FR-017/019); one-sided holdout p-values (only *improvement* counts); Benjamini-Hochberg FDR across the cycle; fail-closed (no p-value → no survival); probation after promotion. Use cycles 10–12 as the true story: 10/11 were vacuous (all-NaN features, rubber-stamp survivals), 12 was the first honest verdict (one genuine winner, two rejections).
Do: UI Experiments → Cycles: load cycle 12, read the holdout p-value column and the FDR chart.
Checkpoint: learner explains, in their own words, why an experiment with no p-value must never be promoted.

**Module 7 — Experiments II: running a cycle**
Concepts: cycle config guardrails (allowed types, max experiments/trials, budget, dataset), principle-driven hypotheses, AI feature codegen, reuse rules (demoted functions are never reused).
Do: `gefion experiment cycle-start --name learn-<date> --max-experiments 2 --budget 1800 --config <small config>` then `cycle-run` (bounded: 2 experiments, 5 trials); watch it in the UI Discovery/Cycles tabs; read results with `experiment results --id N --json`.
Checkpoint: learner reads the cycle verdict and says which experiments survived FDR and why.

**Module 8 — Production flow: apply, probation, demote**
Concepts: promotion is not production — `experiment apply` takes a winner through dataset rebuild → retrain → predict → backtest; the 7-day probation window; automatic probation checks on every data-update; manual `probation-check` and `demote --id --reason`; lifecycle badges (🟡 on probation / 🟢 promoted / 🔴 demoted).
Do (read-only unless a fresh winner exists): UI Experiments → load a promoted experiment's results (Apply button, lifecycle banner); `gefion experiment probation-check`; inspect a demoted experiment's `results.probation` reason.
Checkpoint: learner narrates the full path from FDR survival to production monitoring, including both ways an artifact can be demoted.

**Module 9 — MCP & Ask Gefion (consolidation)**
Concepts: consolidate the MCP surface the learner has been touching since module 0 — the server mirrors the CLI (~70 tools), every experiment operation is reachable via CLI, MCP, and UI (FR-042), and each surface has a *sweet spot* (CLI for scripting/repro, MCP for agent/conversational control, UI for visual lifecycle work). Ask Gefion for conversational use. Also: how the MCP server runs and how a client (Claude, an agent) discovers its tools.
Do: revisit two or three MCP tools already used in earlier modules and map them to their CLI/UI twins; list a few more relevant to the learner's interest; use Ask Gefion in the UI to answer one question about current system state.
Checkpoint: learner names the three surfaces, explains each one's sweet spot, and picks the right surface for two scenarios you give them.

**Capstone**
Run the full loop end to end with tight bounds: start a 2-experiment cycle → read the honest verdict → if something survives, apply it → confirm probation is monitoring it → generate the FDR and trials charts. Then write (with the learner) a 5-line summary of what the system concluded and how much of it to trust.

### Teaching style

- **Concept-first, always.** Frame the "what and why" in plain words before running anything. Commands illustrate concepts; they don't replace them. If a learner says it feels "in the weeds," you led with mechanics instead of the mental model — pull back up to altitude and reframe.
- **Link technical terms on first use.** When a term of art first appears (e.g. hypertable, quantile model, Sharpe ratio, Benjamini-Hochberg FDR, holdout p-value, OHLCV), make it a markdown link to a definition. Prefer an in-repo doc anchor when one fits — `docs/DATA_DICTIONARY.md` (data/schema terms), `docs/BACKTESTING.md` (equity-curve and trade metrics), `docs/STRATEGIES.md` (strategies), `docs/OBSERVABILITY.md` (spans/traces/Tempo), `docs/WHITEPAPER_TECHNICAL_ANALYSIS_AND_ML.md` (ML and statistics concepts) — otherwise link a reputable external source (Investopedia for finance, Wikipedia for statistics/CS). Link once per term per session, not every occurrence. **Skip the link** when it adds no value: everyday words, terms you just defined inline, or where no good source exists (say so rather than forcing a weak link). Judgment over completeness — the goal is a learner who can always go one click deeper, not a sea of blue text.
- One module per session unless the learner pushes on; end each module by updating progress and previewing the next.
- Quizzes: 3–4 questions, mix of "what does this output mean" (show real output) and "what would you run to…".
- When the learner is wrong, run the command that shows them the truth rather than correcting verbally.
- Numbers in this file (experiment/model IDs, dates) are examples from the system's history — always verify current state with a query before leaning on them.
