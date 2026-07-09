# Tasks: Short-Side Execution for Backtests

**Input**: Design documents from `/specs/009-short-side-execution/`
**Prerequisites**: plan.md, spec.md, research.md (R1–R9), data-model.md,
contracts/ (all present). **No DDL — no owner-approval gate.**

**Tests**: INCLUDED — TDD is non-negotiable (Constitution II). Every
implementation task is preceded by a failing test (Red → Green), committed
together.

**Delivery rule** (plan, mandatory): each story lands its CLI/MCP surface and
docs *in the same increment*; the widened docs-drift tests enforce the mechanical
part.

**Execution ordering note**: spec priorities are US1/US2/US3 = P1, US4/US5 = P2 —
but the plan's safety rule runs **US2 (the byte-identical regression harness)
FIRST**, because it is the gate that guarantees no short work silently perturbs
existing long-only results.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Add a `mode` parameter (`long_only` default) plumbed through `BacktestEngine.__init__`/`run` and the strategy call site in `src/gefion/backtest/engine.py` — inert until later phases consume it (no behavior change yet; the seam only)

## Phase 2: US2 — Long-only reproducibility harness (P1) 🎯 the safety gate

**Goal**: lock that every existing backtest reproduces byte-identically before any
short code exists, and keep it locked as short paths are added.
**Independent test**: run reference strategies in default mode; equity curve,
metrics, and trades match a captured baseline exactly.

- [ ] T002 Write `tests/test_backtest_long_only_regression.py`: for a set of seeded strategies/datasets, assert the full result (equity curve points, metrics, trade log) equals a committed reference fixture; and that `mode` defaults to `long_only` when unspecified (RED — reference fixtures captured from current `main`)
- [ ] T003 Capture the reference fixtures from current behavior and make the harness GREEN with the T001 seam in place (proves the mode seam changed nothing); this file is the SC-902 gate re-run in every later phase

**Checkpoint**: a green, committed byte-identical baseline exists; all subsequent
phases must keep it green.

---

## Phase 3: US1 — Signed positions, short & cover (P1) 🎯 foundation

**Goal**: the portfolio holds a negative position and the engine opens/marks/
closes a short correctly — the mirror of a long.
**Independent test**: short a price that falls 10% and cover → +10% of notional
(minus costs); rising 10% → −10%.

- [ ] T004 [US1] Write `tests/test_backtest_short_positions.py`: `Portfolio.short` opens a negative position and credits proceeds; `calculate_equity` marks it up as price falls (research R1); `cover` realizes `(entry−exit)×size` and records a short round-trip; partial cover attributes P&L pro-rata; a cover never flips into a long (clamped); flip long→short requires explicit close+open (RED)
- [ ] T005 [US1] Implement `Portfolio.short`/`Portfolio.cover` (signed positions) in `src/gefion/backtest/portfolio.py`; leave `buy`/`sell` untouched (GREEN)
- [ ] T006 [US1] Write `tests/test_backtest_short_positions.py` (engine part): `_execute_signal` routes `short`/`cover` in `long_short` mode and drops them in `long_only`; the trade log carries `side`/`action` (RED)
- [ ] T007 [US1] Implement `short`/`cover` routing + mode gate in `src/gefion/backtest/engine.py::_execute_signal` (GREEN); rerun the T002 regression harness — long_only still byte-identical

---

## Phase 4: US3 — Short economics: costs, margin, guardrail (P1)

**Goal**: a short is not free or unbounded-safe — borrow fee per day, dividends
owed, buying-power consumption, and a forced-cover guardrail.
**Independent test**: hold a short across a borrow window and an ex-div date →
equity reflects both; drive loss past maintenance → forced-cover event fires.

- [ ] T008 [US3] Write `tests/test_backtest_short_costs.py`: a short held D days accrues `shares×price×rate/252` per day (equity reduced); a short across an ex-dividend date is debited the dividend (from `stock_ohlcv.dividend_amount`); commission/spread/impact/slippage apply symmetrically to `short`/`cover` (RED)
- [ ] T009 [US3] Extend `src/gefion/backtest/costs.py` (borrow rate + `accrue_borrow`) and wire the daily borrow accrual + ex-div dividend debit into `src/gefion/backtest/engine.py`'s per-bar loop (GREEN)
- [ ] T010 [US3] Write `tests/test_backtest_short_risk.py`: a short exceeding buying power is rejected/sized down and recorded; a short breaching maintenance margin emits a logged `forced_cover` margin-event (reusing the engine's exit-signal-first seam); `max_gross_exposure`/`max_short_exposure` constrain new shorts; equity may go negative (represented, not clamped — research R9) (RED)
- [ ] T011 [US3] Extend `src/gefion/backtest/risk.py` (`RiskManager`: Reg-T buying power, short exposure limits, forced-cover exit signals) and wire into the engine (GREEN); regression harness still green

---

## Phase 5: US4 — Metrics correct under shorts (P2)

**Goal**: a winning short reads as a win; drawdown/returns reconcile; exposure is
visible.
**Independent test**: a short-only strategy over a declining market reports
positive return and win rate reconciling to the equity curve.

- [ ] T012 [US4] Write `tests/test_backtest_short_metrics.py`: a short round-trip with entry>exit counts as a win and raises `profit_factor`; drawdown/returns reconcile to the (signed-correct) equity curve on a mixed long-short run; gross/net/long/short `exposure` series is emitted; the no-losses `profit_factor=0` convention is unchanged (RED)
- [ ] T013 [US4] Implement short-aware per-trade P&L sign and the exposure series in `src/gefion/backtest/metrics.py` (GREEN); regression harness still green (long-only metrics unchanged)

---

## Phase 6: US5 — Strategies act on both directions + surfaces (P2)

**Goal**: the six strategies emit shorts on their bearish branch (mode-gated), and
the mode/params reach CLI + MCP + docs in the same increment.
**Independent test**: each strategy shorts in `long_short` on its bearish trigger
and flattens on the same trigger in `long_only`.

- [ ] T014 [US5] Write `tests/test_strategies_short_side.py`: each of `mean_reversion` (short overbought), `momentum` (short losers), `breakout` (short downside breakout), `pairs_trading` (short rich + long cheap from flat), `ml_signal`/`ml_filter` (short on `strong_down`/low `q10`) emits `short`/`cover` in `long_short` and its existing long/flat signals in `long_only` (RED)
- [ ] T015 [US5] Thread `mode` into the strategies and implement their bearish `short`/`cover` branch in `src/gefion/strategies/{mean_reversion,momentum,breakout,pairs_trading,ml_signal,ml_filter}.py` (GREEN); regression harness still green
- [ ] T016 [P] [US5] CLI: `--mode`, `--borrow-rate`, `--initial-margin`/`--maintenance-margin`, `--max-gross-exposure`/`--max-short-exposure` on `backtest run` (and `--mode` on `compare`) in `src/gefion/cli.py`; result payload gains `mode`/`exposure`/`margin_events`/`short_costs` (tests: interface assertions in `tests/test_strategies_short_side.py` or a surfaces test) (RED→GREEN)
- [ ] T017 [P] [US5] MCP: `backtest_run` (+`backtest_compare`) gain the mode/short args in `mcp-server/server.py`; `/gefion` operator-skill routing ("short this / long-short backtest", always surface margin events + short costs); docs: README backtest-mode row, USER_GUIDE short section, MCP_WORKFLOWS args, ARCHITECTURE execution-model section; docs-drift green

---

## Phase 7: Polish & Cross-Cutting

- [ ] T018 [US5] Learning materials: `.claude/commands/gefion-learn.md` Module 4 short-side aside + checkpoint (why a winning short is price-down; why borrow cost + unbounded loss must be modeled); curriculum drift test green
- [ ] T019 Observability: run a long_short backtest with `OTEL_ENABLED=true`; `gefion span-check` — short-execution and margin-event spans parented, no orphans
- [ ] T020 Full-suite pre-flight: drop `gefion_test`, complete suite against a fresh DB (capture the exit code — the pipe-masking lesson); the T002 long-only regression harness green; docs-drift green
- [ ] T021 Update the roadmap stub `.specify/memory/progress.md` (short-side execution shipped); note that gefion now acts on both directions; PR the branch, merge on green

---

## Dependencies & Story Completion Order

```
Setup (T001: mode seam)
  └─> US2 harness (T002–T003: byte-identical baseline)   🎯 the gate
        └─> US1 (T004–T007: signed positions + short/cover)
              └─> US3 (T008–T011: costs + margin + guardrail)
                    └─> US4 (T012–T013: metrics under shorts)
                          └─> US5 (T014–T017: strategies + CLI/MCP/docs)
                                └─> Polish (T018–T021)
```

Parallel opportunities: T016 (CLI) and T017 (MCP+docs) after T015; the six
strategy edits in T015 are independent of each other.

## Implementation Strategy

- **The regression harness is the spine.** T002–T003 land first and re-run at the
  end of every later phase; nothing merges if long-only drifts (SC-902).
- **MVP = through Phase 4** (US2+US1+US3): a short can be opened, marked, closed,
  and honestly costed/risked — the capability exists and is trustworthy.
- **The payoff = Phase 6**: strategies actually short, and the mode is reachable
  from CLI/MCP — negative edges become actable end to end.
- No DDL, no services beyond the price DB; every task runs on this machine.

## Success Criteria Mapping

SC-901 (short P&L sign/magnitude) → T004–T007 · SC-902 (long-only byte-identical)
→ T002–T003 (+ re-run every phase) · SC-903 (borrow + dividend costs) → T008–T009
· SC-904 (margin/forced-cover) → T010–T011 · SC-905 (metrics under shorts) →
T012–T013 · SC-906 (strategies mode-gated) → T014–T015 · SC-907 (pairs_trading
genuine long-short) → T014–T015
