# Contract: Composite Market Function (macro-of-macro)

## Function contract

```python
def compute(row):
    # row: dict for ONE trading date — {"<series_name>": float, ...}
    #      containing exactly the declared input series, all present
    #      (a date missing ANY declared input never reaches the body)
    # Return: float (the composite's value for that date) or None (gap)
```

- Executed in the standard sandbox (011): whitelist unchanged, forbidden
  import → refusal, non-numeric/bool return → error, NaN/inf → gap.
- The body sees one date at a time; it has no access to history, other
  dates, or anything outside `row` — causality is inherited from the stored
  inputs by construction.

## Declaration

`feature_functions` row with `scope='market'` and
`inputs = {"series": ["vix", "breadth_sma200", "dispersion_20"]}`.
The input shape (`series` vs `features`) is the executor discriminator —
no new scope value.

## Validation (registration AND promotion)

- Every declared series exists in `macro_series` and is enabled — unknown or
  disabled input refuses loudly, naming the series.
- `series` must be non-empty.
- Cycle refusal: DFS over (output series name → declared inputs, recursing
  through composite-produced series) — a cycle refuses at the door, never
  detected at run time.

## Execution semantics (`run_composite_function`)

- One query pivots the declared input series' stored values per date
  (~6.7k dates × few series — trivial memory).
- A date where any declared input has no stored value is a **gap**: no row
  reaches the body, no value is written. Never imputed.
- Incremental derive computes only dates missing from the output series;
  rerun writes nothing (idempotent); `--full` recomputes history (the
  recovery door after an input series is re-derived).
- Failure isolation: a raising body writes nothing for the whole run and
  reports (exit 2 parity with 011).
- A disabled input series makes the composite a **reported skip** (silence
  never reads as health), distinct from per-date gaps.

## Derive ordering

`macro derive --series all` runs non-composite market functions first, then
composites in topological order of the dependency graph — same-night inputs
are fresh before any composite reads them.
