# UI Contract — Regime Slicing (005)

Streamlit views live in `src/gefion/ui/views/`; pages are registered in `src/gefion/ui/app.py`.
UI views call the same `src/gefion/regimes/*` service functions the CLI uses (no logic
re-implementation). "Ask Gefion" contextual chat is available on every page per existing pattern.

## New view: `src/gefion/ui/views/regimes.py` → `render_regimes()`

Registered as a new **Regimes** page in `app.py`. Sections:

1. **Regime list** — table of definitions (name, scope, status, label coverage, mean dwell,
   flicker flag). Row → detail drawer.
2. **New / edit regime** — an AST builder form (add comparison / reference / detector-function
   leaves; combine with AND/OR/NOT; set scope, bucketing, optional min-dwell). Validates before
   save; shows unresolved-feature-ref errors inline. Calls the same define/validate service as CLI.
3. **Detail drawer** — the AST (rendered readably), bucketing, persistence, dataset provenance,
   and the three descriptive-metadata layers; **Compute** action.
4. **Labels / coverage** — after compute: an **episode timeline** (contiguous regime episodes,
   not scattered days), a **bucket-frequency** bar chart, and dwell-time; `undefined` periods
   shown distinctly.
5. **Continuous-interaction panel** — pick a signal + conditioning variable → interaction
   coefficient, p-value, effective-N; renders "no significant gradient" honestly when flat.

## Extended view: `src/gefion/ui/views/backtest.py`

- Add a **"Slice by regime"** selector to the backtest form. When set, the results area renders
  **per-regime metric blocks** (return, Sharpe, drawdown, win rate, trade count) each annotated
  with sample size, effective-N, and a **low-power/flicker badge**; a reconciliation indicator
  confirms buckets sum to the aggregate. Unset → unchanged view.

## Extended view: `src/gefion/ui/views/experiments.py`

- Conditional experiment results gain a **per-regime holdout p-value column** and an **FDR chart**
  showing which (experiment × regime × bucket) tests entered the flat BH family and which survived;
  low-power/undefined buckets shown as "no verdict (fail-closed)".

## Cross-cutting UI rules

- **Empty/loading/error states**: "no regimes yet", "not computed yet", "regime uncomputable —
  missing feature refs", and "reconciliation failed" each have explicit UI states.
- **Parity**: every value shown is available identically via CLI `--json` / MCP (interfaces.md).
- **Low-power honesty**: the UI MUST visually distinguish a real finding from a low-power/withheld
  bucket — never render a withheld bucket's metrics as if trustworthy.
