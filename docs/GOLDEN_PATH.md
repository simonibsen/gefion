# Golden Path — Clone to First Backtest in ~20 Minutes

This is the end-to-end tour. Follow it once and you'll have:

- Live price data for ~20 NASDAQ symbols (years of daily OHLCV)
- A computed feature set on those symbols
- A trained quantile-regression model predicting 7-day returns
- Predictions evaluated against actuals
- A momentum strategy backtested over a recent year
- Everything visible in the Streamlit UI

If you just want a five-step "is this thing alive" demo, use the Quick Start in [the main README](../README.md#quick-start) and stop after step 5. Come back here when you want to see predictions and backtests.

---

## 0. Prerequisites

You've already done the README Quick Start (install, services up, `gefion init`).

You also need an [AlphaVantage API key](https://www.alphavantage.co/support/#api-key) (free tier works). Put it in `.env`:

```
ALPHAVANTAGE_API_KEY=your_key_here
```

Rough budget: **~20 min wall-clock**. The slowest step is data ingestion (~4–6 min for 20 symbols full history at the free-tier rate limit of ~68 calls/min).

---

## 1. Ingest live data

Pull daily OHLCV for 20 NASDAQ symbols:

```bash
gefion data-update --exchange NASDAQ --limit 20
```

What you'll see: a progress bar and per-symbol status. Most calls take ~1s due to rate limiting. Re-running is cheap — the ingestion is incremental and 91% of subsequent calls are skipped.

When it's done, sanity-check:

```bash
gefion db-health --json | head -20
```

You should see a few thousand rows of `stock_ohlcv` per symbol going back to ~2000.

---

## 2. Compute features

Compute a small but diverse feature set across all ingested symbols:

```bash
gefion feat-compute \
  --features indicator_rsi_14,indicator_macd,indicator_bb_20,indicator_adx_14
```

Without `--symbols`, this runs against every symbol with price data. Takes ~1–2 min for the four indicators × 20 symbols × multi-year history.

List what you've got:

```bash
gefion feat-def-list
```

---

## 3. Build a dataset

A "dataset" in Gefion is a manifest that pins which symbols, which features, and which time range are used for ML. Build one:

```bash
gefion ml dataset-build \
  --name tour \
  --version v1 \
  --exchange NASDAQ \
  --limit 20
```

This registers the dataset in `ml_datasets` and reports row counts. Expect tens of thousands of rows.

---

## 4. Train a model

Train a quantile regression model on the dataset:

```bash
gefion ml train \
  --dataset-name tour --dataset-version v1 \
  --model-name tour --model-version v1
```

Defaults: `quantile_regression` algorithm, 7-day horizon, q10/q50/q90 predictions. Training a quantile regression on 20 symbols completes in ~30–60s on a modern laptop.

Inspect what landed:

```bash
gefion ml model-inspect --model-name tour --model-version v1
```

---

## 5. Predict

Generate predictions for the latest available date:

```bash
gefion ml predict \
  --model-name tour --model-version v1 \
  --exchange NASDAQ --limit 20
```

This writes rows into `predictions` (quantile values + median forecast) for each symbol. You can list them:

```bash
gefion ml predict-list --model-name tour --model-version v1 --limit 10
```

---

## 6. Evaluate

Compare predictions to actuals over a recent window. You need a date range where the prediction horizon has already elapsed — i.e. ≥7 days ago for a 7-day horizon model.

```bash
gefion ml eval \
  --model-name tour --model-version v1 \
  --start-date 2025-01-01 --end-date 2025-03-01
```

Outputs coverage and pinball-loss metrics per quantile. If the 90th percentile actually contains ~90% of actuals, your model is well-calibrated.

> **Gotcha**: if eval reports zero rows, your prediction dates haven't been backfilled. Generate predictions for historical dates first — see the model-inspect output for the prediction date range, or use `gefion ml predict --prediction-date 2025-01-15 ...` to seed historical predictions.

---

## 7. Backtest a strategy

Run a momentum strategy on the same symbols:

```bash
gefion backtest run \
  --exchange NASDAQ --limit 20 \
  --start-date 2024-01-01 --end-date 2024-12-01 \
  --initial-cash 100000 \
  --strategy momentum --top-n 3
```

Outputs portfolio value over time, total return, Sharpe, max drawdown, and trade list. Try `--strategy mean_reversion` or `--strategy ma_crossover` to compare.

Side-by-side comparison:

```bash
gefion backtest compare \
  --strategies momentum,mean_reversion,ma_crossover \
  --exchange NASDAQ --limit 20 \
  --start-date 2024-01-01 --end-date 2024-12-01
```

---

## 8. See it in the UI

```bash
gefion ui
```

Open <http://localhost:8501> and click through:

- **Charts** → Price → pick a symbol you ingested → candlesticks + indicators
- **Charts** → Predictions → see the q10/q50/q90 bands you just generated
- **ML Pipeline** → inspect your `tour` model + its eval metrics
- **Backtesting** → see the run you just executed and its equity curve
- **Dashboard** → high-level system health

---

## 9. Render a chart from the CLI (optional)

If you want a PNG you can email or paste into a doc:

```bash
gefion chart predictions \
  --model-name tour --model-version v1 \
  --symbol AAPL \
  --out aapl_predictions.png
```

---

## Where to next

- **Tune hyperparameters**: `gefion ml tune` (Optuna Bayesian search)
- **Try ensembles**: `gefion ml train-ensemble`
- **Classify instead of regress**: `gefion ml train-classifier` → 5-class trend prediction
- **Run autonomous experiments**: see [README › Autonomous Experiments](../README.md#autonomous-experiments)
- **Full command reference**: [USER_GUIDE.md](USER_GUIDE.md)

---

## Common gotchas

- **Eval reports zero rows** → prediction dates haven't been backfilled (see step 6 note).
- **Training fails with "insufficient data"** → 20 symbols × short history can be borderline. Bump `--limit` to 50 or extend history with another `data-update` run.
- **AlphaVantage 429s** → free tier is ~5 calls/min on burst, ~68/min sustained. Gefion's built-in 1s spacing avoids this; if you hit it, the smart-ingestion skip logic means a retry is cheap.
- **`gefion ui` won't load** → check `docker compose ps postgres` and `gefion health`. Most UI errors trace back to the DB being down.
