# Internal Interfaces — Short-Side Execution (009)

The in-code contracts the implementation must honor (no external API/DDL).

## Engine action set (`engine._execute_signal`)

| action | effect |
|---|---|
| `buy` | **unchanged** — open/increase long |
| `sell` | **unchanged** — reduce long, clamped to holdings |
| `short` | open/increase a short (negative position); only in `long_short` mode |
| `cover` | reduce a short toward zero; only in `long_short` mode; never flips to long |
| other | no-op (unchanged) |

In `long_only` mode, `short`/`cover` are dropped (like any unrecognized action
today) — the reproducibility guarantee.

## Portfolio (signed positions)

- `short(symbol, shares, price, date, costs, daily_volume=None) -> trade|None`
- `cover(symbol, shares, price, date, costs, daily_volume=None) -> trade|None`
- `buy`/`sell` — unchanged signatures and behavior.
- `calculate_equity(prices)` — unchanged; already correct for signed shares.

## Cost hooks (`costs.py`)

- `TransactionCosts.calculate_cost(...)` — unchanged; applies to short/cover too.
- New: a borrow-rate config + `accrue_borrow(position, price, days) -> float`
  used by the engine's daily loop; dividend debit driven by
  `stock_ohlcv.dividend_amount` on ex-div dates.

## Risk hooks (`risk.py`)

- `RiskManager` gains buying-power / margin checks and `max_short_exposure` /
  `max_gross_exposure`; emits `forced_cover` exit signals at maintenance breach
  (reusing the existing exit-signal seam the engine already consumes first each
  bar).

## Metrics (`metrics.py`)

- Per-trade `pnl`: `(exit − entry) × size` (long) / `(entry − exit) × size`
  (short); winning short = price-down.
- `win_rate`/`profit_factor` count short round-trips with the above sign; the
  no-losses `profit_factor = 0` convention is unchanged.
- New `exposure` series (gross/net/long/short) in the result.

## Strategy signature

Strategies receive the run `mode`; on their bearish branch they emit
`{action: "short"|"cover", symbol, shares}` in `long_short` and their existing
long/flat signals in `long_only`. No strategy changes behavior in `long_only`.
