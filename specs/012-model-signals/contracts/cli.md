# CLI Contract — Model Signals (012)

## `gefion ml predict-backfill`
```
gefion ml predict-backfill --model-name X --model-version V [--end YYYY-MM-DD]
                           [--db-url] [--json]
```
- Refuses: unknown model (names `ml train`); dates ≤ the model's recorded
  cutoff; missing features for a date (that date is reported-skipped, not
  fabricated).
- Resumable: starts at max(prediction_date)+1; idempotent by PK.
- Reports: days predicted, per-day symbol counts, skipped days, elapsed.

## `gefion regime discover start --signal-source model_predictions`
- Requires --signal names from the model-derived namespace; refuses windows
  touching ≤ cutoff (lookahead), coverage < floor, entangled atoms (names the
  colliding model input feature).
- search_space records: signal_source, model name/version, cutoff.

## Derived series (via 011 mode, seeded)
- `macro_model_outlook_q50` — median of per-stock pred_q50 across universe.
- `macro_model_confidence_width` — median of (pred_q90 − pred_q10).
