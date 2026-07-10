# Quickstart — SPA Re-Verdict (010)

End-to-end once implemented.

## 1. Re-verdict an existing run

```bash
gefion regime discover spa second-hunt-prod --json
# verification: 96/96 units reproduce stored p-values (max divergence 3e-12)
# SPA: p_consistent=0.18  [p_lower=0.11, p_upper=0.24]  level=0.01  PASSED
# family_size=96 iterations=1000 seed=<run seed> block_length=6.2
# recorded: spa_reverdicts id=1 (append-only)
```

Same seed → identical p-values on re-run; a new `--seed` appends a new row.

## 2. Where it shows up

```bash
gefion regime discover show second-hunt-prod     # SPA line beside the BH family
gefion regime discover verdicts second-hunt-prod # survivors + SPA pass/fail
gefion regime discover grades                    # admitted edge flagged iff family failed SPA
```

A run never re-verdicted shows `SPA: not yet run` — absence is visible.

## 3. Honest refusals

```bash
# price backfill since the run:
gefion regime discover spa old-run
# REFUSED: reconstruction mismatch — 3 unit(s) diverge beyond tolerance
#   interaction:ab12cd (signal indicator_adx_14): stored p=0.0042, recomputed p=0.0187
# No verdict recorded: this world is not the world the run searched.
```

Also refused: empty family ("nothing to test"), missing price data, outer
window below the observation floor.

## 4. The budget gate

```bash
gefion regime discover start --name big-hunt --budget 500 --atoms atoms.json …
# REFUSED: budget 500 exceeds the v1 cap (200). Raising budgets requires a
# passing SPA re-verdict on the 2 most recent completed runs on this dataset:
#   run 6 'vintage-2020': SPA not yet run   -> gefion regime discover spa 6
#   run 7 'vix-atom-proof': SPA not yet run -> gefion regime discover spa 7
```

After both pass, the same start is accepted and the gate satisfaction is
recorded in the run's pre-registration.

## 5. The negative control (CI)

Seeded noise families: SPA rejects at ≤ the nominal rate (exact binomial
bound); a planted-edge family rejects. Runs in CI beside 006's BH control.

## MCP

`regime_discover_spa` (confirm first — appends one record); `show`/`verdicts`/
`grades` carry the SPA line automatically.
