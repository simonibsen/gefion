# Implementation Plan: Short-Side Execution for Backtests

**Branch**: `009-short-side-execution` | **Date**: 2026-07-09 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/009-short-side-execution/spec.md`

## Summary

Make a short a first-class backtest position so gefion can act on the
negative-directionality edges its research core already detects. The portfolio
gains **signed positions** (negative shares = short), the engine gains
**`short`/`cover`** actions, short economics are modeled honestly (borrow fee per
day, dividends owed, margin/buying-power, an unbounded-loss guardrail), metrics
and exposure are correct under shorts, and the six built-in strategies emit
shorts on their bearish branch. A per-run **`long_only` (default) / `long_short`**
mode gates every short behavior, so all existing backtests reproduce
byte-identically (the regression gate) and shorts are opt-in. No schema change —
backtests are computed and returned, not persisted.

## Technical Context

**Language/Version**: Python 3.10+ (existing codebase)
**Primary Dependencies**: numpy (metrics), existing `gefion.backtest` package
(engine, portfolio, costs, risk, metrics, slippage, sizing), `gefion.observability`
**Storage**: **None new.** Backtests run in-memory and return a result payload;
there is no backtest-results table (verified — nothing in `sql/schema.sql`, no
INSERT). Mode and short trades are recorded in the returned payload. **No DDL →
Schema Governance is a no-op for this feature.**
**Testing**: pytest; the short-economics tests are pure/in-memory (no DB);
strategy tests reuse existing synthetic-price fixtures
**Target Platform**: dev + prod (same code path; backtests are local compute)
**Project Type**: single project — extends `src/gefion/backtest/*` and
`src/gefion/strategies/*`
**Performance Goals**: no material slowdown on long-only runs (the default path is
unchanged); short bookkeeping is O(open positions) per bar
**Constraints**: `long_only` byte-identical to today (SC-902 regression gate);
unbounded loss represented, not clamped; parameterized SQL only where DB is touched
(dividend reads); short costs default to sensible borrow assumptions, overridable
**Scale/Scope**: 6 strategies updated; ~50-symbol × multi-year daily backtests

## Constitution Check

*GATE: evaluated against constitution v1.9.0 — PASS (no pending items).*

| Principle | Status | Notes |
|---|---|---|
| I. Database-First | PASS | No schema change; backtests are in-memory. Dividend data read from the existing `stock_ohlcv.dividend_amount`. |
| II. TDD | PASS | Every phase Red→Green; short-economics unit tests are pure and hand-checkable. |
| III. CLI-First | PASS | `--mode long_only\|long_short` + short params on `backtest run` (+ compare), mirrored in MCP `backtest_run`, same increment. |
| IV. Observability | PASS | Short-execution + margin-event spans with attributes. |
| V. CLI Presentation | PASS | Output via `out.*`/`get_output`; `--json` unchanged shape plus additive fields. |
| VI. Simplicity | PASS | Signed shares reuse the existing `calculate_equity` (`shares × price` is already correct MTM for negative shares); extend `TransactionCosts`/`RiskManager` rather than rebuild. |
| Schema Governance | PASS (no-op) | No DDL — confirmed no backtest-results table. |
| Secrets | PASS | None involved. |

*Post-Phase-1 re-check: no new violations; still zero DDL.*

## Project Structure

### Documentation (this feature)

```text
specs/009-short-side-execution/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R9
├── data-model.md        # Phase 1: signed position, short round-trip, margin state
├── quickstart.md        # Phase 1: end-to-end short backtest walkthrough
├── contracts/
│   ├── cli.md           # backtest run --mode + short params; result payload additions
│   ├── mcp.md           # backtest_run mode/short args
│   └── interfaces.md    # engine action set, portfolio signed-position API, cost/risk hooks
└── tasks.md             # Phase 2 (/speckit.tasks — not here)
```

### Source Code (repository root)

```text
src/gefion/backtest/
├── portfolio.py         # signed positions: short() / cover(); calculate_equity already signed-correct
├── engine.py            # recognize short/cover; mode gate; daily borrow accrual + dividend debit; margin guardrail
├── costs.py             # TransactionCosts gains borrow-rate + dividend handling (ShortCosts config)
├── risk.py              # RiskManager gains margin/buying-power + short exposure limits + forced-cover
├── metrics.py           # short round-trip P&L (entry−exit); winning short = price-down; exposure series
└── sizing.py            # (reused; short reuses notional/vol sizing)

src/gefion/strategies/
├── mean_reversion.py    # short the overbought (mode-gated)
├── momentum.py          # short the losers
├── breakout.py          # short downside breakouts (instead of flatten)
├── pairs_trading.py     # genuine long-short from flat (short rich leg + long cheap leg)
├── ml_signal.py         # short on strong_down / low q10
└── ml_filter.py         # short-side filtering

src/gefion/cli.py        # backtest run/compare: --mode, --borrow-rate, --max-gross-exposure, margin
mcp-server/server.py     # backtest_run: mode + short params

tests/
├── test_backtest_short_positions.py      # portfolio signed positions, short/cover, MTM, P&L
├── test_backtest_short_costs.py          # borrow accrual, dividend debit, symmetric txn costs
├── test_backtest_short_risk.py           # margin/buying-power, forced cover, exposure limits, negative equity
├── test_backtest_long_only_regression.py # SC-902: default path byte-identical
├── test_backtest_short_metrics.py        # winning short = win; drawdown/exposure under shorts
└── test_strategies_short_side.py         # each strategy shorts in long_short, flattens in long_only
```

**Structure Decision**: extend the existing `gefion.backtest` package in place —
the signed-position insight (existing `calculate_equity` is already correct for
negative shares) means this is an extension, not a rewrite (Constitution VI).

## Interfaces, Documentation & Learning Impact *(mandatory)*

- **Three interfaces**:
  | Operation | CLI | MCP | UI |
  |---|---|---|---|
  | Run a long/short backtest | `backtest run --mode long_short [--borrow-rate …] [--max-gross-exposure …]` | `backtest_run` (mode + short args) | Backtesting page gains a mode toggle + short params; results show long/short exposure |
  | Compare long-only vs long-short | `backtest compare` across modes | `backtest_compare` | side-by-side |
  Default (`long_only`) needs no flag and is unchanged everywhere.
- **Documentation**: README (backtest mode row), USER_GUIDE (short section:
  direction, costs/risk, mode), MCP_WORKFLOWS (`backtest_run` mode/short args),
  ARCHITECTURE (execution-model section: signed positions, short economics),
  `/gefion` routing ("short this / long-short backtest").
- **Learning materials**: `gefion-learn.md` Module 4 (Backtesting) gains a
  short-side aside — "an edge is a direction; gefion detects both but only acted
  long until 009" — and a checkpoint: *why does a winning short show as a win
  when the price went down, and why must borrow cost + unbounded loss be modeled?*
- **Delivery rule**: mode/params + docs land with the code per increment.

## Increment plan (value & safety ordering)

1. **US2 regression harness first** — capture reference long-only runs and lock
   byte-identical reproduction (the gate that lets everything else proceed safely).
2. **US1 signed positions** — portfolio `short`/`cover`, engine actions, MTM,
   P&L; the foundation.
3. **US3 short economics** — borrow accrual, dividend debit, margin/buying-power,
   forced-cover guardrail, exposure limits (honesty; a short must not look free).
4. **US4 metrics** — winning-short accounting, drawdown/returns under shorts,
   exposure series.
5. **US5 strategies** — the six emit short/cover on their bearish branch,
   mode-gated; `pairs_trading` becomes genuinely long-short.
6. **Surfaces + docs** — CLI/MCP mode & params, docs, curriculum (each increment).
7. **Polish** — span-check, fresh-suite with the long-only regression gate green,
   then PR.

## Complexity Tracking

*No constitutional violations.* Notably **no DDL and no new external dependency** —
the change lives entirely inside the existing backtest package, and the signed-
position reuse of `calculate_equity` keeps the mark-to-market math from being
reinvented.
