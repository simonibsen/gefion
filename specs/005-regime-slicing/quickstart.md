# Quickstart: Regime Slicing (005)

End-to-end walkthrough once the feature is implemented. Assumes services running
(`/gefion-services start`) and a computed dataset.

## 1. Define a regime (CLI)

```bash
# A market volatility regime: calm / normal / stressed via causal rolling-vol terciles
cat > /tmp/vol_expr.json <<'JSON'
{ "leaf": "comparison", "feature": "realized_vol_20", "cmp": "quantile", "value": "tercile", "scope": "market" }
JSON
gefion regime define --name vol-regime --scope market --expression /tmp/vol_expr.json --json
```

## 2. Compute causal labels + inspect episodes

```bash
gefion regime compute vol-regime --json          # coverage per bucket, mean dwell-time, flicker flag
gefion regime labels  vol-regime --json          # contiguous episodes (not scattered days)
```

Expect: one label per date, `undefined` for the initial lookback window, and episodes — not
day-by-day flicker.

## 3. Slice a backtest by the regime

```bash
gefion backtest run --exchange NASDAQ --limit 100 \
  --start-date 2025-10-01 --end-date 2026-04-02 \
  --strategy ma_crossover --fast-period 10 --slow-period 30 \
  --by-regime vol-regime --json
```

Read the `by_regime` block: per-bucket Sharpe/return/drawdown/trade-count, each with `effective_n`
and a low-power/flicker badge. Confirm `reconciliation_ok: true` (buckets sum to the aggregate).
**Trust only buckets above the effective-sample floor** — low-power buckets are flagged, not findings.

## 4. Ask the gradient question (continuous-interaction)

```bash
gefion regime interaction --signal momentum --by realized_vol_20 --horizon-days 7 --json
# → { interaction_coef, interaction_pvalue, n, effective_n }
```

A significant positive interaction ⇒ the edge scales with volatility; a flat one ⇒ no gradient
(reported honestly, no false signal from bucketing noise).

## 5. Conditional experiment verdict

```bash
gefion experiment run --id <N> --by-regime vol-regime
```

Results show a per-regime holdout p-value; the (experiment × regime × bucket) tests enter the flat
Benjamini-Hochberg family. A regime survives only if it clears FDR; low-power/undefined buckets
fail closed.

## 6. Same three surfaces

- **MCP**: `regime_define`, `regime_compute`, `regime_labels`, `regime_interaction`, and
  `backtest_run`/`experiment_run` with `by_regime` — identical payloads to the CLI `--json`.
- **UI**: the **Regimes** page (define/compute/episodes/interaction), plus per-regime blocks on
  **Backtesting** and a per-regime p-value column + FDR chart on **Experiments**.

## Success signals

- Labels are causal (no lookahead) and form episodes.
- A sliced backtest reconciles to its aggregate; low-power buckets are flagged.
- The interaction test recovers a planted gradient and stays silent on a flat one.
- Conditional verdicts enter one flat BH family; nothing under-powered survives.
