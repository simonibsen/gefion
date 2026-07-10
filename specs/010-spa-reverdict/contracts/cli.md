# CLI Contract — SPA Re-Verdict (010)

All read commands support `--json` and `--db-url`.

## `gefion regime discover spa`
```
gefion regime discover spa <run-name-or-id>
    [--iterations 1000]      # bootstrap B
    [--seed N]               # default: the run's own stored seed
    [--level 0.01]           # pass/fail level; default: the run's FDR rate
    [--block-length X]       # expert override of the automatic choice
    [--db-url …] [--json]
```
Reconstructs the counted family from the ledger + pre-registration, verifies
(recomputed per-unit p-values must reproduce stored ones), runs the joint
stationary bootstrap, records one append-only `spa_reverdicts` row, and prints:
p_consistent (the verdict) with p_lower/p_upper, pass/fail at the level,
family size, iterations, seed, block length, and the verification summary.

**Honest refusals** (no row recorded): reconstruction mismatch (names the
divergent units and magnitudes); missing price data (names what's missing);
family_size 0 / no counted candidates ("nothing to test"); outer window below
the observation floor (floor named).

## `gefion regime discover show` / `verdicts` (extended)
The latest SPA result appears beside the BH family — p_consistent, level,
pass/fail, when — or explicitly `SPA: not yet run`.

## `gefion regime discover grades` (extended)
An admitted edge whose run's latest SPA fails carries a loud
`family failed selection-aware check (SPA p=…)` flag. The BH verdict and the
trust grade are unchanged — no auto-demotion.

## `gefion regime discover start` (gate)
Configurations with `--budget > 200` or `--depth > 2` (the named v1 caps) are
refused unless the most recent 2 completed runs on the same dataset version
each carry a passing latest SPA re-verdict. The refusal names the gate and the
satisfying command. On satisfaction, `{gate: "spa", runs, reverdict_ids}` is
recorded in the new run's pre-registration. Within-cap starts are unchanged.
