# Data Model — Short-Side Execution for Backtests (009)

All in-memory (backtest runtime) — **no database tables**. These are the runtime
structures and the shape of the returned result payload.

## Signed position (extends `Portfolio.positions[symbol]`)

| Field | Meaning |
|---|---|
| `shares` | signed: **> 0 long, < 0 short**, 0 = flat |
| `avg_price` | volume-weighted entry (per side; a short's avg entry is where it was sold) |
| `borrow_accrued` | cumulative borrow fee charged while this short has been open (0 for longs) |
| `opened_at` | date the current position side was opened (for borrow-day counting) |

Invariants: a position never holds long and short simultaneously (flip = close
then open — spec edge case). `calculate_equity` already values `shares × price`
correctly for either sign (research R1).

## Portfolio API additions

- `short(symbol, shares, price, date, costs, …)` — open/increase a short: credit
  proceeds (net of txn costs) to cash, set/append negative shares, record avg
  entry. Refuses if buying power insufficient (or is sized down by the caller).
- `cover(symbol, shares, price, date, costs, …)` — reduce a short toward zero:
  debit buy-back cost, realize `(entry − exit) × covered` P&L, clamp so a cover
  never flips into a long (spec edge case).
- Existing `buy`/`sell` unchanged (long-only semantics preserved — the gate).

## Short round-trip / trade record

Each trade in the result's trade log gains:

| Field | Meaning |
|---|---|
| `side` | `long` \| `short` |
| `action` | `buy` \| `sell` \| `short` \| `cover` |
| `pnl` | realized on close: `(entry − exit) × size` for shorts, `(exit − entry) × size` for longs |
| `borrow_cost` | borrow fee attributed to this short round-trip |
| `dividends_paid` | dividends debited while this short was held |

A **winning short** is `entry > exit` (price fell) → positive `pnl` → counts as a
win in `win_rate`/`profit_factor` (research R6).

## Margin / exposure state (engine, per bar)

| Field | Meaning |
|---|---|
| `buying_power` | cash-derived buying power consumed by open positions (Reg-T) |
| `gross_exposure` | Σ |position notional| / equity |
| `net_exposure` | (long notional − short notional) / equity |
| `long_exposure` / `short_exposure` | per-side notionals / equity |
| limits | `max_gross_exposure`, `max_short_exposure` (configurable; constrain new shorts) |

## Margin event

| Field | Meaning |
|---|---|
| `date`, `symbol` | when/what |
| `loss` | mark-to-market loss that breached maintenance |
| `threshold` | the maintenance requirement breached |
| `action` | `forced_cover` |

Emitted (and logged) when a short's loss erodes equity below maintenance margin
(research R4) — bounds runaway loss without hiding the excursion (research R9).

## Backtest result payload additions

The returned dict (and `--json`) gains, additively (long-only shape unchanged
except these optional fields default to long-only values):

- `mode`: `long_only` (default) | `long_short`
- `exposure`: series of {date, gross, net, long, short} — long_short runs
- `margin_events`: list of margin-event records
- `short_costs`: {borrow_total, dividends_total}
- trade records gain the `side`/`action`/`borrow_cost`/`dividends_paid` fields

## Configuration (run parameters, not schema)

| Param | Default | Meaning |
|---|---|---|
| `mode` | `long_only` | short behavior gate |
| `borrow_rate` | low-single-digit % annualized | per-day short borrow fee |
| `initial_margin` / `maintenance_margin` | 0.50 / 0.25 | Reg-T requirements |
| `max_gross_exposure` / `max_short_exposure` | sensible caps | exposure limits |

Defaults preserve today's behavior when `mode = long_only` (none of the short
params take effect).
