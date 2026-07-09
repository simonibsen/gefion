# CLI Contract — Provider-Garbage Detection & Quarantine (008)

All read commands support `--json` and `--db-url`.

## `gefion quality findings`
```
gefion quality findings [--metric beta] [--symbol MDXH | --entity-table macro_series --entity-id 1]
                        [--verdict trash|suspect] [--since 2026-01-01] [--limit 50]
                        [--db-url …] [--json]
```
List findings with rule, observed, expected, verdict, context, resolution state.
Default: unresolved findings, newest first.

## `gefion quality catalog`
```
gefion quality catalog [--json]
```
The validation catalog as loaded: covered metrics (bounds, derivations, entity
kind) AND uncovered numeric columns on validated tables — the coverage gap is
enumerable, never silent.

## `gefion quality backfill`
```
gefion quality backfill [--metric beta] [--entity-table stocks] [--db-url …] [--json]
```
Validate already-stored history through the same catalog + ledger. Idempotent
(re-runs upsert findings, never duplicate); **changes zero stored values** —
summary reports rows examined, findings created/refreshed, per rule. Mutating
only in the ledger sense.

## `gefion quality resolve`
```
gefion quality resolve <finding-id> --reason "catalog bound corrected in …" [--json]
```
Supersede a finding (sets resolved_at/resolution). Never deletes; refuses without
`--reason`.

## `gefion db-health` (extended)
New `data_quality` section: per-metric flagged counts by verdict (unresolved),
warnings on nonzero trash counts — dimension-coverage style.

## Consumer opt-in (uniform flag)
```
gefion cross-sectional-compute … [--include-flagged]
gefion ml dataset-build …        [--include-flagged]
```
Default: trash-convicted values are treated as missing (recorded in the artifact:
`quality_filtering: active`). With `--include-flagged`: verbatim values included,
recorded as `quality_filtering: opted-out`. Suspect findings never affect either
mode (v1).

## Write-path summaries (extended, not new commands)
`fundamentals-update` and `macro ingest` summaries gain `quality_findings: N`
(and never fail because of validation — validation errors are counted and
reported, not raised).

## Errors (honest, non-silent)
- catalog file invalid → refused at load with the offending stanza named
- `quality resolve` without reason → refused
- backfill on an uncataloged metric → refused, names `quality catalog` as the
  coverage listing
