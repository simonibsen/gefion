# Data Model Sketch

Blending `folly` (MySQL calc_store pattern) with the provided notes and Gefjon’s Postgres schema. This will evolve incrementally with migrations/tests.

## Direct sources
- `stocks`: id, symbol (later: industry/sector refs)
- `stock_history` (Timescale hypertable): OHLCV, pct changes, fundamentals; keyed by data_id + date
- `market_context_history`: date, vix, macro indicators, commodities (AlphaVantage economic endpoints)

## Computed sources (derived from direct)
- `sectors` / `sector_history`: derived aggregates per sector
- `industries` / `industry_history`: derived aggregates per industry

## Data store pattern (folly-style)
- `feature_definitions`: descriptor metadata (fx_name/args, source_t/source_c, store_table, store_column, store_type)
- `computed_features` (Timescale hypertable): tall store keyed by (feature_id, data_id, date, value)
- Future optional: `data_store_<descriptor_id>` materializations if a single feature needs its own table.

## Management/meta
- `data_refresh_schedule`: tracks ingestion + calculation jobs

## Notes for implementation
- Align naming with Postgres (snake_case, `id` as serial/identity, use constraints and indexes mirroring folly’s uniqueness on (descriptor_id, data_id, date)).
- Keep descriptors as metadata to drive computations (analogous to Gefjon’s `feature_definitions` → `computed_features`).
- Start with stocks + stock_history + descriptor/data_store tables; add sector/industry/market context as ingestion work is added.
