# UI Contract — Agentic Regime Discovery (006)

`src/gefion/ui/views/regimes.py` gains a **Discovery** tab (the Regimes page is the home of
all regime concerns). Views call the same `regimes.discovery` services as the CLI.

1. **Runs table** — name, status, family size, budget usage, dataset version; row → detail.
2. **New run form** — atoms upload/selection, depth/budget, tier toggles, the three pluggable
   selectors (signal source, grading scheme, universe-filter chain with explicit passthrough
   choice), fresh-holdout picker (required when expressive tier enabled), seed.
3. **Run detail** — pre-registration (immutable display), segregation boundaries,
   **candidate ledger** (filterable by verdict; refused candidates visible — losers are part
   of the story), **diagnostics panel** (sample-dependent vs structural, with quantitative
   reasons), **verdicts** (admitted highlighted; FDR family size shown next to survivors).
4. **Trust-grade timeline** — per admitted regime: forward folds as they accrue (probation =
   fold 1); backward era-slices rendered in a visually distinct "descriptive" style that can
   never be confused with confirmations.
5. **Admitted regimes** appear in the main Regimes list with an `origin=machine` badge and
   full 005 affordances (labels, chart, slice).

Honesty rules in UI: refusals shown with reasons, never hidden; no unadmitted candidate
styled as a finding; empty/error states explicit ("run invalid — segregation unproven").
