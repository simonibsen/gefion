# Quickstart — Sector-State Signals (013)

```bash
# 1. Seed generated bodies for every sector with >= 100 members
gefion macro seed-sectors --json

# 2. Compute all derived series (sector + breadth + dispersion + model)
gefion macro derive --series all --json

# 3. Inspect one series
psql "$DATABASE_URL" -c "
  SELECT cf.date, cf.value FROM computed_features cf
  JOIN feature_definitions fd ON fd.id = cf.feature_id
  WHERE fd.name = 'macro_sector_rs_technology'
  ORDER BY cf.date DESC LIMIT 5"

# 4. Hunt on sector states (atoms file lists macro_sector_* terciles)
gefion regime discover start --name sector-hunt-1 --atoms sector_atoms.json \
  --tier interaction --tier grammar --horizon-days 20 --holdout-weeks 80 \
  --seed 60 --dataset prod
```

Honesty notes: thin (sector, date) days are gaps; NULL-sector stocks are in
the market baseline but no sector; current sector metadata labels the past
(membership-vintage caveat — see REGIMES.md).
