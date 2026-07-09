# Data Model — Provider-Garbage Detection & Quarantine (008)

## New table: `data_quality_findings` (audit ledger)

One row per detection. Append-only in spirit: detection facts are immutable;
only the resolution fields may be set later (a superseding amendment, never an
erasure). Full DDL in [contracts/sql.md](contracts/sql.md) — **proposed, awaiting
owner approval**.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `entity_table` | TEXT NOT NULL | 007 declared axis (`stocks`, `macro_series`) |
| `entity_id` | INTEGER NOT NULL | id in the declared table — deliberately **no FK** (ledger survives entity deletion; FR-307) |
| `metric` | TEXT NOT NULL | catalog metric name (e.g. `beta`, `dividend_yield`, `vix`) |
| `date` | DATE NOT NULL | the observation date the value belongs to |
| `rule` | TEXT NOT NULL | `definitional_bound` \| `cross_field` \| `temporal_spike` \| `cross_sectional_outlier` |
| `verdict` | TEXT NOT NULL | CHECK `('trash','suspect')` — only tiers 1–2 may write `trash` (enforced in code; FR-304) |
| `observed` | DOUBLE PRECISION | what the provider said (float8: garbage is unbounded by definition) |
| `expected` | DOUBLE PRECISION NULL | recomputed/bound value where applicable |
| `detail` | JSONB NULL | rule-specific context (inputs used, tolerance, neighbors, z) — `Json()` adapter |
| `context` | TEXT NULL | detecting command/run (e.g. `fundamentals-update 2026-07-12`, `quality backfill`) |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| `resolved_at` | TIMESTAMPTZ NULL | set only by an explicit resolution |
| `resolution` | TEXT NULL | why the finding was superseded (e.g. catalog bound corrected) |

**Constraints & indexes**
- `UNIQUE (entity_table, entity_id, metric, date, rule)` — idempotence by
  construction (FR-306); re-validation upserts, never duplicates.
- Index `(metric, verdict)` — db-health counts and findings listing.
- Index `(entity_table, entity_id)` — per-entity lookups and consumer joins.

**Deletion story (007 governance)**: no ON DELETE behavior anywhere — nothing
references the ledger and the ledger references nothing. `entity-delete` does not
touch it (audit exception, issue #76). Layer: audit/ops — outside the feeds graph
like the discovery ledgers; `TABLE_PURPOSE` entry + dictionary regen required.

## Configuration: `data-quality/catalog.yaml`

Repo-versioned; the authoritative statement of what is validated and why.

```yaml
defaults:
  tolerance_factor: 10        # tier 2: convict beyond this observed/recomputed ratio
  spike_factor: 100           # tier 3: neighbor-magnitude multiple that marks a spike
  robust_z_threshold: 10      # tier 4: |v - median| / (1.4826 * MAD)

metrics:
  beta:
    entity_table: stocks
    table: stocks_fundamentals
    column: beta
    bounds: {min: -50, max: 50}
    why: >
      Beta is a regression slope of asset vs market returns; empirically |beta|
      rarely exceeds 5, never ~50, for anything traded. Observed garbage: -503341.44.
  dividend_yield:
    entity_table: stocks
    table: stocks_fundamentals
    column: dividend_yield
    bounds: {min: 0, max: 2.0}     # 200% — generous even for imminent delisting
    derivation:
      expression: dividend_per_share / close
      inputs: [overview.DividendPerShare, stock_ohlcv.close]
    why: Yield is dividend/price. Observed garbage: 1000000.0.
  pe_ratio:
    entity_table: stocks
    table: stocks_fundamentals
    column: pe_ratio
    bounds: {min: -10000, max: 10000}
    derivation:
      expression: close / eps
      inputs: [stock_ohlcv.close, stocks_fundamentals.eps]
    why: Loose — near-zero EPS legitimately explodes PE; bounds catch only nonsense.
  # …remaining fundamentals metrics (forward_pe, peg_ratio, book_value, eps,
  # revenue_per_share, profit_margin, operating_margin, return_on_equity,
  # ev_to_ebitda, market_cap via shares×close) — margins/ROE with deliberately
  # loose envelopes (near-zero denominators are real)…
  vix:
    entity_table: macro_series
    table: macro_series_values
    column: value
    series: vix
    bounds: {min: 0.01, max: 200}
    why: A volatility index is strictly positive; all-time high ~90 (2008).

universe:
  test_tickers: [ZVZZT, ZWZZT, ZXZZT, ZJZZT, ZBZX, ZTEST]   # configuration, not code
  selectors:
    asset_type_common: "asset_type = 'Common Stock'"         # fail-closed on NULL
```

**Validation of the catalog itself**: loader rejects unknown keys, non-numeric
bounds, metrics naming nonexistent table/column pairs; `quality catalog` lists
covered metrics AND uncovered numeric columns on validated tables (edge case:
enumerable gaps, no coverage illusion).

## Verdict semantics (state, not stored per value)

A value's effective status is derived at read time by consumers:
- **convicted** — an unresolved `trash` finding exists for (entity, metric, date)
  → treated as missing by default (FR-308), included under `--include-flagged`.
- **suspect** — only suspect findings exist → visible in ledger/db-health, never
  excluded (FR-310).
- **clean** — no unresolved findings.
- **resolved** — a finding with `resolved_at` set no longer affects consumers;
  the row remains as history.

## Relationships

- `(entity_table, entity_id)` — logical (declared) reference into `stocks` /
  `macro_series` (007 model; validated at write, audited by db-health).
- `metric` → catalog stanza (configuration join, not SQL).
- Consumers anti-join their reads against unresolved trash findings via the
  catalog's metric→(table, column) mapping (research R3).
