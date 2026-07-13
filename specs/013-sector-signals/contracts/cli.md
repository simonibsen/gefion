# CLI Contract — Sector Signals (013)

## `gefion macro seed-sectors`
```
gefion macro seed-sectors [--sectors "TECHNOLOGY,HEALTHCARE"] [--min-members 100]
                          [--db-url] [--json]
```
- Census from `stocks.sector` (asset_type='Stock', NULL excluded).
- Default: seed every sector meeting `--min-members`; reports seeded /
  already-present / skipped-thin (with counts).
- `--sectors` restricts to named sectors; unknown names refuse listing the
  known census (honest error).
- Slug collisions (two sectors → one slug) refuse loudly.
- Create-if-absent only: never overwrites an edited DB body.

## `gefion macro derive --series all` (semantics change)
- 'all' now expands to SEED_BODIES ∪ enabled scope='market' DB functions
  (sector + model + future series); disabled functions skipped-and-reported.

## The sector hunt (task #48, existing door)
```
gefion regime discover start --name sector-hunt-1 --atoms sector_atoms.json \
  --tier interaction --tier grammar --horizon-days 20 --holdout-weeks 80 \
  --seed <declared> --dataset <tag>
```
- Atoms: `macro_sector_rs_<slug>` / `macro_sector_breadth_<slug>` terciles
  (top-6 census sectors) + proven market vocabulary. No discovery code
  changes — the SC-1303 e2e test is the proof.
