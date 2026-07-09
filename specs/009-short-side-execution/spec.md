# Feature Specification: Short-Side Execution for Backtests

**Feature Branch**: `009-short-side-execution`
**Created**: 2026-07-09
**Status**: Draft
**Input**: User description: "Add short-side execution to the backtest engine so gefion can act on the negative-directionality edges it already detects…" (full text in git history; motivated by the 2026-07-09 verification that the backtest layer is structurally long-only)

## The problem in one paragraph

gefion's research core is **direction-symmetric**: edge discovery scores each
observation as `sign(signal − trailing_median) × forward_return`, the
interaction test is two-sided, the trend classifier has explicit
`strong_down`/`weak_down` classes, and quantile regression predicts the downside
`q10`. But the **backtest layer is structurally long-only** — verified: every
strategy emits only `buy`/`sell`; the engine recognizes only `buy`/`sell`
(anything else is dropped); a `sell` is clamped to shares held, so it can only
reduce a long; and the portfolio cannot hold a negative position (no margin, no
short accounting). Even `pairs_trading`, which advertises long-short, emits
"sell if held else buy" — long-short in intent, long-only in execution. The
result: **half of every edge is un-actable.** A signal that correctly predicts a
decline can only move a backtest to flat, never to a profitable short. This
feature makes a short a first-class position so negative predictions become
tradeable, while keeping existing long-only behavior byte-identical by default.

## Core concepts

- **Short as a first-class position.** Opening a short sells borrowed shares:
  the position size goes negative, proceeds are credited, and a borrow liability
  is tracked. The short **gains when the price falls** and loses when it rises.
  Covering buys the shares back to close.
- **The asymmetry of shorts is real and must be modeled.** A long can lose at
  most its cost; a short's loss is **unbounded** (price can rise without limit).
  Shorts also consume **buying power/margin**, accrue a **borrow fee** for every
  day held, and owe **dividends** to the lender while short. These are not
  optional refinements — a short backtest that ignores them overstates returns.
- **Long-only stays the default and the baseline.** Every existing backtest
  runs in `long_only` mode and reproduces byte-identically; short-side behavior
  is opt-in per run (`long_short` mode) and recorded with the result, so a
  strategy's long-only and long-short performance are distinct, comparable
  artifacts.
- **This is execution modeling, not live trading.** It extends the *backtest*
  so validated negative edges can be measured; it deliberately stops at the
  signal/measurement boundary (no broker, no real orders — that stays out of
  gefion's scope).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A short position is opened, marked, and closed correctly (Priority: P1)

As a researcher, when a strategy emits a short in `long_short` mode, the backtest
opens a negative position, marks it to market so it profits as the price falls,
and realizes the correct P&L when covered — the mirror image of a long.

**Why this priority**: this is the foundation; every other story depends on the
portfolio and engine handling a short at all. Without it, nothing downstream is
measurable.

**Independent Test**: seed a price that falls 10%, open a short, cover at the
low → the trade's realized P&L is +10% of notional (minus costs); seed a price
that rises 10% → the short realizes −10%.

**Acceptance Scenarios**:

1. **Given** `long_short` mode and no position, **When** a `short` of N shares
   at price P executes, **Then** the position is −N shares, cash is credited the
   proceeds (net of costs), and equity is unchanged at the instant of the trade.
2. **Given** an open short at P, **When** the price moves to P′, **Then**
   mark-to-market equity reflects `+N × (P − P′)` — up when the price falls.
3. **Given** an open short, **When** a `cover` executes, **Then** the position
   returns toward zero, realized P&L equals proceeds minus buy-back cost minus
   fees, and a closed short round-trip is recorded.
4. **Given** a partial cover, **When** it executes, **Then** the remaining short
   size and average entry are correct and P&L is attributed pro-rata.
5. **Given** `long_only` mode, **When** a strategy emits a `short`, **Then** it
   is ignored (or treated as flatten) exactly as today — no negative position.

---

### User Story 2 - Existing long-only backtests are byte-identical (Priority: P1)

As a researcher, every backtest I have run before this feature produces exactly
the same equity curve, metrics, and trades — short support changes nothing
unless I opt in.

**Why this priority**: a change to the execution core that silently perturbs
historical results would invalidate every prior conclusion. Reproducibility is
the trust anchor.

**Independent Test**: run the full existing backtest test corpus and a set of
recorded reference runs in default mode → equity curves, metrics, and trade
logs match the pre-feature baseline exactly.

**Acceptance Scenarios**:

1. **Given** a strategy and dataset, **When** run in default (`long_only`) mode
   before and after this feature, **Then** the equity curve, metrics, and trades
   are identical.
2. **Given** any CLI/MCP backtest invocation without a mode flag, **When** it
   runs, **Then** it behaves as `long_only` (the safe default).

---

### User Story 3 - Short costs and risk are modeled honestly (Priority: P1)

As a researcher, a short backtest charges borrow fees per day held, debits
dividends owed while short, consumes buying power, and enforces loss/margin
guardrails — so short returns are believable, not inflated.

**Why this priority**: an unbounded-loss instrument with zero holding cost would
make shorting look free and safe, producing dangerously optimistic edges. The
honesty machinery is what makes short backtests worth trusting — consistent with
gefion's whole ethos.

**Independent Test**: hold a short across a borrow-fee window and a dividend
date → equity reflects the accrued borrow fee and the dividend debit; drive a
short's loss past the margin threshold → the guardrail fires (forced cover /
recorded margin event) rather than the equity silently going impossible.

**Acceptance Scenarios**:

1. **Given** a short held D days at borrow rate r, **When** marked, **Then**
   equity is reduced by the accrued borrow fee over those days.
2. **Given** a short open across an ex-dividend date, **When** the dividend
   occurs, **Then** the account is debited the dividend owed to the lender.
3. **Given** buying-power limits, **When** a short would exceed available margin,
   **Then** it is rejected or sized down with the constraint recorded (never
   silently over-leveraged).
4. **Given** a short whose loss breaches the margin/stop threshold, **When**
   marked, **Then** a forced-cover / margin-call event fires and is logged — the
   unbounded-loss guardrail.
5. **Given** short exposure limits (gross/net), **When** a new short would
   breach them, **Then** it is constrained and the limit is reported.

---

### User Story 4 - Metrics are correct under shorts (Priority: P2)

As a researcher, the equity curve, drawdown, returns, win rate, and profit
factor are all correct when a strategy holds shorts — a winning short (price
down) counts as a win, and gross/net/long/short exposure is visible.

**Why this priority**: metrics are how an edge is judged; if a profitable short
shows as a loss or drawdown is miscomputed under negative positions, the verdict
is wrong. Depends on US1's correct P&L.

**Independent Test**: a strategy that only shorts, over a declining market,
reports positive return and positive win rate; the metrics reconcile to the
equity curve; long/short/gross/net exposure series are emitted.

**Acceptance Scenarios**:

1. **Given** a profitable short round-trip, **When** metrics compute, **Then**
   it counts as a win and increases profit factor (a winning short is price-down).
2. **Given** a mixed long-short run, **When** metrics compute, **Then** drawdown
   and returns reconcile to the equity curve, and gross/net/long/short exposure
   are reported.
3. **Given** the no-losses convention, **When** a short-only run has no losing
   trades, **Then** `profit_factor` follows the existing documented convention.

---

### User Story 5 - Strategies act on both directions (Priority: P2)

As a researcher, the built-in strategies emit shorts where their logic implies
one: `mean_reversion` shorts the overbought, `momentum` shorts the losers,
`breakout` shorts downside breakouts (instead of only flattening), `pairs_trading`
becomes genuinely long-short, and `ml_signal`/`ml_filter` short on the
classifier's down-classes / low quantiles.

**Why this priority**: the engine supporting shorts is inert until strategies
use it. This turns the capability into actual bidirectional edges. Depends on
US1–US3.

**Independent Test**: each updated strategy, in `long_short` mode on data that
triggers its bearish branch, opens a short; the same strategy in `long_only`
mode flattens instead — same signal, mode-gated action.

**Acceptance Scenarios**:

1. **Given** `mean_reversion` in `long_short` mode and an overbought signal,
   **When** it runs, **Then** it opens a short (in `long_only` mode it stays
   flat).
2. **Given** `pairs_trading` in `long_short` mode from a flat book, **When** the
   spread is extreme, **Then** it shorts the rich leg and longs the cheap leg
   simultaneously (a real long-short pair, not a held-only sell).
3. **Given** `ml_signal` with a `strong_down` prediction (or low `q10`) in
   `long_short` mode, **When** it runs, **Then** it opens a short.
4. **Given** any updated strategy in `long_only` mode, **When** its bearish
   branch triggers, **Then** it behaves exactly as today (flatten/hold).

---

### Edge Cases

- **Flip long→short in one step**: a strategy wanting to reverse must close the
  long *and* open the short (two legs); the engine must not net them into a
  wrong-sized position.
- **Covering more than the short**: covering more shares than are short must not
  silently flip into a long — it closes the short and (only if intended) opens a
  long, or is clamped, per a defined rule.
- **Borrow unavailable / hard-to-borrow**: v1 assumes borrow is available at the
  configured rate; a "no-locate" modeling refinement is out of scope but the
  assumption must be explicit and the rate overridable.
- **Short of a delisted/halted symbol**: a symbol with no forward price cannot be
  marked; the position is frozen at last mark and the gap is surfaced, not
  silently valued at zero.
- **Dividend on a short with no cash**: the debit can drive equity negative
  (margin event) — handled by the guardrail, not an exception.
- **Long-only reproducibility under refactor**: adding short branches must not
  perturb the long-only path — the regression gate (US2) guards this.
- **Negative equity**: a short can drive account equity below zero (unbounded
  loss); metrics and the equity curve must represent this rather than clamp at 0.

## Requirements *(mandatory)*

### Functional Requirements

**Positions & execution**

- **FR-901**: The portfolio MUST support signed positions (negative = short):
  opening a short credits proceeds (net of costs) and records the short size and
  average entry; covering reduces the short toward zero and realizes P&L.
- **FR-902**: The engine MUST recognize `short` and `cover` actions (in addition
  to `buy`/`sell`) and route them to the signed-position path; unknown actions
  remain no-ops.
- **FR-903**: Mark-to-market MUST value a short as gaining when price falls and
  losing when price rises; account equity MUST reflect open shorts continuously,
  including when it goes negative.
- **FR-904**: Realized and unrealized P&L for shorts MUST be the mirror of longs
  (entry minus current/exit, times size), with partial covers attributed
  pro-rata.

**Costs**

- **FR-905**: A per-day **borrow/locate fee** MUST accrue for every day a short
  is held, at a configurable rate (sensible default, overridable per run), and
  reduce equity.
- **FR-906**: **Dividends owed** to the lender MUST be debited when a held short
  crosses an ex-dividend date (using available dividend data).
- **FR-907**: Existing long-side transaction costs (commission, spread, market
  impact, slippage) MUST apply symmetrically to short entries and covers.

**Risk & margin**

- **FR-908**: Shorts MUST consume buying power/margin; a short that would exceed
  available margin MUST be rejected or sized down, with the constraint recorded
  (never silently over-leveraged).
- **FR-909**: An unbounded-loss guardrail MUST fire a forced-cover / margin-call
  event (logged) when a short's loss breaches the configured margin/stop
  threshold.
- **FR-910**: Gross and net (and long/short) exposure MUST be trackable, with
  configurable exposure limits that constrain new shorts when breached.

**Mode & reproducibility**

- **FR-911**: A per-backtest mode (`long_only` default, `long_short` opt-in) MUST
  gate all short behavior; `long_only` MUST be byte-identical to pre-feature
  behavior.
- **FR-912**: The chosen mode MUST be recorded with the backtest result so
  long-only and long-short runs of the same strategy are distinct, comparable
  artifacts.

**Metrics**

- **FR-913**: Equity curve, drawdown, total/annualized return, win rate, and
  profit factor MUST be correct under short and mixed long-short positions (a
  winning short is price-down), reconciling to the equity curve.
- **FR-914**: Exposure series (gross/net/long/short) MUST be reported for
  long-short runs.

**Strategies**

- **FR-915**: `mean_reversion`, `momentum`, `breakout`, `pairs_trading`,
  `ml_signal`, and `ml_filter` MUST emit `short`/`cover` on their bearish branch
  in `long_short` mode and behave exactly as today in `long_only` mode.

**Interfaces & definition of done** (owner directive; docs-drift enforced)

- **FR-916**: The mode flag and short parameters (borrow rate, margin, exposure
  limits) MUST be reachable via CLI and MCP in the same increment; docs (README,
  USER_GUIDE, MCP_WORKFLOWS, ARCHITECTURE) and learning materials updated in the
  same increment.
- **FR-917**: The short-execution path MUST be observable (spans with position,
  cost, and margin-event attributes).
- **FR-918**: Any schema change (e.g., a column recording a trade's side or a
  run's mode) requires owner-approved DDL; positions themselves are in-memory in
  the backtest and need none.

### Key Entities

- **Signed position**: a symbol's holding with sign (long > 0, short < 0),
  average entry, and — for shorts — accrued borrow and dividend liabilities.
- **Short trade / round-trip**: a short open paired with its cover(s), with
  realized P&L, borrow cost, and dividend debits attributed.
- **Backtest mode**: `long_only` (default) vs `long_short`, recorded per run.
- **Margin/exposure state**: buying power consumed, gross/net/long/short
  exposure, and the limits that constrain new positions.
- **Margin event**: a forced-cover / margin-call record (when, symbol, loss,
  threshold breached).

## Automation *(consider)*

- **Proposed skill**: None needed — the `/gefion` operator skill's backtest
  routing gains the mode flag; no new workflow shape.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-901**: In `long_short` mode, a strategy that shorts a symbol which then
  falls X% realizes approximately +X% of notional (minus modeled costs); the same
  symbol rising X% realizes approximately −X%. Sign and magnitude verified against
  a hand-computed reference.
- **SC-902** (regression gate): 100% of existing backtests and recorded
  reference runs reproduce byte-identically in default (`long_only`) mode — same
  equity curve, metrics, and trades.
- **SC-903**: A short held across a borrow-fee window and an ex-dividend date
  shows equity reduced by the accrued borrow fee and the dividend debit
  (non-zero, matching the configured rate and the dividend amount).
- **SC-904**: A short driven past the margin/stop threshold triggers a logged
  forced-cover/margin-call event rather than an impossible equity value; a short
  exceeding buying power is rejected or sized down with the constraint recorded.
- **SC-905**: A short-only strategy over a declining market reports positive
  return and positive win rate, with metrics reconciling to the equity curve and
  gross/net/long/short exposure reported.
- **SC-906**: Each of the six updated strategies opens a short on its bearish
  branch in `long_short` mode and flattens on the same branch in `long_only`
  mode (same signal, mode-gated action).
- **SC-907**: `pairs_trading` in `long_short` mode establishes a simultaneous
  short-rich/long-cheap pair from a flat book (a genuine long-short pair).

## Assumptions

- **Borrow availability**: v1 assumes shorts can be borrowed at a configurable
  flat annualized rate (sensible default, e.g. low-single-digit %; hard-to-borrow
  / locate modeling is out of scope but the rate is overridable per run).
- **Margin model**: a simple Reg-T-style initial/maintenance margin with a flat
  requirement is sufficient for v1; broker-specific tiered margin is out of scope.
- **Dividend data**: dividends owed while short use the dividend data already in
  the price store; symbols without dividend data accrue none.
- **Mode default**: `long_only` — the safe, reproducible default; short is always
  opt-in.
- **Position sizing**: shorts reuse the existing position-sizing machinery
  (notional/volatility-based); short-specific sizing tweaks are a later refinement.
- **Unbounded loss is represented, not clamped**: equity may go negative; that is
  a modeled outcome (with a guardrail), not an error to suppress.

## Out of Scope

- Live and paper execution, broker/order-router integration — deliberately out of
  gefion's scope (it emits validated signals; it does not trade).
- Options, futures, or other derivatives; short exposure is cash-equity only.
- Portfolio-level optimization or capital allocation beyond per-position sizing.
- Intraday / real-time execution; backtests remain daily-bar.
- Hard-to-borrow/locate availability modeling and broker-specific margin tiers.
- Any change to the research/detection core — it is already bidirectional; this
  feature only makes the *execution* side symmetric.
