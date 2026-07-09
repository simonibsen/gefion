# CLI Contract — Short-Side Execution (009)

Additive to `gefion backtest run` / `backtest compare`. All existing invocations
behave identically (default `long_only`); short params take effect only in
`long_short`.

## `gefion backtest run` (extended)
```
gefion backtest run --strategy <name> … \
    [--mode long_only|long_short]          # default long_only
    [--borrow-rate 0.03]                   # annualized short borrow fee
    [--initial-margin 0.50] [--maintenance-margin 0.25]
    [--max-gross-exposure 1.5] [--max-short-exposure 1.0]
    [--json]
```
- `--mode long_only` (default): byte-identical to today; short params ignored.
- `--mode long_short`: strategies may open shorts; borrow/dividend/margin
  modeling active.
- Result payload (and `--json`) gains `mode`, and for long_short:
  `exposure`, `margin_events`, `short_costs`, and `side`/`action`/`borrow_cost`/
  `dividends_paid` on trades.

## `gefion backtest compare` (extended)
```
gefion backtest compare … [--mode long_only|long_short]
```
Compare strategies within a mode; comparing a strategy's long_only vs long_short
is two runs (recorded mode distinguishes them).

## Honest behavior
- A `long_short` short that would exceed buying power / exposure limits is sized
  down or rejected, and the constraint is reported in the result (never silently
  over-leveraged).
- A short breaching maintenance margin produces a logged `forced_cover`
  margin-event in the result — not an impossible equity value.
- Equity may go negative on an adverse short (represented, not clamped).

## Errors
- `--mode long_short` with a strategy that has no bearish branch → runs, emits no
  shorts (not an error; reported as zero short trades).
- invalid `--borrow-rate`/margin (negative) → refused with the offending value.
