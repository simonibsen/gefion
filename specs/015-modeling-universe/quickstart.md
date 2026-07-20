# Quickstart: Modeling Universe (015)

## See the default universe

```bash
gefion universe list
gefion universe show modeling_default
gefion universe explain AACT          # a SPAC — names the no-shell-companies rule
gefion universe explain AAPL          # member
```

## Verify the cleanup end-to-end (SC-001)

```bash
gefion universe refresh
gefion dataset-build --name check --version v1 --features rsi_14 --export   # default universe
gefion dataset-build --name check-all --version v1 --features rsi_14 --export --universe all  # control
# member counts differ by exactly the excluded set; both datasets record their universe stamp
gefion universe members modeling_default --limit 5
```

## Add a rule without code (SC-002)

```yaml
# rules.yaml — add to existing rules (rules are EXCLUSION predicates:
# matching excludes)
- name: no-penny-stocks
  attribute: close
  op: lt
  value: 1.00
  reason: "Sub-dollar prices distort return statistics"
```

```bash
gefion universe define modeling_default --rules-file rules.yaml
gefion universe refresh          # prints delta; guard refuses outsized shrink
gefion universe show modeling_default   # flap counts for the close rule
```

## Date-aware membership (US3)

```bash
gefion universe members modeling_default --as-of 2015-06-30
gefion universe explain XYZ --as-of 2015-06-30
```

## Prod rollout runbook (FR-013 — one-time vintage change)

1. Deploy + `gefion db-init` (creates tables, seeds `modeling_default`).
2. `gefion universe refresh` — inspect delta (expect ~458 shells + ~1268 ETFs).
3. Recompute derived market series full history: `gefion macro derive --recompute`.
4. Re-derive regime labels conditioned on recomputed series: `gefion regime compute ...`.
5. Re-check the 013 admitted signal via SPA re-verdict machinery; record outcome.
6. Record the vintage change as a system observation (operating ledger).
7. Add `universe refresh` to the nightly cron chain (after feat-compute, before macro derive).
