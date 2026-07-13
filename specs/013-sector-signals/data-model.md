# Data Model — Sector-State Signals (013)

ZERO DDL. Existing surfaces only:

- `feature_functions` (scope='market'): ≤ ~30 generated rows
  `sector_rs_<slug>` / `sector_breadth_<slug>`, inputs
  `{"features": ["ret_20"]}` / `{"features": ["indicator_sma_200"]}`,
  body carries MIN_MEMBERS and the sector string; seeded create-if-absent
  (DB wins after).
- `macro_series` (kind='derived'): one row per function (existing
  `catalog.ensure_series` path via derive).
- `feature_definitions` + `computed_features`: one `macro_<fn>` feature per
  series, values keyed to the macro_series entity — the standard derived
  mold, gaps preserved.
- `stocks.sector`: read-only input (census + stream column).

Validation rules: slug collisions refuse at seeding; census floor at
seeding; MIN_MEMBERS floor at compute (gap, not value); NULL sector
excluded from member sets, included in ALL-rows market baseline.
