# Quickstart — Provider-Garbage Detection & Quarantine (008)

End-to-end walkthrough once implemented. Assumes services running
(`/gefion-services start`) and the 008 migration applied.

## 1. See what is validated (and what isn't)

```bash
gefion quality catalog
# metrics: beta [stocks_fundamentals.beta] bounds [-50, 50] + no derivation
#          dividend_yield [...] bounds [0, 2] + derivation dps/close (tol 10x)
#          vix [macro_series_values.value, series=vix] bounds [0.01, 200]
#          ...
# uncovered numeric columns: stocks_fundamentals.shares_outstanding, ...
```

## 2. Flag the garbage already in the database

```bash
gefion quality backfill --json
# {"rows_examined": ..., "findings": {"created": N, "refreshed": M},
#  "by_rule": {"definitional_bound": ..., "cross_field": ...},
#  "stored_values_changed": 0}
```

The issue-79 quartet convicts immediately:

```bash
gefion quality findings --metric beta
# MDXH 2026-07-08  beta  definitional_bound  trash  observed=-503341.44  expected=[-50, 50]
# ELOX 2026-07-08  beta  definitional_bound  trash  observed=-165013.73  expected=[-50, 50]
gefion quality findings --symbol CTAA
# CTAA 2026-07-08  dividend_yield  cross_field  trash  observed=1000000.0  expected≈0.02
```

A shell company's real ROE of −615% produces **no** finding — run
`gefion quality findings --metric return_on_equity` and see only actual trash.

## 3. Quality at a glance

```bash
gefion db-health
# data_quality: {beta: {trash: 2}, dividend_yield: {trash: 1}, ...}
# WARNING: 4 unresolved trash finding(s) across 3 metric(s) — see gefion quality findings
```

## 4. Research is quarantined by default

```bash
# Cross-sectional rankings: convicted values are simply missing
gefion cross-sectional-compute --feature beta_percentile ...
# -> MDXH has no beta rank that day; the universe median is unpolluted

# Dataset build: filtering recorded in the manifest
gefion ml dataset-build --name clean_v1 ...
# manifest.json: "quality_filtering": "active"

# Explicit opt-in when you really want the verbatim data
gefion ml dataset-build --name raw_v1 --include-flagged ...
# manifest.json: "quality_filtering": "opted-out"
```

## 5. Junk instruments are out of every research universe

```bash
gefion cross-sectional-compute ...      # ZVZZT & friends: never in the peer group
gefion regime discover start --universe-filter "test_tickers,asset_type:common" ...
# same declared vocabulary, same single source of truth (catalog universe: block)
```

## 6. The write paths validate as they go

```bash
gefion fundamentals-update
# ... updated: 6100, write_errors: 0, quality_findings: 2
gefion macro ingest --name vix
# quality_findings: 0   (a VIX of -3 would be convicted here)
```

## 7. Corrections supersede, never erase

```bash
gefion quality resolve 17 --reason "beta bound widened after review of leveraged ETNs"
# finding 17: resolved (row retained); consumers stop excluding that value
```

## 8. The family test (SC-306)

Add bounds for a new metric — one YAML stanza in `data-quality/catalog.yaml`,
then `gefion quality backfill --metric <name>`. Zero code, zero schema.

## MCP equivalents

`quality_findings`, `quality_catalog`, `quality_backfill` (confirm first),
`quality_resolve` (confirm first); db-health surfaces carry the `data_quality`
section automatically.
