# Quickstart: First-Class Entities (007)

Assumes the DDL has been owner-approved and migrated (increments 1–4 landed).

## 1. Ingest VIX (the proving case)

```bash
gefion macro ingest --name vix --kind index --cadence daily --full --json
gefion macro list --json          # coverage: first/last date, rows, materialized ✓
```

One catalog row + decades of daily values + the `macro_vix` feature materialized
into the feature store — `stocks` untouched, zero equity-pipeline changes.

## 2. It's just a feature now

```bash
gefion regime interaction --signal indicator_macd --by macro_vix      # gradient
# discovery: add {"feature": "macro_vix", "form": "tercile"} to atoms.json —
# VIX-seeded principles stop logging uncomputable_proposal diagnostics
gefion regime discover start --name vix-hunt --atoms atoms.json …
```

## 3. Trust, verified

```bash
gefion db-health --json | jq '.entity_integrity'   # orphan scan: expect zeros
```

## 4. First-class deletion

```bash
gefion data entity-delete macro_series vix          # dry-run: full blast radius
gefion data entity-delete macro_series vix --confirm
gefion data entity-delete stocks ZVZZT              # works uniformly for stocks
```

## 5. The family test (SC-207)

Adding the second series is configuration, not engineering:

```bash
gefion macro ingest --name cpi --provider alphavantage:CPI --kind rate --cadence monthly --full
```

One catalog row, one ingest call, one materialized feature. Zero DDL. If this step
ever needs a schema change, the design has failed its own test.
