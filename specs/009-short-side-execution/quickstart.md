# Quickstart — Short-Side Execution (009)

End-to-end once implemented. Backtests are local compute — no services beyond the
DB for price/dividend data.

## 1. Long-only is unchanged (the default)

```bash
gefion backtest run --strategy mean_reversion --exchange NASDAQ --limit 50 \
    --start-date 2025-01-01 --end-date 2025-12-31 --json
# mode: long_only — identical equity curve, metrics, trades to before 009
```

## 2. Turn on shorts

```bash
gefion backtest run --strategy mean_reversion --mode long_short \
    --borrow-rate 0.03 --exchange NASDAQ --limit 50 \
    --start-date 2025-01-01 --end-date 2025-12-31 --json
# now the overbought names are shorted, not just avoided
# result gains: mode=long_short, exposure[], margin_events[], short_costs{}
# trades carry side/action/borrow_cost/dividends_paid
```

## 3. A winning short reads as a win

A short opened at 100 and covered at 90 shows `pnl > 0` (price fell 10%), counts
toward `win_rate` and `profit_factor` — a profitable short is not a loss.

## 4. Shorts aren't free

Hold a short across weeks and a dividend date, and the result's `short_costs`
shows a non-zero `borrow_total` (per-day fee) and `dividends_total` (owed to the
lender). Compare `long_short` vs `long_only` returns for the same strategy to see
what the short side actually adds after costs.

## 5. Risk is enforced

- A short that would exceed `--max-gross-exposure` / buying power is sized down or
  rejected — reported, never silently over-leveraged.
- A short that runs against you past maintenance margin produces a logged
  `forced_cover` margin-event; equity may dip negative (represented, not clamped)
  before the cover.

## 6. pairs_trading is genuinely long-short

```bash
gefion backtest run --strategy pairs_trading --mode long_short …
# from a flat book, an extreme spread shorts the rich leg AND longs the cheap leg
# simultaneously — a real pair, not the old "sell only what you hold"
```

## 7. Compare the two modes

```bash
gefion backtest run --strategy momentum --mode long_only  … --json > lo.json
gefion backtest run --strategy momentum --mode long_short … --json > ls.json
# the recorded `mode` distinguishes the artifacts; compare return/Sharpe/exposure
```

## MCP

`backtest_run` with `mode="long_short"` and optional `borrow_rate`/margin/exposure
args; the result carries the same additive fields. Always surface margin events
and short costs alongside a short's return.
