# Market Function Body Contract (011)

A market-scope `function_body` MUST define:

```python
def compute(rows):
    """rows: list[dict] for ONE trading date.
    Keys: symbol, close, high, low, volume, plus every feature named in
    the registry row's inputs.features (missing per-stock values -> key
    absent from that stock's dict).
    Return: float (the day's series value) or None (no value for this day).
    NaN/inf are treated as None. Anything else is a failure."""
```

- Executed inside the standard sandbox: whitelisted imports only (numpy,
  pandas, math, statistics, ...), no filesystem, no network, no exec/eval.
- The executor guarantees: rows only for dates in the requested range;
  cross-sections thinner than min_stocks are never passed (the day is a gap
  by policy, not by the body's choice).
- Purity expectation: same rows -> same value (no state between dates).

## Seed bodies (v1)

- `breadth_sma200`: % of rows with close > indicator_sma_200.
- `dispersion_20`: population std of per-stock 20-day returns — supplied to
  the body via a declared input feature (see research R3/R7; the 20-day
  return column is computed in the streaming query from close vs LAG(close,20)
  and exposed to rows as `ret_20`).
