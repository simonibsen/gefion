# Research — Short-Side Execution for Backtests (009)

Decisions resolving the plan's design questions, grounded in the current
`gefion.backtest` code. No NEEDS CLARIFICATION remains.

## R1 — Signed positions vs a separate short book

**Decision**: represent a short as a **negative-shares position** in the existing
`Portfolio.positions` dict, not a parallel short-positions structure.

**Rationale**: `Portfolio.calculate_equity` already marks each position as
`shares × price` — for negative shares this is *already* correct short
mark-to-market (a short's contribution is `−N × price`, which rises as price
falls, exactly right). So the mark-to-market math is reused, not reinvented
(Constitution VI). One position model, signed, keeps long/short symmetric and
metrics uniform.

**Alternatives considered**: a separate `short_positions` dict (rejected —
duplicates equity/metrics logic and invites drift); a positions list with a side
enum (rejected — signed shares is the minimal representation the equity calc
already understands).

## R2 — `short`/`cover` actions vs overloading `buy`/`sell` with signs

**Decision**: add explicit **`short`** and **`cover`** actions to the engine's
`_execute_signal`; leave `buy`/`sell` with today's long-only semantics untouched.

**Rationale**: explicit actions keep the long-only path byte-identical (the
SC-902 gate) — `buy`/`sell` behave exactly as before, so any regression is
structurally impossible on the default path. Strategies express intent directly
("short the rich leg"), and the engine routes: `short` opens/increases a negative
position, `cover` reduces it toward zero. Unknown actions remain no-ops (as
today).

**Alternatives considered**: negative `shares` on `buy`/`sell` (rejected —
perturbs the existing clamp/guard logic on the long path, risking the regression
gate; less readable in strategy code).

## R3 — Borrow fee & dividends: extend `TransactionCosts`, accrue in the daily loop

**Decision**: extend the cost model with a **borrow rate** (annualized, default
low-single-digit %, overridable per run) and **dividend handling**. The borrow
fee **accrues daily** in the engine's per-bar loop for every open short
(`shares × price × rate / 252`), debited from cash. Dividends owed are debited
when a held short crosses an **ex-dividend date**, read from the existing
`stock_ohlcv.dividend_amount` column. Ordinary transaction costs
(commission/spread/impact/slippage) apply symmetrically to `short`/`cover`.

**Rationale**: borrow cost is a *holding* cost, not a *trade* cost — it belongs in
the daily mark loop keyed off open shorts, not in `calculate_cost` at trade time.
Dividend data already exists in the price store (no new source). Symmetric
transaction costs fall out of routing short/cover through the same cost path.

**Alternatives considered**: a separate financing module (rejected — YAGNI at v1;
the accrual is a few lines in the loop); ignoring dividends (rejected — a short
that skips dividend debits overstates returns, violating the honesty principle).

## R4 — Margin / buying-power model

**Decision**: a simple **Reg-T-style** model — initial margin 50%, maintenance
25% (both configurable). A short consumes buying power = proceeds + initial-margin
requirement; a `short` exceeding available buying power is **rejected or sized
down**, recorded. When a short's loss erodes equity below the maintenance
requirement, a **forced-cover / margin-call** event fires (via an extended
`RiskManager`) and is logged.

**Rationale**: Reg-T flat requirements are the standard, defensible default and
enough to make short risk real without broker-specific tiering (out of scope).
Reuses `RiskManager`'s existing stop-loss/position-check seam.

**Alternatives considered**: no margin model (rejected — makes shorts look free
and unbounded-safe, the exact dishonesty the spec forbids); portfolio-margin /
tiered (rejected — broker-specific, out of scope).

## R5 — Mode flag threading and the regression gate

**Decision**: a per-run **`mode`** parameter (`long_only` default, `long_short`)
threaded CLI/MCP → engine. In `long_only`, the engine drops `short`/`cover`
signals (as today an unknown action is dropped) and strategies take only their
long branch — so the default path is unchanged. Mode is recorded in the returned
result payload (no DDL — backtests aren't persisted).

**Rationale**: the mode gate is what makes SC-902 (byte-identical long-only)
structurally guaranteed rather than hoped-for: short code simply never executes
in the default mode. Opt-in and recorded means long-only and long-short runs of
the same strategy are distinct, comparable artifacts.

**Alternatives considered**: always-on shorts with strategies self-gating
(rejected — a single strategy bug could perturb long-only results; the engine-level
gate is the stronger guarantee).

## R6 — Metrics under shorts

**Decision**: trade P&L for a short round-trip is `(entry − exit) × size` (mirror
of the long), so a **winning short is price-down**; `win_rate`/`profit_factor`
count short round-trips with this sign. Equity-curve-derived metrics (drawdown,
returns) already work because `calculate_equity` is signed-correct (R1). Add
**gross / net / long / short exposure** series to the result for long-short runs.

**Rationale**: metrics are the verdict; a profitable short must read as a win.
Because equity is already signed-correct, only the per-trade P&L sign and the new
exposure series need adding — drawdown/returns need no change.

**Alternatives considered**: recomputing drawdown specially for shorts (rejected —
unnecessary; the equity curve already reflects shorts correctly).

## R7 — Strategy mode-gating

**Decision**: each strategy receives the **mode** and emits `short`/`cover` on its
bearish branch **only in `long_short`**; in `long_only` it takes its existing
long/flat branch verbatim. `pairs_trading` in `long_short` shorts the rich leg and
longs the cheap leg simultaneously from flat (a genuine pair); `ml_signal`/
`ml_filter` short on `strong_down` class or low `q10`.

**Rationale**: keeps each strategy's long-only behavior identical (regression
gate) while making the bearish logic actable. The mode is a strategy input, not a
new strategy — minimal surface change.

**Alternatives considered**: separate `*_long_short` strategy variants (rejected —
doubles the strategy count and duplicates logic; a mode param is simpler).

## R8 — No persistence, no DDL

**Decision**: **no schema change.** Backtests compute and return a result payload;
there is no backtest-results table (verified — no such table in `sql/schema.sql`,
no INSERT anywhere). Mode, short trades, exposure, and margin events live in the
returned payload / trade log, consumed by CLI/MCP/UI.

**Rationale**: Schema Governance is a no-op here; nothing to approve. If backtest
*persistence* is ever added (a separate concern), a `mode`/`side` column would go
through the normal approval path then.

## R9 — Unbounded loss represented, not clamped

**Decision**: account equity **may go negative** under an adverse short; the
equity curve and metrics represent this rather than clamping at zero. The
forced-cover guardrail (R4) fires at the maintenance threshold to bound runaway
loss, but the modeled outcome (including a negative excursion) is shown, not
suppressed.

**Rationale**: clamping would hide the very risk shorts carry — the honesty
principle. The guardrail is risk management; the representation is truth-telling.
Both coexist: the guardrail limits how far it runs, the metrics show what happened.

**Alternatives considered**: clamp equity at 0 (rejected — dishonest, hides
blow-up risk); no guardrail, pure representation (rejected — a runaway short with
no margin call is also unrealistic; brokers force-cover).
